import time
from pathlib import Path
from unittest.mock import patch

from aarkib.extensions import db
from aarkib.models import Book, JobRecord, Library, User
from aarkib.services.enricher import EnrichedMetadata
from aarkib.services.job_manager import JobManager, JobStatus
from aarkib.services.scanner import index_single_book, scan_library


def _login_admin(client, app):
    with app.app_context():
        admin = User(username="admin_jm", is_admin=True)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()
    client.post(
        "/auth/login",
        data={"username": "admin_jm", "password": "adminpass"},
        follow_redirects=True,
    )


def test_job_manager_lifecycle():
    jm = JobManager(max_workers=2)

    def simple_worker(progress_callback=None):
        if progress_callback:
            progress_callback(50.0, "Halfway done")
        time.sleep(0.05)
        return {"items_processed": 42}

    job = jm.submit_job("test_worker", simple_worker)
    assert job.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    # Wait for completion
    timeout = 3.0
    start = time.time()
    while job.status != JobStatus.COMPLETED and time.time() - start < timeout:
        time.sleep(0.02)

    assert job.status == JobStatus.COMPLETED
    assert job.result == {"items_processed": 42}
    assert job.progress == 100.0
    assert job.completed_at is not None

    # Test get_job and list_jobs
    fetched = jm.get_job(job.id)
    assert fetched is not None
    assert fetched.id == job.id

    all_jobs = jm.list_jobs()
    assert any(j.id == job.id for j in all_jobs)
    jm.shutdown(wait=True)


def test_job_manager_failure():
    jm = JobManager(max_workers=1)

    def failing_worker(progress_callback=None):
        raise ValueError("Simulated task failure")

    job = jm.submit_job("failing_worker", failing_worker)

    timeout = 3.0
    start = time.time()
    while job.status != JobStatus.FAILED and time.time() - start < timeout:
        time.sleep(0.02)

    assert job.status == JobStatus.FAILED
    assert "Simulated task failure" in (job.error or "")
    jm.shutdown(wait=True)


def test_job_manager_deduplication():
    jm = JobManager(max_workers=2)
    gate = False

    def blocking_worker(progress_callback=None):
        while not gate:
            time.sleep(0.01)
        return "done"

    job1 = jm.submit_job("dup_task", blocking_worker, deduplicate=True)
    job2 = jm.submit_job("dup_task", blocking_worker, deduplicate=True)

    assert job1.id == job2.id

    gate = True
    timeout = 3.0
    start = time.time()
    while job1.status != JobStatus.COMPLETED and time.time() - start < timeout:
        time.sleep(0.02)

    assert job1.status == JobStatus.COMPLETED
    jm.shutdown(wait=True)


def test_job_manager_cleanup():
    jm = JobManager(max_workers=1)

    def quick_task(progress_callback=None):
        return 1

    job = jm.submit_job("quick", quick_task)
    timeout = 3.0
    start = time.time()
    while job.status != JobStatus.COMPLETED and time.time() - start < timeout:
        time.sleep(0.02)

    # Setting max_age_seconds=0 should clean up completed job
    jm.cleanup_old_jobs(max_age_seconds=0)
    assert jm.get_job(job.id) is None
    jm.shutdown(wait=True)


def test_api_async_scan_and_jobs(client, app, tmp_path, sample_epub):
    import shutil

    _login_admin(client, app)

    # Put a book into default library dir
    books_dir = Path(app.config["LIBRARY_DIR"])
    books_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(sample_epub, books_dir / "async_test.epub")

    # 1. Trigger async full scan -> 202 Accepted
    res = client.post("/api/libraries/scan")
    assert res.status_code == 202
    data = res.get_json()
    assert data["status"] == "accepted"
    job_id = data["job_id"]
    assert job_id is not None
    assert f"/api/jobs/{job_id}" in data["job_url"]

    # 2. Poll job status
    timeout = 5.0
    start = time.time()
    job_data = {}
    while time.time() - start < timeout:
        status_res = client.get(f"/api/jobs/{job_id}")
        assert status_res.status_code == 200
        job_data = status_res.get_json()
        if job_data["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)

    assert job_data["status"] == "completed"
    assert "scanned" in job_data["result"]

    # 3. List all jobs
    list_res = client.get("/api/jobs")
    assert list_res.status_code == 200
    jobs_list = list_res.get_json()["jobs"]
    assert any(j["id"] == job_id for j in jobs_list)

    # 4. GET invalid job returns 404
    missing_res = client.get("/api/jobs/non-existent-uuid")
    assert missing_res.status_code == 404


def test_api_async_single_library_scan(client, app, tmp_path, sample_epub):
    import shutil

    _login_admin(client, app)

    # Create folder and library
    folder = tmp_path / "single_lib_folder"
    folder.mkdir()
    shutil.copy(sample_epub, folder / "book1.epub")

    add_res = client.post(
        "/api/libraries",
        json={"path": str(folder), "name": "Single Lib"},
    )
    assert add_res.status_code == 201
    lib_id = add_res.get_json()["library"]["id"]

    # Trigger single library async scan
    scan_res = client.post(f"/api/libraries/{lib_id}/scan")
    assert scan_res.status_code == 202
    data = scan_res.get_json()
    job_id = data["job_id"]

    # Wait for job completion
    timeout = 5.0
    start = time.time()
    while time.time() - start < timeout:
        status_res = client.get(f"/api/jobs/{job_id}")
        if status_res.get_json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)

    assert status_res.get_json()["status"] == "completed"


def test_api_async_enrich_library(client, app, sample_epub):
    _login_admin(client, app)

    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        index_single_book(sample_epub, covers_dir)

    mock_meta = EnrichedMetadata(
        title="Async Enriched Book",
        description="Async description",
        publisher="Async Pub",
        source="Mock Source",
    )

    with patch(
        "aarkib.services.enricher.fetch_external_metadata", return_value=mock_meta
    ):
        enrich_res = client.post("/api/libraries/enrich", json={"overwrite": True})
        assert enrich_res.status_code == 202
        job_id = enrich_res.get_json()["job_id"]

        timeout = 5.0
        start = time.time()
        while time.time() - start < timeout:
            status_res = client.get(f"/api/jobs/{job_id}")
            if status_res.get_json()["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)

        assert status_res.get_json()["status"] == "completed"


def test_media_item_library_foreign_key_and_relationships(app, tmp_path, sample_epub):
    import shutil

    folder = tmp_path / "rel_test_folder"
    folder.mkdir()
    shutil.copy(sample_epub, folder / "rel_book.epub")

    with app.app_context():
        # Create a Library model directly
        lib = Library(
            slug="rel-lib",
            name="Relationship Library",
            path=str(folder.resolve()),
            media_type="book",
        )
        db.session.add(lib)
        db.session.commit()
        lib_id = lib.id

        # Scan library
        scan_res = scan_library(app, library_id=lib_id)
        assert scan_res["scanned"] >= 1

        # Query book and verify library_id and relationship
        book = db.session.scalar(
            db.select(Book).where(Book.title == "Sample Test Book")
        )
        assert book is not None
        assert book.library_id == lib_id
        assert book.library is not None
        assert book.library.id == lib_id
        assert book.library.name == "Relationship Library"

        # Verify reverse relationships
        reloaded_lib = db.session.get(Library, lib_id)
        assert reloaded_lib is not None
        assert len(reloaded_lib.media_items) == 1
        assert reloaded_lib.media_items[0].id == book.id
        assert len(reloaded_lib.books) == 1
        assert reloaded_lib.books[0].id == book.id


def test_job_persistence_lifecycle(app):
    jm = JobManager(max_workers=2)

    def sample_worker(app, progress_callback=None):
        if progress_callback:
            progress_callback(50.0, "Halfway done")
        return {"items": 10, "status": "ok"}

    job = jm.submit_job("test_persist", sample_worker, app=app)
    job_id = job.id

    timeout = 3.0
    start = time.time()
    while job.status != JobStatus.COMPLETED and time.time() - start < timeout:
        time.sleep(0.02)

    assert job.status == JobStatus.COMPLETED

    with app.app_context():
        rec = db.session.get(JobRecord, job_id)
        assert rec is not None
        assert rec.job_type == "test_persist"
        assert rec.status == JobStatus.COMPLETED.value
        assert rec.progress == 100.0
        assert rec.finished_at is not None
        d = rec.to_dict()
        assert d["id"] == job_id
        assert d["result"] == {"items": 10, "status": "ok"}

    jm.shutdown(wait=True)


def test_job_retrieval_after_memory_cleared(app):
    jm = JobManager(max_workers=2)

    def quick_fn(app, progress_callback=None):
        return {"done": True}

    job = jm.submit_job("restore_test", quick_fn, app=app)
    job_id = job.id

    timeout = 3.0
    start = time.time()
    while job.status != JobStatus.COMPLETED and time.time() - start < timeout:
        time.sleep(0.02)

    # Wipe in-memory dictionary to simulate server restart or cache eviction
    with jm._lock:
        jm._jobs.clear()

    # Query get_job - should fetch from SQLite job_history
    restored = jm.get_job(job_id, app=app)
    assert restored is not None
    assert restored.id == job_id
    assert restored.job_type == "restore_test"
    assert restored.status == JobStatus.COMPLETED
    assert restored.result == {"done": True}
    assert restored.progress == 100.0

    # Query list_jobs - should fetch from SQLite job_history
    job_list = jm.list_jobs(app=app)
    assert any(j.id == job_id for j in job_list)

    jm.shutdown(wait=True)


def test_job_reconciliation_on_startup(app):
    from datetime import UTC, datetime

    with app.app_context():
        dangling_run = JobRecord(
            id="job_dangling_running",
            job_type="scan_library",
            status="running",
            progress=45.0,
            progress_message="Scanning...",
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )
        dangling_queued = JobRecord(
            id="job_dangling_queued",
            job_type="scan_library",
            status="queued",
            progress=0.0,
            created_at=datetime.now(UTC),
        )
        completed_job = JobRecord(
            id="job_already_done",
            job_type="scan_library",
            status="completed",
            progress=100.0,
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        db.session.add_all([dangling_run, dangling_queued, completed_job])
        db.session.commit()

    jm = JobManager(max_workers=1)
    reconciled_count = jm.reconcile_on_startup(app)
    assert reconciled_count == 2

    with app.app_context():
        rec1 = db.session.get(JobRecord, "job_dangling_running")
        assert rec1.status == JobStatus.INTERRUPTED.value
        assert "server restart" in rec1.error_message
        assert rec1.finished_at is not None

        rec2 = db.session.get(JobRecord, "job_dangling_queued")
        assert rec2.status == JobStatus.INTERRUPTED.value
        assert "server restart" in rec2.error_message

        rec3 = db.session.get(JobRecord, "job_already_done")
        assert rec3.status == JobStatus.COMPLETED.value


def test_job_cleanup_db(app):
    jm = JobManager(max_workers=1)

    def simple_worker(app, progress_callback=None):
        return 1

    job = jm.submit_job("cleanup_test", simple_worker, app=app)
    timeout = 3.0
    start = time.time()
    while job.status != JobStatus.COMPLETED and time.time() - start < timeout:
        time.sleep(0.02)

    jm.cleanup_old_jobs(max_age_seconds=0, app=app, delete_db=True)
    assert jm.get_job(job.id, app=app) is None

    with app.app_context():
        assert db.session.get(JobRecord, job.id) is None

    jm.shutdown(wait=True)
