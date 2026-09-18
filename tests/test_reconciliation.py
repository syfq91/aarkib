"""Tests for mount-safe library availability verification and three-way reconciliation."""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from sqlalchemy import select

from aarkib import create_app
from aarkib.config import TestConfig
from aarkib.extensions import db
from aarkib.models import Library, MediaItem
from aarkib.services.library_service import validate_library_availability
from aarkib.services.scanner import scan_library


def test_validate_library_availability_basics(tmp_path: Path):
    """Test availability checks on various filesystem states."""
    # 1. Non-existent path
    non_existent = tmp_path / "does_not_exist"
    lib_missing = Library(slug="missing", name="Missing", path=str(non_existent))
    assert validate_library_availability(lib_missing) is False

    # 2. Path is a file, not a directory
    regular_file = tmp_path / "file.txt"
    regular_file.write_text("hello")
    lib_file = Library(slug="file", name="File", path=str(regular_file))
    assert validate_library_availability(lib_file) is False

    # 3. Empty directory with expected_items_count == 0 (safe fresh library)
    empty_dir = tmp_path / "empty_fresh"
    empty_dir.mkdir()
    lib_empty = Library(slug="empty", name="Empty", path=str(empty_dir))
    assert validate_library_availability(lib_empty, expected_items_count=0) is True

    # 4. Empty directory with expected_items_count > 0 (unmounted mount point suspected)
    assert validate_library_availability(lib_empty, expected_items_count=10) is False

    # 5. Empty directory with expected_items_count > 0 but force_prune=True
    assert (
        validate_library_availability(
            lib_empty, expected_items_count=10, force_prune=True
        )
        is True
    )

    # 6. Healthy directory with files
    healthy_dir = tmp_path / "healthy"
    healthy_dir.mkdir()
    (healthy_dir / "media.mp4").write_text("data")
    lib_healthy = Library(slug="healthy", name="Healthy", path=str(healthy_dir))
    assert validate_library_availability(lib_healthy, expected_items_count=1) is True


def test_validate_library_availability_candidate_count(tmp_path: Path):
    """Test candidate count validation during post-walk verification."""
    lib_dir = tmp_path / "media_dir"
    lib_dir.mkdir()
    (lib_dir / "other.txt").write_text("not media")
    lib = Library(slug="test", name="Test", path=str(lib_dir))

    # Expected items > 0 but scan discovered 0 candidate files
    assert (
        validate_library_availability(lib, expected_items_count=5, candidate_count=0)
        is False
    )

    # If force_prune is True, should pass
    assert (
        validate_library_availability(
            lib, expected_items_count=5, candidate_count=0, force_prune=True
        )
        is True
    )

    # Expected items > 0 and candidates found
    assert (
        validate_library_availability(lib, expected_items_count=5, candidate_count=5)
        is True
    )

    # Expected items == 0 and candidate_count == 0
    assert (
        validate_library_availability(lib, expected_items_count=0, candidate_count=0)
        is True
    )


def test_unmounted_storage_abort_missing_dir(tmp_path: Path, sample_epub: Path):
    """Simulate unmounted storage where library root path does not exist.

    Verify that scanner aborts reconciliation and retains 100% of existing DB records.
    """
    nas_dir = tmp_path / "nas_storage"
    nas_dir.mkdir()
    book_dest = nas_dir / "book.epub"
    shutil.copy(sample_epub, book_dest)

    class NasConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        MEDIA_DIRS = [nas_dir]
        MEDIA_DIR = nas_dir
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/nas_test.db"

    app = create_app(NasConfig)

    with app.app_context():
        # Initial scan: index book
        res1 = scan_library(app)
        assert res1["scanned"] == 1
        assert res1["added"] == 1
        assert res1["deleted"] == 0
        assert len(res1["aborted_libraries"]) == 0

        item_count = db.session.scalar(
            select(MediaItem).where(MediaItem.title == "Sample Test Book")
        )
        assert item_count is not None

        # Simulate unmounting NAS: entire directory is removed
        shutil.rmtree(nas_dir)
        assert not nas_dir.exists()

        # Rescan: must abort reconciliation for unmounted library and protect DB records
        res2 = scan_library(app)
        assert res2["scanned"] == 0
        assert res2["deleted"] == 0
        assert len(res2["aborted_libraries"]) > 0

        # CRITICAL SAFETY INVARIANT: Database record MUST STILL EXIST
        retained_item = db.session.scalar(
            select(MediaItem).where(MediaItem.title == "Sample Test Book")
        )
        assert retained_item is not None
        # Directory must NOT be automatically re-created
        assert not nas_dir.exists()


def test_unmounted_storage_abort_empty_mountpoint(tmp_path: Path, sample_epub: Path):
    """Simulate unmounted storage where mount point exists as an empty directory.

    Verify that scanner detects empty mount point, aborts, and retains all DB records.
    """
    mount_dir = tmp_path / "mount_books"
    mount_dir.mkdir()
    shutil.copy(sample_epub, mount_dir / "book1.epub")

    class MountConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        MEDIA_DIRS = [mount_dir]
        MEDIA_DIR = mount_dir
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/mount_test.db"

    app = create_app(MountConfig)

    with app.app_context():
        # Initial scan
        res1 = scan_library(app)
        assert res1["scanned"] == 1
        assert res1["added"] == 1
        assert res1["deleted"] == 0

        # Simulate unmount: media file is gone, leaving empty mount point directory
        (mount_dir / "book1.epub").unlink()
        assert mount_dir.exists()
        assert len(list(mount_dir.iterdir())) == 0

        # Rescan
        res2 = scan_library(app)
        assert res2["scanned"] == 0
        assert res2["deleted"] == 0
        assert len(res2["aborted_libraries"]) > 0

        # Record retained
        item = db.session.scalar(
            select(MediaItem).where(MediaItem.title == "Sample Test Book")
        )
        assert item is not None


def test_multi_library_partial_unmount(
    tmp_path: Path, sample_epub: Path, sample_cbz: Path
):
    """Multi-library test: local library is safe and modified; remote library is unmounted.

    Verify pruning applies ONLY to the safe local library, while protecting the unmounted library.
    """
    local_dir = tmp_path / "local_books"
    nas_dir = tmp_path / "nas_comics"
    local_dir.mkdir()
    nas_dir.mkdir()

    # Local has book1 and book2
    shutil.copy(sample_epub, local_dir / "book1.epub")
    shutil.copy(sample_epub, local_dir / "book2.epub")
    # NAS has comic1
    shutil.copy(sample_cbz, nas_dir / "comic1.cbz")

    class MultiConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        MEDIA_DIRS = [local_dir, nas_dir]
        MEDIA_DIR = local_dir
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/multi_sync.db"

    app = create_app(MultiConfig)

    with app.app_context():
        # Initial scan indexes all 3 items
        res1 = scan_library(app)
        assert res1["scanned"] == 3
        assert res1["added"] == 3
        assert res1["deleted"] == 0
        assert len(res1["aborted_libraries"]) == 0

        # Now:
        # 1. In local_dir, user deleted book2.epub
        (local_dir / "book2.epub").unlink()

        # 2. NAS is unmounted (nas_dir is emptied)
        (nas_dir / "comic1.cbz").unlink()

        # Rescan
        res2 = scan_library(app)
        assert res2["scanned"] == 1  # Only book1.epub scanned
        assert res2["deleted"] == 1  # Only book2.epub deleted from local library
        assert len(res2["aborted_libraries"]) > 0  # NAS library was aborted

        # Verify DB state:
        # book1 exists
        assert (
            db.session.scalar(
                select(MediaItem).where(
                    MediaItem.original_file_path
                    == str((local_dir / "book1.epub").resolve())
                )
            )
            is not None
        )
        # book2 pruned
        assert (
            db.session.scalar(
                select(MediaItem).where(
                    MediaItem.original_file_path
                    == str((local_dir / "book2.epub").resolve())
                )
            )
            is None
        )
        # comic1 RETAINED (nas was unmounted)
        assert (
            db.session.scalar(
                select(MediaItem).where(
                    MediaItem.original_file_path
                    == str((nas_dir / "comic1.cbz").resolve())
                )
            )
            is not None
        )


def test_three_way_reconciliation_pipeline(
    tmp_path: Path, sample_epub: Path, sample_cbz: Path
):
    """Test the complete three-way sync pipeline: NEW, CHANGED, UNCHANGED, MISSING."""
    media_dir = tmp_path / "sync_media"
    media_dir.mkdir()

    file_a = media_dir / "book_a.epub"
    file_b = media_dir / "comic_b.cbz"
    shutil.copy(sample_epub, file_a)
    shutil.copy(sample_cbz, file_b)

    class SyncConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        MEDIA_DIRS = [media_dir]
        MEDIA_DIR = media_dir
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/pipeline.db"

    app = create_app(SyncConfig)

    with app.app_context():
        # Step 1: Initial scan -> 2 NEW files
        res1 = scan_library(app)
        assert res1["scanned"] == 2
        assert res1["added"] == 2
        assert res1["updated"] == 0
        assert res1["deleted"] == 0
        assert res1["added_or_updated"] == 2

        # Step 2: Re-scan with no changes -> 2 UNCHANGED files
        res2 = scan_library(app)
        assert res2["scanned"] == 2
        assert res2["added"] == 0
        assert res2["updated"] == 0
        assert res2["deleted"] == 0
        assert res2["added_or_updated"] == 0

        # Step 3: Modify file_a (update mtime and content) -> 1 CHANGED, 1 UNCHANGED
        current_mtime = file_a.stat().st_mtime
        # Append some dummy bytes to change size and bump mtime
        with open(file_a, "ab") as f:
            f.write(b"extra")
        os.utime(file_a, (current_mtime + 100, current_mtime + 100))

        res3 = scan_library(app)
        assert res3["scanned"] == 2
        assert res3["added"] == 0
        assert res3["updated"] == 1
        assert res3["deleted"] == 0
        assert res3["added_or_updated"] == 1

        # Step 4: Add file_c, delete file_b -> 1 NEW, 1 MISSING (deleted), 1 UNCHANGED
        file_c = media_dir / "book_c.epub"
        shutil.copy(sample_epub, file_c)
        file_b.unlink()

        res4 = scan_library(app)
        assert res4["scanned"] == 2  # file_a and file_c
        assert res4["added"] == 1  # file_c
        assert res4["updated"] == 0
        assert res4["deleted"] == 1  # file_b
        assert res4["added_or_updated"] == 1

        # Check final DB state
        assert (
            db.session.scalar(
                select(MediaItem).where(
                    MediaItem.original_file_path == str(file_a.resolve())
                )
            )
            is not None
        )
        assert (
            db.session.scalar(
                select(MediaItem).where(
                    MediaItem.original_file_path == str(file_b.resolve())
                )
            )
            is None
        )
        assert (
            db.session.scalar(
                select(MediaItem).where(
                    MediaItem.original_file_path == str(file_c.resolve())
                )
            )
            is not None
        )


def test_scan_cancellation_mid_reconciliation(tmp_path: Path, sample_epub: Path):
    """Test that cancelling a scan mid-execution commits progress cleanly without pruning."""
    media_dir = tmp_path / "cancel_media"
    media_dir.mkdir()
    shutil.copy(sample_epub, media_dir / "item1.epub")
    shutil.copy(sample_epub, media_dir / "item2.epub")

    class CancelConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        MEDIA_DIRS = [media_dir]
        MEDIA_DIR = media_dir
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/cancel.db"

    app = create_app(CancelConfig)

    with app.app_context():
        # Pre-populate DB with an item that would otherwise be pruned if not cancelled
        cancel_event = threading.Event()
        cancel_event.set()  # Cancel immediately

        res = scan_library(app, cancel_event=cancel_event)
        assert res.get("cancelled") == 1
        assert res["deleted"] == 0


def test_force_prune_override(tmp_path: Path, sample_epub: Path):
    """Test that force_prune=True permits pruning even when directory is completely empty."""
    media_dir = tmp_path / "prune_media"
    media_dir.mkdir()
    book_file = media_dir / "book.epub"
    shutil.copy(sample_epub, book_file)

    class ForceConfig(TestConfig):
        DATA_DIR = tmp_path / "data"
        MEDIA_DIRS = [media_dir]
        MEDIA_DIR = media_dir
        COVERS_DIR = tmp_path / "data" / "covers"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path}/force.db"

    app = create_app(ForceConfig)

    with app.app_context():
        # Scan initial
        res1 = scan_library(app)
        assert res1["added"] == 1
        assert res1["deleted"] == 0

        # Remove file on disk
        book_file.unlink()
        assert len(list(media_dir.iterdir())) == 0

        # Force prune
        res2 = scan_library(app, force_prune=True)
        assert res2["deleted"] == 1
        assert len(res2["aborted_libraries"]) == 0

        # Verify DB item was removed
        assert db.session.scalar(select(MediaItem)) is None
