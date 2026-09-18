"""Backup and Disaster Recovery service for Aarkib.

Handles hot SQLite snapshots (via sqlite3 backup API), covers archiving,
manifest creation, archive validation, and atomic restoration.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from aarkib.extensions import db
from aarkib.models import MediaItem
from aarkib.services.events import (
    EVENT_BACKUP_FINISHED,
    EVENT_BACKUP_STARTED,
    event_bus,
)

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"
DATABASE_FILENAME = "database.sqlite"


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file in 1MB chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(1048576):
            h.update(chunk)
    return h.hexdigest()


def get_backup_dir(app: Flask) -> Path:
    """Returns the configured backup directory, creating it if needed."""
    raw_dir = (
        app.config.get("BACKUP_DIR")
        or app.config.get("BACKUPS_DIR")
        or Path(app.config.get("DATA_DIR", "data")) / "backups"
    )
    backup_dir = Path(raw_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _get_sqlite_db_path(app: Flask) -> Path | None:
    """Resolves the physical SQLite file path from the database URI, or None if in-memory."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite:///"):
        return None
    db_part = uri.replace("sqlite:///", "")
    if db_part == ":memory:" or not db_part:
        return None
    return Path(db_part).resolve()


def create_backup(
    app: Flask,
    output_path: Path | None = None,
    include_covers: bool = True,
    **kwargs: Any,
) -> Path:
    """Creates a full backup archive of the Aarkib database and cover art.

    Uses the SQLite online backup API to take a hot, non-locking, crash-consistent
    snapshot while the server remains online.

    Returns the Path to the created .zip backup archive.
    """
    with app.app_context():
        backup_dir = get_backup_dir(app)
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
        if output_path is None:
            archive_path = backup_dir / f"aarkib-backup-{timestamp}.zip"
            counter = 1
            while archive_path.exists():
                archive_path = backup_dir / f"aarkib-backup-{timestamp}-{counter}.zip"
                counter += 1
        else:
            archive_path = Path(output_path)
            archive_path.parent.mkdir(parents=True, exist_ok=True)

        event_bus.emit(
            EVENT_BACKUP_STARTED,
            {"archive_path": str(archive_path), "filename": archive_path.name},
        )

        try:
            covers_dir = Path(app.config.get("COVERS_DIR", "data/covers"))
            media_count = db.session.scalar(select(func.count(MediaItem.id))) or 0

            # Create temporary working directory for the hot snapshot
            with tempfile.TemporaryDirectory(prefix="aarkib_backup_") as tmpdir:
                tmp_path = Path(tmpdir)
                temp_db_path = tmp_path / DATABASE_FILENAME

                # Perform atomic SQLite hot backup
                raw_conn = db.engine.raw_connection()
                try:
                    # Use connection.driver_connection (SQLAlchemy 2.0) or fallback to .connection
                    sqlite_source = getattr(raw_conn, "driver_connection", None)
                    if sqlite_source is None:
                        sqlite_source = getattr(raw_conn, "connection", None)

                    dest_conn = sqlite3.connect(str(temp_db_path))
                    try:
                        if hasattr(sqlite_source, "backup"):
                            sqlite_source.backup(dest_conn)
                        else:
                            # Fallback for file-based DB if raw driver connection doesn't expose backup
                            src_db_path = _get_sqlite_db_path(app)
                            if src_db_path and src_db_path.is_file():
                                shutil.copy2(src_db_path, temp_db_path)
                            else:
                                raise RuntimeError(
                                    "Unable to snapshot database: backup API unavailable"
                                )
                    finally:
                        dest_conn.close()
                finally:
                    raw_conn.close()

                db_checksum = _compute_sha256(temp_db_path)
                covers_count = 0

                # Write archive
                with zipfile.ZipFile(
                    archive_path, "w", compression=zipfile.ZIP_DEFLATED
                ) as zf:
                    # 1. Add database
                    zf.write(temp_db_path, arcname=DATABASE_FILENAME)

                    # 2. Add covers if requested and directory exists
                    if include_covers and covers_dir.is_dir():
                        for cover_file in covers_dir.iterdir():
                            if cover_file.is_file() and not cover_file.name.startswith(
                                "."
                            ):
                                zf.write(
                                    cover_file, arcname=f"covers/{cover_file.name}"
                                )
                                covers_count += 1

                    # 3. Create manifest
                    manifest = {
                        "app_name": "Aarkib",
                        "app_version": "0.1.0",
                        "backup_format_version": 1,
                        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
                        "database_checksum": f"sha256:{db_checksum}",
                        "media_count": int(media_count),
                        "covers_count": covers_count,
                        "includes_covers": include_covers,
                    }
                    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
                    zf.writestr(MANIFEST_FILENAME, manifest_bytes)

            logger.info(
                "Created Aarkib backup '%s' (items: %d, covers: %d)",
                archive_path.name,
                media_count,
                covers_count,
            )
            prune_backups(app)
            size_mb = (
                round(archive_path.stat().st_size / (1024 * 1024), 2)
                if archive_path.exists()
                else 0.0
            )
            event_bus.emit(
                EVENT_BACKUP_FINISHED,
                {
                    "success": True,
                    "archive_path": str(archive_path),
                    "filename": archive_path.name,
                    "size_mb": size_mb,
                    "media_count": media_count,
                },
            )
            return archive_path
        except Exception as exc:
            event_bus.emit(
                EVENT_BACKUP_FINISHED,
                {
                    "success": False,
                    "archive_path": str(archive_path),
                    "filename": archive_path.name,
                    "error": str(exc),
                },
            )
            raise


def validate_backup(archive_path: Path | str) -> tuple[bool, str, dict[str, Any]]:
    """Validates the structure, manifest, and SQLite database integrity of a backup archive.

    Returns:
        (is_valid, message, manifest_dict)
    """
    path = Path(archive_path)
    if not path.is_file():
        return False, f"Backup file not found: {path}", {}

    if not zipfile.is_zipfile(path):
        return False, "File is not a valid ZIP archive", {}

    try:
        with zipfile.ZipFile(path, "r") as zf:
            namelist = set(zf.namelist())

            if MANIFEST_FILENAME not in namelist:
                return False, f"Missing {MANIFEST_FILENAME} in backup archive", {}

            if DATABASE_FILENAME not in namelist:
                return False, f"Missing {DATABASE_FILENAME} in backup archive", {}

            # Parse and validate manifest
            manifest_data = json.loads(zf.read(MANIFEST_FILENAME).decode("utf-8"))
            if not isinstance(manifest_data, dict):
                return False, "Invalid manifest: expected JSON object", {}

            if manifest_data.get("app_name") != "Aarkib":
                return (
                    False,
                    f"Unsupported application backup: {manifest_data.get('app_name')}",
                    manifest_data,
                )

            # Test database integrity
            with tempfile.TemporaryDirectory(prefix="aarkib_val_") as val_dir:
                extracted_db = Path(val_dir) / DATABASE_FILENAME
                with open(extracted_db, "wb") as f_out:
                    f_out.write(zf.read(DATABASE_FILENAME))

                # Verify checksum if present
                expected_cksum = manifest_data.get("database_checksum")
                if expected_cksum and expected_cksum.startswith("sha256:"):
                    actual_cksum = f"sha256:{_compute_sha256(extracted_db)}"
                    if actual_cksum != expected_cksum:
                        return (
                            False,
                            "Database checksum mismatch (possible corruption)",
                            manifest_data,
                        )

                # Run SQLite PRAGMA integrity_check
                conn = sqlite3.connect(str(extracted_db))
                try:
                    cursor = conn.cursor()
                    cursor.execute("PRAGMA integrity_check;")
                    result = cursor.fetchone()
                    if not result or result[0] != "ok":
                        return (
                            False,
                            f"SQLite integrity check failed: {result}",
                            manifest_data,
                        )
                finally:
                    conn.close()

            return True, "Backup archive is valid and verified", manifest_data

    except Exception as exc:
        logger.error("Error validating backup archive %s: %s", path, exc)
        return False, f"Validation error: {exc}", {}


def restore_backup(
    app: Flask,
    archive_path: Path | str,
) -> tuple[bool, str]:
    """Restores the database and cover art from a validated backup archive.

    A safety copy of the current database is saved before overwriting.
    """
    path = Path(archive_path)
    is_valid, msg, manifest = validate_backup(path)
    if not is_valid:
        return False, f"Restore aborted: {msg}"

    with app.app_context():
        target_db_path = _get_sqlite_db_path(app)
        if target_db_path is None:
            return False, "Cannot restore into in-memory or non-file SQLite database"

        covers_dir = Path(app.config.get("COVERS_DIR", "data/covers"))
        covers_dir.mkdir(parents=True, exist_ok=True)

        # 1. Dispose active engine connections to release file locks
        db.session.remove()
        db.engine.dispose()

        # 2. Make safety copy of existing database if present
        if target_db_path.is_file():
            safety_copy = target_db_path.with_suffix(".pre_restore.bak")
            try:
                shutil.copy2(target_db_path, safety_copy)
                logger.info("Saved pre-restore safety copy to %s", safety_copy)
            except Exception as e:
                logger.warning("Could not create pre-restore database copy: %s", e)

        # 3. Extract and overwrite database and covers
        try:
            with zipfile.ZipFile(path, "r") as zf:
                # Write database file
                with open(target_db_path, "wb") as f_out:
                    f_out.write(zf.read(DATABASE_FILENAME))

                # Extract covers
                for item in zf.namelist():
                    if item.startswith("covers/") and len(item) > len("covers/"):
                        filename = Path(item).name
                        if filename and not filename.startswith("."):
                            dest_file = covers_dir / filename
                            with open(dest_file, "wb") as f_out:
                                f_out.write(zf.read(item))

            # 4. Run automatic migrations and rebuild FTS
            from aarkib import migrate_database
            from aarkib.services.search import rebuild_search_index

            migrate_database()
            rebuild_search_index()

            logger.info("Successfully restored backup '%s'", path.name)
            return (
                True,
                f"Successfully restored backup from {manifest.get('created_at', path.name)}",
            )

        except Exception as exc:
            logger.critical("Fatal error during backup restore: %s", exc, exc_info=True)
            # Attempt rollback if safety copy exists
            safety_copy = target_db_path.with_suffix(".pre_restore.bak")
            if safety_copy.is_file():
                try:
                    shutil.copy2(safety_copy, target_db_path)
                    logger.info("Rolled back database to pre-restore safety copy")
                except Exception as rb_exc:
                    logger.critical("Failed to rollback safety copy: %s", rb_exc)
            return False, f"Restore failed and rolled back: {exc}"


def list_backups(app: Flask) -> list[dict[str, Any]]:
    """Returns a list of all backup archives sorted by creation time descending."""
    backup_dir = get_backup_dir(app)
    results = []

    for file in sorted(backup_dir.glob("*.zip"), key=os.path.getmtime, reverse=True):
        if not file.is_file():
            continue

        stat = file.stat()
        info: dict[str, Any] = {
            "filename": file.name,
            "path": str(file),
            "size_bytes": stat.st_size,
            "modified_at": datetime.datetime.fromtimestamp(
                stat.st_mtime, datetime.UTC
            ).isoformat(),
            "manifest": None,
            "is_valid": False,
        }

        try:
            if zipfile.is_zipfile(file):
                with zipfile.ZipFile(file, "r") as zf:
                    if MANIFEST_FILENAME in zf.namelist():
                        info["manifest"] = json.loads(
                            zf.read(MANIFEST_FILENAME).decode("utf-8")
                        )
                        info["is_valid"] = True
        except Exception:
            pass

        results.append(info)

    return results


def delete_backup(app: Flask, filename: str) -> bool:
    """Deletes a backup file by filename from BACKUP_DIR."""
    from aarkib.routes.api import is_safe_media_path

    backup_dir = get_backup_dir(app)
    target = (backup_dir / filename).resolve()

    if not is_safe_media_path(target) or not target.is_file():
        return False

    try:
        target.unlink()
        logger.info("Deleted backup '%s'", filename)
        return True
    except OSError as e:
        logger.error("Failed to delete backup '%s': %s", filename, e)
        return False


def prune_backups(app: Flask, max_count: int | None = None) -> int:
    """Prunes oldest backup archives if total backups exceed max_count.

    Returns the number of pruned backup archives.
    """
    if max_count is None:
        raw_val = app.config.get("BACKUP_RETENTION_COUNT", 7)
        try:
            max_count = int(raw_val)
        except ValueError, TypeError:
            max_count = 7

    if max_count <= 0:
        return 0

    backups = list_backups(app)
    if len(backups) <= max_count:
        return 0

    excess = backups[max_count:]
    pruned_count = 0
    for b in excess:
        filename = b["filename"]
        if delete_backup(app, filename):
            pruned_count += 1
            logger.info(
                "Retention policy pruned backup '%s' (retaining %d)",
                filename,
                max_count,
            )

    return pruned_count
