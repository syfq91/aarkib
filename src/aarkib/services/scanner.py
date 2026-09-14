"""Library scanning and directory indexing supervisor."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from aarkib.extensions import db
from aarkib.models import Book, Library
from aarkib.services.indexer import (
    DEFAULT_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    _assign_authors_tags_series,
    _extract_and_generate_cover,
    _resolve_media_type,
    compute_fast_fingerprint,
    compute_sha256,
    compute_sort_title,
    get_supported_extensions,
    index_media_file,
    index_single_book,
)
from aarkib.services.library_service import (
    count_books_in_library,
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
)
from aarkib.services.watcher import (
    DebouncedLibraryChangeHandler,
    LibraryChangeHandler,
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
    "LibraryChangeHandler",
    "_assign_authors_tags_series",
    "_extract_and_generate_cover",
    "_resolve_media_type",
    "compute_fast_fingerprint",
    "compute_sha256",
    "compute_sort_title",
    "count_books_in_library",
    "count_media_in_library",
    "generate_slug",
    "get_library_definitions",
    "get_library_dirs",
    "get_library_dirs_from_config",
    "get_media_dirs_from_config",
    "get_supported_extensions",
    "index_media_file",
    "index_single_book",
    "library_path_conditions",
    "path_match_filter",
    "path_prefixes",
    "resolve_library",
    "scan_library",
    "start_library_watcher",
    "stop_library_watcher",
    "sync_and_get_libraries",
]


def scan_library(
    app: Flask,
    library_id: str | int | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    **kwargs: Any,
) -> dict[str, int]:
    """Scans configured library directories for changes. Supports scanning a specific library."""
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        auto_enrich = app.config.get("AUTO_ENRICH", False)
        covers_dir.mkdir(parents=True, exist_ok=True)

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
        visited_dirs: set[tuple[int, int]] = set()
        for lib in libraries:
            lib_dir = Path(lib.path).expanduser()
            if not lib_dir.exists():
                lib_dir.mkdir(parents=True, exist_ok=True)
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
                        candidate_files.append((lib, file_path))

        total_candidates = len(candidate_files)
        if progress_callback:
            progress_callback(
                10.0, f"Discovered {total_candidates} files. Processing..."
            )

        added = 0
        existing_files: set[str] = set()
        new_item_ids: list[int] = []

        for idx, (lib, file_path) in enumerate(candidate_files):
            if cancel_event is not None and cancel_event.is_set():
                logger.info("scan_library cancelled during file indexing")
                db.session.commit()
                return {
                    "scanned": len(existing_files),
                    "added_or_updated": added,
                    "deleted": 0,
                    "cancelled": 1,
                }

            existing_files.add(str(file_path.resolve()))
            lib_auto_enrich = (
                lib.auto_enrich
                if getattr(lib, "auto_enrich", None) is not None
                else auto_enrich
            )
            lib_provider = getattr(lib, "metadata_provider", None)

            book = index_single_book(
                file_path,
                covers_dir,
                auto_enrich=lib_auto_enrich,
                library_media_type=lib.media_type,
                library_id=getattr(lib, "id", None),
                metadata_provider=lib_provider,
                commit=False,
            )
            if book:
                if book.id is None:
                    db.session.flush()
                new_item_ids.append(book.id)
                added += 1

            if progress_callback and (idx % 5 == 0 or idx == total_candidates - 1):
                db.session.commit()
                pct = 10.0 + ((idx + 1) / max(total_candidates, 1)) * 80.0
                progress_callback(
                    pct, f"Indexing {file_path.name} ({idx + 1}/{total_candidates})"
                )
            elif (idx + 1) % 100 == 0:
                db.session.commit()

        db.session.commit()

        # Clean up deleted files from DB
        if cancel_event is not None and cancel_event.is_set():
            logger.info("scan_library cancelled before pruning")
            return {
                "scanned": len(existing_files),
                "added_or_updated": added,
                "deleted": 0,
                "cancelled": 1,
            }

        if progress_callback:
            progress_callback(92.0, "Pruning removed records...")

        deleted = 0
        if library_id is not None:
            for lib in libraries:
                p_res, p_raw = library_path_conditions(lib)
                lib_cond = or_(
                    Book.library_id == lib.id if getattr(lib, "id", None) else False,
                    Book.original_file_path.startswith(p_res),
                    Book.original_file_path.startswith(p_raw),
                )
                lib_books = db.session.scalars(select(Book).where(lib_cond)).all()
                for book in lib_books:
                    if (
                        book.original_file_path not in existing_files
                        and not Path(book.original_file_path).exists()
                    ):
                        db.session.delete(book)
                        deleted += 1
        else:
            all_books = db.session.scalars(select(Book)).all()
            for book in all_books:
                if (
                    book.original_file_path not in existing_files
                    and not Path(book.original_file_path).exists()
                ):
                    db.session.delete(book)
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

        logger.info("Library scan complete: %d indexed, %d deleted.", added, deleted)
        return {
            "scanned": len(existing_files),
            "added_or_updated": added,
            "deleted": deleted,
        }
