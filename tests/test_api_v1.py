"""Tests for versioned REST API v1 endpoints (/api/v1/...)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from aarkib.extensions import db
from aarkib.models import (
    Library,
    MediaItem,
    Profile,
    ProfileLibraryAccess,
)
from aarkib.models.token import DeviceToken
from aarkib.services.indexer import index_media_file
from aarkib.services.job_manager import job_manager


def test_api_v1_health(unauth_client):
    """Healthcheck endpoint is public and returns v1 information."""
    res = unauth_client.get("/api/v1/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "healthy"
    assert data["version"] == "v1"
    assert data["app"] == "aarkib"


def test_api_v1_auth_enforcement(app, unauth_client, default_user):
    """Verify unauthenticated, invalid, expired, and scoped tokens on /api/v1."""
    # 1. Unauthenticated request rejected
    res = unauth_client.get("/api/v1/profiles")
    assert res.status_code == 401
    assert "Authentication required" in res.get_json()["error"]

    # 2. Invalid Bearer token
    res = unauth_client.get(
        "/api/v1/profiles",
        headers={"Authorization": "Bearer invalid_token_123"},
    )
    assert res.status_code == 401
    assert "Invalid API token" in res.get_json()["error"]

    # 3. Expired Bearer token
    with app.app_context():
        tok_obj, raw_token = DeviceToken.create_token(
            user_id=default_user,
            name="Old Tablet",
            scopes=["media:read"],
        )
        tok_obj.expires_at = datetime.now(UTC) - timedelta(days=1)
        db.session.add(tok_obj)
        db.session.commit()

    res = unauth_client.get(
        "/api/v1/media",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert res.status_code == 401
    assert "Device token has expired" in res.get_json()["error"]

    # 4. Valid token but missing required scope
    with app.app_context():
        scoped_token_obj, scoped_token_str = DeviceToken.create_token(
            user_id=default_user,
            name="Restricted App",
            scopes=["other:scope"],
        )
        db.session.add(scoped_token_obj)
        db.session.commit()

    res = unauth_client.get(
        "/api/v1/media",
        headers={"Authorization": f"Bearer {scoped_token_str}"},
    )
    assert res.status_code == 403
    assert "missing required scope" in res.get_json()["error"]


def test_api_v1_media_item_and_playback_plan(client, app, sample_epub):
    """Verify GET /api/v1/media/<id> and GET /api/v1/media/<id>/playback-plan."""
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

    # 1. Canonical Media Item Details
    res = client.get(f"/api/v1/media/{book_id}")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert data["id"] == book_id
    assert data["data"]["id"] == book_id
    assert data["data"]["title"] == "Sample Test Book"
    assert "playback" in data["data"]
    playback = data["data"]["playback"]
    assert "playback_plan" in playback
    assert playback["playback_plan"]["mode"] in (
        "direct",
        "remux",
        "transcode",
        "optimize",
    )

    # 2. Deterministic Playback Plan Endpoint
    res_plan = client.get(
        f"/api/v1/media/{book_id}/playback-plan",
        headers={"User-Agent": "Mozilla/5.0 Chrome/124.0.0.0 Safari/537.36"},
    )
    assert res_plan.status_code == 200
    plan_data = res_plan.get_json()
    assert plan_data["status"] == "success"
    plan = plan_data["data"]
    assert "mode" in plan
    assert "container" in plan
    assert "direct_url" in plan
    assert "diagnostics" in plan

    # 3. Non-existent items
    res_404 = client.get("/api/v1/media/99999")
    assert res_404.status_code == 404
    assert "not found" in res_404.get_json()["error"].lower()

    res_plan_404 = client.get("/api/v1/media/99999/playback-plan")
    assert res_plan_404.status_code == 404


def test_api_v1_media_acl_enforcement(client, app, default_user, sample_epub):
    """Verify profile-level ACL enforcement returns HTTP 403 on v1 media endpoints."""
    with app.app_context():
        lib_restricted = Library(
            slug="classified-v1",
            name="Classified V1",
            path=str(sample_epub.parent),
            media_type="book",
        )
        db.session.add(lib_restricted)
        db.session.commit()

        item = MediaItem(
            original_file_path=str(sample_epub.resolve()),
            title="Classified V1 Document",
            library_id=lib_restricted.id,
            file_format="epub",
            file_hash="hash_classified_v1",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

        prof = Profile(user_id=default_user, name="Guest Profile V1", is_child=False)
        db.session.add(prof)
        db.session.commit()

        acl = ProfileLibraryAccess(
            profile_id=prof.id,
            library_id=lib_restricted.id,
            can_read=False,
            can_download=False,
        )
        db.session.add(acl)
        db.session.commit()
        prof_id = prof.id

    headers = {"X-Aarkib-Profile-Id": str(prof_id)}

    # Item details blocked
    res = client.get(f"/api/v1/media/{item_id}", headers=headers)
    assert res.status_code == 403
    assert "Access denied" in res.get_json()["error"]

    # Playback plan blocked
    res_plan = client.get(f"/api/v1/media/{item_id}/playback-plan", headers=headers)
    assert res_plan.status_code == 403
    assert "Access denied" in res_plan.get_json()["error"]


def test_api_v1_list_media_and_filtering(client, app, sample_epub):
    """Verify listing media with search, media_type, and pagination."""
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        index_media_file(sample_epub, covers_dir)

    res = client.get("/api/v1/media?limit=10&page=1")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert data["total"] >= 1
    assert len(data["items"]) >= 1

    # Search filter matching
    res_search = client.get("/api/v1/media?q=Sample")
    assert res_search.status_code == 200
    assert len(res_search.get_json()["items"]) >= 1

    # Search filter non-matching
    res_none = client.get("/api/v1/media?q=NonExistentTitleQueryXYZ")
    assert res_none.status_code == 200
    assert len(res_none.get_json()["items"]) == 0


def test_api_v1_profile_management_lifecycle(client, app, default_user):
    """Verify complete CRUD and ACL management on /api/v1/profiles."""
    # 1. Create Profile
    res = client.post(
        "/api/v1/profiles",
        json={
            "name": "Bob Junior",
            "is_child": True,
            "avatar_url": "https://avatar.com/bob.png",
        },
    )
    assert res.status_code == 201
    data = res.get_json()
    assert data["status"] == "success"
    profile = data["profile"]
    assert profile["name"] == "Bob Junior"
    assert profile["is_child"] is True
    bob_id = profile["id"]

    # 2. List Profiles
    res_list = client.get("/api/v1/profiles")
    assert res_list.status_code == 200
    profiles = res_list.get_json()["profiles"]
    assert any(p["id"] == bob_id for p in profiles)

    # 3. Retrieve Profile
    res_get = client.get(f"/api/v1/profiles/{bob_id}")
    assert res_get.status_code == 200
    assert res_get.get_json()["profile"]["name"] == "Bob Junior"

    # 4. PATCH Profile (partial update: name & ACLs)
    with app.app_context():
        lib = Library(
            slug="kids-books", name="Kids Books", path="/tmp/kids", media_type="book"
        )
        db.session.add(lib)
        db.session.commit()
        lib_id = lib.id

    res_patch = client.patch(
        f"/api/v1/profiles/{bob_id}",
        json={
            "name": "Robert Junior",
            "library_access": [
                {"library_id": lib_id, "can_read": True, "can_download": True}
            ],
        },
    )
    assert res_patch.status_code == 200
    updated = res_patch.get_json()["profile"]
    assert updated["name"] == "Robert Junior"
    assert len(updated["library_access"]) == 1
    assert updated["library_access"][0]["can_download"] is True

    # 5. Delete Profile
    res_del = client.delete(f"/api/v1/profiles/{bob_id}")
    assert res_del.status_code == 200
    assert "deleted successfully" in res_del.get_json()["message"]

    # 6. Cannot delete default profile
    default_prof = [p for p in profiles if p["name"].lower() == "default"][0]
    res_del_def = client.delete(f"/api/v1/profiles/{default_prof['id']}")
    assert res_del_def.status_code == 400
    assert "Cannot delete the default profile" in res_del_def.get_json()["error"]


def test_api_v1_job_inspection_and_cancellation(client, app):
    """Verify /api/v1/jobs inspection, cancellation, and retry endpoints."""
    # List jobs
    res = client.get("/api/v1/jobs")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert isinstance(data["jobs"], list)

    # 404 for unknown job
    res_404 = client.get("/api/v1/jobs/non-existent-uuid")
    assert res_404.status_code == 404
    assert "Job not found" in res_404.get_json()["error"]

    res_cancel_404 = client.post("/api/v1/jobs/non-existent-uuid/cancel")
    assert res_cancel_404.status_code == 404

    # Submit a dummy job and inspect/cancel
    def dummy_task(job=None):
        import time

        for _ in range(50):
            if job and job._cancel_event.is_set():
                break
            time.sleep(0.05)

    job = job_manager.submit_job("test_v1_task", dummy_task)
    assert job is not None

    # Get job
    res_get = client.get(f"/api/v1/jobs/{job.id}")
    assert res_get.status_code == 200
    assert res_get.get_json()["job"]["id"] == job.id

    # Cancel job
    res_cancel = client.post(f"/api/v1/jobs/{job.id}/cancel")
    assert res_cancel.status_code in (200, 400)


def test_api_v1_library_reconciliation(client, app, tmp_path: Path):
    """Verify POST /api/v1/libraries/<id>/reconcile with availability validation."""
    valid_dir = tmp_path / "valid_library"
    valid_dir.mkdir(parents=True, exist_ok=True)
    # Put a dummy file in valid_dir so it's not empty
    (valid_dir / "test.epub").write_bytes(b"dummy")

    with app.app_context():
        lib_valid = Library(
            slug="valid-reconcile-lib",
            name="Valid Lib",
            path=str(valid_dir),
            media_type="book",
        )
        lib_missing = Library(
            slug="missing-reconcile-lib",
            name="Missing Lib",
            path=str(tmp_path / "non_existent_folder_xyz"),
            media_type="book",
        )
        db.session.add_all([lib_valid, lib_missing])
        db.session.commit()
        valid_id = lib_valid.id
        missing_id = lib_missing.id

    # 1. Non-existent path fails availability validation with 409 Conflict
    res_fail = client.post(f"/api/v1/libraries/{missing_id}/reconcile")
    assert res_fail.status_code == 409
    assert "failed availability validation" in res_fail.get_json()["error"]

    # 2. Valid path with sync=true executes reconciliation
    res_sync = client.post(f"/api/v1/libraries/{valid_id}/reconcile?sync=true")
    assert res_sync.status_code == 200
    data = res_sync.get_json()
    assert data["status"] == "success"
    assert "reconciliation" in data

    # 3. Valid path async triggers background job
    with patch("aarkib.routes.api_v1.scan_library", return_value={"status": "done"}):
        res_async = client.post(f"/api/v1/libraries/{valid_id}/reconcile")
        assert res_async.status_code == 202
        async_data = res_async.get_json()
        assert async_data["status"] == "accepted"
        assert "job_id" in async_data

    # 4. List libraries endpoint
    res_libs = client.get("/api/v1/libraries")
    assert res_libs.status_code == 200
    assert len(res_libs.get_json()["libraries"]) >= 1

    # 5. Single library endpoint
    res_single = client.get(f"/api/v1/libraries/{valid_id}")
    assert res_single.status_code == 200
    assert res_single.get_json()["library"]["name"] == "Valid Lib"
