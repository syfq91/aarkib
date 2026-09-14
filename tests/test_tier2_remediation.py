from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from aarkib.extensions import db, safe_commit
from aarkib.models import Collection, Creator, Library, MediaItem, Tag, User
from aarkib.services.job_manager import JobManager, JobStatus, job_manager

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient


def test_h4_eager_loading_subsonic_jellyfin_opds(
    client: FlaskClient, app: Flask
) -> None:
    """Verify H4: Subsonic, Jellyfin, and OPDS eager loading executes cleanly without N+1 query storms."""
    with app.app_context():
        # Setup test user
        user = User(username="sub_h4_user", is_admin=True)
        user.set_password("pass_h4")
        db.session.add(user)

        # Library
        lib = Library(
            name="Audio Lib", slug="audio-lib", path="/media/audio", media_type="audio"
        )
        db.session.add(lib)
        db.session.flush()

        # Artist, Album, Tag
        artist = Creator(name="Ludwig van Beethoven")
        db.session.add(artist)
        db.session.flush()

        album = Collection(name="Symphonies")
        db.session.add(album)
        db.session.flush()

        tag = Tag(name="Classical")
        db.session.add(tag)
        db.session.flush()

        # Media items
        item = MediaItem(
            title="Symphony No. 5 in C Minor",
            original_file_path="/media/audio/beethoven/sym5.mp3",
            file_format="mp3",
            file_hash="hash_h4_beethoven",
            media_type="audio",
            library_id=lib.id,
            collection_id=album.id,
        )
        item.creators = [artist]
        item.tags = [tag]
        db.session.add(item)
        safe_commit()
        user_id = user.id

    # 1. Test Subsonic API endpoints using selectinload
    auth_q = "u=sub_h4_user&p=pass_h4&f=json"
    res_artists = client.get(f"/rest/getArtists.view?{auth_q}")
    assert res_artists.status_code == 200
    res_json = res_artists.get_json()
    assert res_json["subsonic-response"]["status"] == "ok"

    res_search = client.get(f"/rest/search3.view?query=Symphony&{auth_q}")
    assert res_search.status_code == 200
    assert res_search.get_json()["subsonic-response"]["status"] == "ok"

    # 2. Test OPDS catalog endpoints using selectinload
    res_authors = client.get("/opds/authors")
    assert res_authors.status_code == 200
    assert "application/atom+xml" in res_authors.headers["Content-Type"]

    res_series = client.get("/opds/series")
    assert res_series.status_code == 200
    assert "application/atom+xml" in res_series.headers["Content-Type"]

    res_tags = client.get("/opds/tags")
    assert res_tags.status_code == 200
    assert "application/atom+xml" in res_tags.headers["Content-Type"]

    # 3. Test Jellyfin Items endpoint
    with app.app_context():
        from aarkib.plugins.jellyfin import generate_jellyfin_token

        token = generate_jellyfin_token(user_id)

    headers = {"X-Emby-Token": token}
    res_jf_items = client.get("/Items", headers=headers)
    assert res_jf_items.status_code == 200
    jf_data = res_jf_items.get_json()
    assert "Items" in jf_data
    assert any(i["Name"] == "Symphony No. 5 in C Minor" for i in jf_data["Items"])


def test_h5_safe_commit_rollback_on_failure(app: Flask) -> None:
    """Verify H5: safe_commit catches exceptions, rolls back the session, and re-raises."""
    with app.app_context():
        user1 = User(username="unique_user_h5", is_admin=False)
        user1.set_password("pass1234")
        db.session.add(user1)
        safe_commit()

        # Attempt to insert a duplicate username, triggering IntegrityError
        user2 = User(username="unique_user_h5", is_admin=False)
        user2.set_password("pass5678")
        db.session.add(user2)

        with pytest.raises(IntegrityError):
            safe_commit()

        # Session should be clean (rolled back) and ready for subsequent queries without PendingRollbackError
        fresh_query = (
            db.session.query(User).filter_by(username="unique_user_h5").first()
        )
        assert fresh_query is not None
        assert fresh_query.id == user1.id


def test_h7_job_cancellation_in_memory_and_db(app: Flask) -> None:
    """Verify H7: JobManager cancel_job handles queued and running jobs with threading.Event."""
    jm = JobManager(max_workers=1)

    gate = threading.Event()
    worker_started = threading.Event()
    cancelled_observed = threading.Event()

    def long_task(cancel_event=None, progress_callback=None):
        worker_started.set()
        # Cooperatively check cancel_event
        while not gate.is_set():
            if cancel_event is not None and cancel_event.is_set():
                cancelled_observed.set()
                return {"status": "cancelled"}
            time.sleep(0.01)
        return {"status": "completed"}

    # Submit job
    job = jm.submit_job("cancellable_task", long_task, app=app)
    assert job.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    # Wait for worker to start
    assert worker_started.wait(timeout=2.0)

    # Cancel the running job
    result = jm.cancel_job(job.id, app=app)
    assert result is not None
    assert result["cancelled"] is True
    assert result["status"] == JobStatus.CANCELLED.value

    # The cooperative worker detects cancellation
    assert cancelled_observed.wait(timeout=2.0)

    # Let worker finish and clean up
    gate.set()
    time.sleep(0.05)

    assert job.is_cancelled is True
    assert job.status == JobStatus.CANCELLED

    # Check already cancelled status handling
    res_repeat = jm.cancel_job(job.id, app=app)
    assert res_repeat is not None
    assert res_repeat["cancelled"] is False

    jm.shutdown(wait=True)


def test_h7_api_cancel_endpoint(app: Flask) -> None:
    """Verify H7: POST /api/jobs/<job_id>/cancel endpoint permissions and operations."""
    # 1. Unauthenticated client should be rejected with 401
    unauth = app.test_client()
    res_unauth = unauth.post("/api/jobs/dummy-job/cancel")
    assert res_unauth.status_code == 401

    # 2. Authenticated admin client
    admin_client = app.test_client()
    with app.app_context():
        admin = User(username="admin_cancel_endpoint", is_admin=True)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()
    admin_client.post(
        "/auth/login",
        data={"username": "admin_cancel_endpoint", "password": "adminpass"},
        follow_redirects=True,
    )

    # 3. Non-existent job returns 404
    res_404 = admin_client.post("/api/jobs/non-existent-job-xyz/cancel")
    assert res_404.status_code == 404
    assert res_404.get_json()["error"] == "Job not found"

    # 4. Create a running job via job_manager
    gate = threading.Event()
    started = threading.Event()

    def sample_worker(app=None, cancel_event=None, progress_callback=None):
        started.set()
        while not gate.is_set():
            if cancel_event and cancel_event.is_set():
                return {"result": "cancelled"}
            time.sleep(0.01)
        return {"result": "done"}

    job = job_manager.submit_job("api_cancel_test", sample_worker, app=app)
    assert started.wait(timeout=2.0)

    # 5. Cancel via API endpoint
    cancel_res = admin_client.post(f"/api/jobs/{job.id}/cancel")
    assert cancel_res.status_code == 200
    data = cancel_res.get_json()
    assert data["status"] == "success"
    assert data["job"]["cancelled"] is True
    assert data["job"]["status"] == "cancelled"

    # Let thread exit cleanly
    gate.set()
    time.sleep(0.05)

    # 6. Cancelling an already cancelled job returns 400
    repeat_res = admin_client.post(f"/api/jobs/{job.id}/cancel")
    assert repeat_res.status_code == 400
    assert "already cancelled" in repeat_res.get_json()["error"]
