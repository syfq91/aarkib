"""Tests for Backup & Disaster Recovery subsystem."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from flask import Flask

from aarkib import create_app
from aarkib.extensions import db
from aarkib.models import MediaItem, User
from aarkib.services.backup import (
    DATABASE_FILENAME,
    MANIFEST_FILENAME,
    create_backup,
    restore_backup,
    validate_backup,
)


@pytest.fixture
def file_app(tmp_path: Path) -> Flask:
    """Create an app with a file-backed SQLite database (required for hot SQLite snapshots)."""
    db_file = tmp_path / "test_aarkib.db"
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_file}",
            "SECRET_KEY": "backup-test-secret",
            "WTF_CSRF_ENABLED": False,
            "COVERS_DIR": str(covers_dir),
            "BACKUPS_DIR": str(tmp_path / "backups"),
        }
    )

    with app.app_context():
        # Create an admin user for authenticated API tests
        admin = User(username="admin", is_admin=True)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()

    return app


def test_create_backup_archive_structure(file_app: Flask, tmp_path: Path) -> None:
    """Verify create_backup generates a valid ZIP containing manifest, database, and covers."""
    with file_app.app_context():
        # Add sample media item
        book = MediaItem(
            title="Dune",
            original_file_path=str(tmp_path / "dune.epub"),
            file_format="epub",
            file_hash="hash_dune_12345",
        )
        db.session.add(book)
        db.session.commit()

        # Add sample cover file
        covers_dir = Path(file_app.config["COVERS_DIR"])
        cover_file = covers_dir / "sample_cover.webp"
        cover_file.write_bytes(b"RIFFWEBP_SAMPLE_BYTES")

        backup_path = create_backup(file_app)
        assert backup_path.is_file()
        assert backup_path.suffix == ".zip"

        # Verify ZIP contents
        with zipfile.ZipFile(backup_path, "r") as zf:
            namelist = zf.namelist()
            assert MANIFEST_FILENAME in namelist
            assert DATABASE_FILENAME in namelist
            assert "covers/sample_cover.webp" in namelist

            manifest = json.loads(zf.read(MANIFEST_FILENAME).decode("utf-8"))
            assert manifest["app_name"] == "Aarkib"
            assert manifest["media_count"] >= 1
            assert manifest["covers_count"] >= 1
            assert manifest["database_checksum"].startswith("sha256:")


def test_validate_backup(file_app: Flask, tmp_path: Path) -> None:
    """Test validation of valid and invalid backup archives."""
    with file_app.app_context():
        backup_path = create_backup(file_app)
        is_valid, msg, manifest = validate_backup(backup_path)
        assert is_valid is True
        assert "verified" in msg.lower()
        assert manifest["app_name"] == "Aarkib"

        # Test invalid archive: corrupt file
        corrupt_path = tmp_path / "corrupt.zip"
        corrupt_path.write_bytes(b"not a zip file at all")
        is_val, msg, _ = validate_backup(corrupt_path)
        assert is_val is False
        assert "not a valid ZIP" in msg

        # Test missing manifest
        missing_manifest_path = tmp_path / "no_manifest.zip"
        with zipfile.ZipFile(missing_manifest_path, "w") as zf:
            zf.writestr(DATABASE_FILENAME, b"empty")
        is_val, msg, _ = validate_backup(missing_manifest_path)
        assert is_val is False
        assert "Missing manifest.json" in msg


def test_restore_backup_restores_state(file_app: Flask, tmp_path: Path) -> None:
    """Test that restore_backup atomically replaces database and cover state."""
    with file_app.app_context():
        # Baseline: 1 book
        book1 = MediaItem(
            title="Book One",
            original_file_path=str(tmp_path / "b1.epub"),
            file_format="epub",
            file_hash="hash_b1_111",
        )
        db.session.add(book1)
        db.session.commit()

        # Take backup at state 1
        backup_path = create_backup(file_app)

        # Mutate database: add 2 more books
        book2 = MediaItem(
            title="Book Two",
            original_file_path=str(tmp_path / "b2.epub"),
            file_format="epub",
            file_hash="hash_b2_222",
        )
        book3 = MediaItem(
            title="Book Three",
            original_file_path=str(tmp_path / "b3.epub"),
            file_format="epub",
            file_hash="hash_b3_333",
        )
        db.session.add_all([book2, book3])
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(MediaItem.id))) == 3

    # Restore from the backup snapshot
    success, msg = restore_backup(file_app, backup_path)
    assert success is True

    # Check that database returned to 1 item
    with file_app.app_context():
        assert db.session.scalar(db.select(db.func.count(MediaItem.id))) == 1
        restored_book = db.session.scalar(
            db.select(MediaItem).where(MediaItem.title == "Book One")
        )
        assert restored_book is not None


def test_backup_api_endpoints(file_app: Flask) -> None:
    """Test the REST API endpoints for backup creation, listing, downloading, and deletion."""
    client = file_app.test_client()

    # Login as admin
    client.post("/auth/login", data={"username": "admin", "password": "adminpass"})

    # 1. Create backup via POST /api/backup
    create_res = client.post("/api/backup?include_covers=false")
    assert create_res.status_code == 201
    data = create_res.get_json()
    assert data["status"] == "success"
    filename = data["filename"]

    # 2. List backups via GET /api/backup
    list_res = client.get("/api/backup")
    assert list_res.status_code == 200
    list_data = list_res.get_json()
    assert list_data["count"] >= 1
    assert any(b["filename"] == filename for b in list_data["backups"])

    # 3. Download backup via GET /api/backup/download/<filename>
    dl_res = client.get(f"/api/backup/download/{filename}")
    assert dl_res.status_code == 200
    assert dl_res.mimetype == "application/zip"
    assert len(dl_res.data) > 0

    # 4. Delete backup via DELETE /api/backup/<filename>
    del_res = client.delete(f"/api/backup/{filename}")
    assert del_res.status_code == 200

    # Verify deleted
    list_res2 = client.get("/api/backup")
    assert not any(b["filename"] == filename for b in list_res2.get_json()["backups"])
