"""Library scanning and directory indexing supervisor."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from aarkib.extensions import db
from aarkib.models import Library, MediaItem
from aarkib.services.events import (
    EVENT_MEDIA_ADDED,
    EVENT_SCAN_FINISHED,
    EVENT_SCAN_PROGRESS,
    EVENT_SCAN_STARTED,
    event_bus,
)
from aarkib.services.indexer import (
    DEFAULT_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    _assign_creators_tags_collections,
    _extract_and_generate_cover,
    _resolve_media_type,
    compute_fast_fingerprint,
    compute_sha256,
    compute_sort_title,
    get_supported_extensions,
    index_media_file,
)
from aarkib.services.library_service import (
    count_media_in_library,
    generate_slug,
    get_library_definitions,
    get_library_dirs,
    get_library_dirs_from_config,
    get_media_dirs_from_config,
    library_path_conditions,
    path_match_filter,
    path_prefixes,
    resolve_library,
    sync_and_get_libraries,
    validate_library_availability,
)
from aarkib.services.watcher import (
    DebouncedLibraryChangeHandler,
    start_library_watcher,
    stop_library_watcher,
)

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_EXTENSIONS",
    "SUPPORTED_EXTENSIONS",
    "DebouncedLibraryChangeHandler",
    "_assign_creators_tags_collections",
    "_extract_and_generate_cover",
    "_resolve_media_type",
    "compute_fast_fingerprint",
    "compute_sha256",
    "compute_sort_title",
    "count_media_in_library",
    "generate_slug",
    "get_library_definitions",
    "get_library_dirs",
    "get_library_dirs_from_config",
    "get_media_dirs_from_config",
    "get_supported_extensions",
    "index_media_file",
    "library_path_conditions",
    "path_match_filter",
    "path_prefixes",
    "resolve_library",
    "scan_library",
    "start_library_watcher",
    "stop_library_watcher",
    "sync_and_get_libraries",
    "validate_library_availability",
]


def scan_library(
    app: Flask,
    library_id: str | int | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    force_prune: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Scans configured library directories for changes. Supports scanning a specific library.

    Enforces mount-safe library availability verification to prevent data loss on unmounted
    storage or network shares. Reconciles state using a three-way sync pipeline (NEW, CHANGED,
    MISSING).
    """
    start_time = time.time()
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        auto_enrich = app.config.get("AUTO_ENRICH", False)
        covers_dir.mkdir(parents=True, exist_ok=True)

        event_bus.emit(EVENT_SCAN_STARTED, {"library_id": library_id})
        if progress_callback:
            progress_callback(5.0, "Discovering media files in library folders...")

        libraries = sync_and_get_libraries(app)
        if library_id is not None:
            if isinstance(library_id, int) or (
                isinstance(library_id, str) and library_id.isdigit()
            ):
                libraries = [lib for lib in libraries if lib.id == int(library_id)]
            else:
                libraries = [lib for lib in libraries if lib.slug == str(library_id)]

        if not libraries:
            fallback_dirs = get_library_dirs_from_config(app)
            libraries = [
                Library(
                    slug="default",
                    name="Media",
                    path=str(d),
                    media_type="all",
                )
                for d in fallback_dirs
            ]

        supported = get_supported_extensions()
        candidate_files: list[tuple[Library, Path]] = []
        safe_libraries: list[Library] = []
        aborted_libraries: list[str] = []
        visited_dirs: set[tuple[int, int]] = set()

        for lib in libraries:
            lib_ident = lib.slug or str(getattr(lib, "id", None) or lib.name)
            if not validate_library_availability(lib, force_prune=force_prune):
                logger.warning(
                    "Library '%s' (id=%s, path=%s) failed availability check; aborting reconciliation.",
                    getattr(lib, "name", "unknown"),
                    getattr(lib, "id", None),
                    lib.path,
                )
                aborted_libraries.append(lib_ident)
                continue

            lib_dir = Path(lib.path).expanduser()
            lib_candidate_files: list[tuple[Library, Path]] = []
            for root, dirs, filenames in os.walk(lib_dir, followlinks=True):
                try:
                    dir_stat = Path(root).stat()
                    dir_key = (dir_stat.st_dev, dir_stat.st_ino)
                    if dir_key in visited_dirs:
                        dirs.clear()
                        continue
                    visited_dirs.add(dir_key)
                except OSError:
                    continue
                for filename in filenames:
                    file_path = Path(root) / filename
                    if file_path.suffix.lower() in supported:
                        lib_candidate_files.append((lib, file_path))

            if not validate_library_availability(
                lib,
                candidate_count=len(lib_candidate_files),
                force_prune=force_prune,
            ):
                logger.warning(
                    "Library '%s' (id=%s, path=%s) failed post-walk candidate check; aborting reconciliation.",
                    getattr(lib, "name", "unknown"),
                    getattr(lib, "id", None),
                    lib.path,
                )
                aborted_libraries.append(lib_ident)
                continue

            safe_libraries.append(lib)
            candidate_files.extend(lib_candidate_files)

        if not safe_libraries and aborted_libraries:
            duration_seconds = round(time.time() - start_time, 2)
            logger.warning(
                "Library scan aborted: all target libraries failed availability checks. Zero database records modified. Aborted: %s",
                aborted_libraries,
            )
            abort_res: dict[str, Any] = {
                "scanned": 0,
                "added": 0,
                "updated": 0,
                "added_or_updated": 0,
                "deleted": 0,
                "aborted_libraries": aborted_libraries,
                "duration_seconds": duration_seconds,
            }
            event_bus.emit(EVENT_SCAN_FINISHED, {**abort_res, "library_id": library_id})
            return abort_res

        total_candidates = len(candidate_files)
        if progress_callback:
            progress_callback(
                10.0, f"Discovered {total_candidates} files. Processing..."
            )

        # Snapshot of existing records in DB for three-way comparison
        existing_records: dict[str, tuple[int, int | None, float | None]] = {
            row[0]: (row[1], row[2], row[3])
            for row in db.session.execute(
                select(
                    MediaItem.original_file_path,
                    MediaItem.id,
                    MediaItem.file_size,
                    MediaItem.file_mtime,
                )
            ).all()
        }

        added = 0
        updated = 0
        existing_files: set[str] = set()
        new_item_ids: list[int] = []

        for idx, (lib, file_path) in enumerate(candidate_files):
            if cancel_event is not None and cancel_event.is_set():
                logger.info("scan_library cancelled during file indexing")
                db.session.commit()
                duration_seconds = round(time.time() - start_time, 2)
                return {
                    "scanned": len(existing_files),
                    "added": added,
                    "updated": updated,
                    "added_or_updated": added + updated,
                    "deleted": 0,
                    "aborted_libraries": aborted_libraries,
                    "cancelled": 1,
                    "duration_seconds": duration_seconds,
                }

            try:
                resolved_path = str(file_path.resolve())
            except Exception:
                resolved_path = str(file_path)

            existing_files.add(resolved_path)

            record_info = existing_records.get(resolved_path)
            is_new = record_info is None
            is_changed = False

            if not is_new:
                _, prev_size, prev_mtime = record_info
                try:
                    st = file_path.stat()
                    if (
                        prev_size != st.st_size
                        or prev_mtime is None
                        or abs(prev_mtime - st.st_mtime) >= 0.01
                    ):
                        is_changed = True
                except OSError:
                    pass

            lib_auto_enrich = (
                lib.auto_enrich
                if getattr(lib, "auto_enrich", None) is not None
                else auto_enrich
            )
            lib_provider = getattr(lib, "metadata_provider", None)

            item = index_media_file(
                file_path,
                covers_dir,
                auto_enrich=lib_auto_enrich,
                library_media_type=lib.media_type,
                library_id=getattr(lib, "id", None),
                metadata_provider=lib_provider,
                commit=False,
            )
            if item:
                if is_new:
                    if item.id is None:
                        db.session.flush()
                    new_item_ids.append(item.id)
                    added += 1
                    event_bus.emit(
                        EVENT_MEDIA_ADDED,
                        {
                            "id": item.id,
                            "title": item.title,
                            "media_type": item.media_type,
                            "file_format": item.file_format,
                        },
                    )
                elif is_changed:
                    new_item_ids.append(item.id)
                    updated += 1

            if progress_callback and (idx % 5 == 0 or idx == total_candidates - 1):
                db.session.commit()
                pct = 10.0 + ((idx + 1) / max(total_candidates, 1)) * 80.0
                progress_callback(
                    pct, f"Indexing {file_path.name} ({idx + 1}/{total_candidates})"
                )
                event_bus.emit(
                    EVENT_SCAN_PROGRESS,
                    {"percentage": pct, "file": file_path.name},
                )
            elif (idx + 1) % 100 == 0:
                db.session.commit()

        db.session.commit()

        # Clean up deleted files from DB
        if cancel_event is not None and cancel_event.is_set():
            logger.info("scan_library cancelled before pruning")
            duration_seconds = round(time.time() - start_time, 2)
            return {
                "scanned": len(existing_files),
                "added": added,
                "updated": updated,
                "added_or_updated": added + updated,
                "deleted": 0,
                "aborted_libraries": aborted_libraries,
                "cancelled": 1,
                "duration_seconds": duration_seconds,
            }

        if progress_callback:
            progress_callback(92.0, "Pruning removed records...")

        deleted = 0
        # Pruning is strictly restricted to safe_libraries.
        # Any library that was aborted or unmounted is NEVER pruned.
        for lib in safe_libraries:
            p_res, p_raw = library_path_conditions(lib)
            lib_cond = or_(
                MediaItem.library_id == lib.id if getattr(lib, "id", None) else False,
                MediaItem.original_file_path.startswith(p_res),
                MediaItem.original_file_path.startswith(p_raw),
            )
            lib_items = db.session.scalars(select(MediaItem).where(lib_cond)).all()
            for item in lib_items:
                if (
                    item.original_file_path not in existing_files
                    and not Path(item.original_file_path).exists()
                ):
                    db.session.delete(item)
                    deleted += 1

        if deleted > 0:
            db.session.commit()

        if new_item_ids:
            try:
                from aarkib.services.search import sync_batch_fts

                sync_batch_fts(new_item_ids)
            except Exception as e:
                logger.warning("FTS incremental sync failed after scan: %s", e)

        if progress_callback:
            progress_callback(
                100.0, f"Scan complete: {len(existing_files)} files processed"
            )

        duration_seconds = round(time.time() - start_time, 2)
        total_scanned = len(existing_files)
        logger.info(
            "Library scan complete: %d added, %d updated, %d deleted in %.2fs (%d files scanned).",
            added,
            updated,
            deleted,
            duration_seconds,
            total_scanned,
            extra={
                "event": "library_scan_complete",
                "added": added,
                "updated": updated,
                "deleted": deleted,
                "total_files": total_scanned,
                "duration_seconds": duration_seconds,
                "library_id": library_id,
                "aborted_libraries": aborted_libraries,
            },
        )
        scan_summary = {
            "scanned": total_scanned,
            "added": added,
            "updated": updated,
            "added_or_updated": added + updated,
            "deleted": deleted,
            "aborted_libraries": aborted_libraries,
            "duration_seconds": duration_seconds,
        }
        event_bus.emit(
            EVENT_SCAN_FINISHED,
            {
                **scan_summary,
                "library_id": library_id,
            },
        )
        return scan_summary
