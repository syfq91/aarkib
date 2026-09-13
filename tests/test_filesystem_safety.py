from __future__ import annotations

from pathlib import Path

from aarkib.config import get_ffmpeg_binary, get_ffprobe_binary
from aarkib.extensions import db
from aarkib.models import Library, MediaItem
from aarkib.routes.api import is_safe_media_path


def test_is_safe_media_path(app, tmp_path):
    """Verifies is_safe_media_path correctly permits library paths and blocks escapes."""
    with app.app_context():
        # Path inside tmp_path / data should be safe
        safe_media = tmp_path / "data" / "media" / "test.epub"
        assert is_safe_media_path(safe_media) is True

        # Arbitrary system file outside allowed roots should be rejected
        unsafe_system_file = Path("/etc/shadow")
        assert is_safe_media_path(unsafe_system_file) is False

        # Add a custom library folder in DB
        custom_lib_dir = tmp_path / "custom_library"
        custom_lib_dir.mkdir(parents=True, exist_ok=True)
        lib = Library(
            slug="custom-lib",
            name="Custom Lib",
            path=str(custom_lib_dir),
            media_type="book",
        )
        db.session.add(lib)
        db.session.commit()

        # Path inside custom library folder should now be safe
        custom_file = custom_lib_dir / "my_book.epub"
        assert is_safe_media_path(custom_file) is True


def test_get_media_file_blocks_path_escape(app, client, tmp_path):
    """Verifies GET /api/media/<id>/file rejects items pointing outside library roots with 403."""
    external_file = Path("/tmp/aarkib_unauthorized_test_file.txt")
    external_file.write_text("classified data")

    try:
        with app.app_context():
            app.config["TESTING"] = False
            item = MediaItem(
                title="Malicious Item",
                media_type="book",
                file_format="txt",
                original_file_path=str(external_file),
                file_hash="fakehash12345",
                file_size=15,
            )
            db.session.add(item)
            db.session.commit()
            item_id = item.id

        resp = client.get(f"/api/media/{item_id}/file")
        assert resp.status_code == 403
        data = resp.get_json()
        assert "outside configured library roots" in data["error"]
    finally:
        if external_file.exists():
            external_file.unlink()
        with app.app_context():
            app.config["TESTING"] = True


def test_download_media_file_blocks_path_escape(app, client, tmp_path):
    """Verifies GET /api/media/<id>/download rejects items pointing outside library roots with 403."""
    external_file = Path("/tmp/aarkib_unauthorized_download_file.txt")
    external_file.write_text("classified download")

    try:
        with app.app_context():
            app.config["TESTING"] = False
            item = MediaItem(
                title="Malicious Download",
                media_type="book",
                file_format="txt",
                original_file_path=str(external_file),
                file_hash="fakehash67890",
                file_size=19,
            )
            db.session.add(item)
            db.session.commit()
            item_id = item.id

        resp = client.get(f"/api/media/{item_id}/download")
        assert resp.status_code == 403
        data = resp.get_json()
        assert "outside configured library roots" in data["error"]
    finally:
        if external_file.exists():
            external_file.unlink()
        with app.app_context():
            app.config["TESTING"] = True


def test_configurable_ffmpeg_paths(monkeypatch, tmp_path):
    """Verifies AARKIB_FFMPEG_PATH and AARKIB_FFPROBE_PATH override discovery."""
    # Create fake executable scripts
    fake_ffmpeg = tmp_path / "custom_ffmpeg"
    fake_ffmpeg.write_text("#!/bin/sh\nexit 0\n")
    fake_ffmpeg.chmod(0o755)

    fake_ffprobe = tmp_path / "custom_ffprobe"
    fake_ffprobe.write_text("#!/bin/sh\nexit 0\n")
    fake_ffprobe.chmod(0o755)

    monkeypatch.setenv("AARKIB_FFMPEG_PATH", str(fake_ffmpeg))
    monkeypatch.setenv("AARKIB_FFPROBE_PATH", str(fake_ffprobe))

    assert get_ffmpeg_binary() == str(fake_ffmpeg)
    assert get_ffprobe_binary() == str(fake_ffprobe)

    # Test config dict override
    assert get_ffmpeg_binary({"FFMPEG_PATH": str(fake_ffmpeg)}) == str(fake_ffmpeg)
    assert get_ffprobe_binary({"FFPROBE_PATH": str(fake_ffprobe)}) == str(fake_ffprobe)
