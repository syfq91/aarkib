"""Filesystem watcher with debounced event handling for library directories."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from aarkib.extensions import db
from aarkib.models import MediaItem
from aarkib.services.indexer import get_supported_extensions, index_media_file
from aarkib.services.library_service import (
    get_library_dirs,
    library_path_conditions,
    sync_and_get_libraries,
)

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)


class DebouncedLibraryChangeHandler(FileSystemEventHandler):
    """Watches library directories and indexes or deletes items with a debounce timer."""

    def __init__(self, app: Flask, debounce_seconds: float | None = None):
        super().__init__()
        self.app = app
        if debounce_seconds is None:
            if app.config.get("TESTING"):
                self.debounce_seconds = 0.0
            else:
                self.debounce_seconds = float(
                    app.config.get("WATCHER_DEBOUNCE_SECONDS", 1.0)
                )
        else:
            self.debounce_seconds = float(debounce_seconds)

        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _schedule_event(self, path_str: str) -> None:
        file_path = Path(path_str)
        if file_path.suffix.lower() not in get_supported_extensions():
            return

        if self.debounce_seconds <= 0:
            self._trigger_index(path_str)
            return

        with self._lock:
            existing_timer = self._timers.get(path_str)
            if existing_timer:
                existing_timer.cancel()

            timer = threading.Timer(
                self.debounce_seconds,
                self._trigger_index,
                args=[path_str],
            )
            self._timers[path_str] = timer
            timer.daemon = True
            timer.start()

    def _trigger_index(self, path_str: str) -> None:
        with self._lock:
            self._timers.pop(path_str, None)

        file_path = Path(path_str)
        try:
            with self.app.app_context():
                covers_dir = Path(self.app.config.get("COVERS_DIR", "data/covers"))
                if file_path.exists():
                    libraries = sync_and_get_libraries(self.app)
                    matched_lib = None
                    for lib in libraries:
                        p_res, p_raw = library_path_conditions(lib)
                        res_p = str(file_path.resolve())
                        if res_p.startswith(p_res) or str(file_path).startswith(p_raw):
                            matched_lib = lib
                            break
                    item = index_media_file(
                        file_path,
                        covers_dir,
                        library_media_type=matched_lib.media_type
                        if matched_lib
                        else None,
                        library_id=getattr(matched_lib, "id", None)
                        if matched_lib
                        else None,
                    )
                    if item:
                        from aarkib.services.search import sync_media_item_fts

                        sync_media_item_fts(item.id)
                else:
                    try:
                        resolved_path = str(file_path.resolve())
                    except Exception:
                        resolved_path = str(file_path)
                    item = db.session.scalar(
                        select(MediaItem).where(
                            MediaItem.original_file_path == resolved_path
                        )
                    )
                    if item:
                        item_id = item.id
                        db.session.delete(item)
                        db.session.commit()
                        from aarkib.services.search import remove_media_item_fts

                        remove_media_item_fts(item_id)
        except Exception as exc:
            logger.error("Error processing watcher event for %s: %s", path_str, exc)

    def cancel_all(self) -> None:
        """Cancel all pending debounce timers immediately."""
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule_event(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule_event(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule_event(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule_event(event.src_path)
            if hasattr(event, "dest_path"):
                self._schedule_event(event.dest_path)


def start_library_watcher(app: Flask) -> Observer | None:
    """Starts a background filesystem observer on all configured library directories."""
    if not app.config.get("WATCH_LIBRARY", True):
        return None

    # Stop any existing watcher first
    existing = (
        app.extensions.get("library_watcher") if hasattr(app, "extensions") else None
    )
    if existing and existing.is_alive():
        try:
            handler = getattr(app, "_library_change_handler", None)
            if handler and hasattr(handler, "cancel_all"):
                handler.cancel_all()
            existing.stop()
            existing.join(timeout=2.0)
        except Exception as e:
            logger.debug("Error stopping existing library watcher: %s", e)

    library_dirs = get_library_dirs(app)
    event_handler = DebouncedLibraryChangeHandler(app)
    app._library_change_handler = event_handler
    observer = Observer()
    for lib_dir in library_dirs:
        lib_dir.mkdir(parents=True, exist_ok=True)
        observer.schedule(event_handler, str(lib_dir), recursive=True)
    observer.daemon = True
    observer.start()
    if hasattr(app, "extensions"):
        app.extensions["library_watcher"] = observer
    logger.info(
        "Library watcher started for %s", ", ".join(str(d) for d in library_dirs)
    )
    return observer


def stop_library_watcher(app: Flask) -> None:
    """Stops the active background library watcher if running."""
    handler = getattr(app, "_library_change_handler", None)
    if handler and hasattr(handler, "cancel_all"):
        handler.cancel_all()
        app._library_change_handler = None

    if hasattr(app, "extensions"):
        observer = app.extensions.get("library_watcher")
        if observer and observer.is_alive():
            try:
                observer.stop()
                observer.join(timeout=2.0)
                logger.info("Library watcher stopped.")
            except Exception as e:
                logger.warning("Error stopping library watcher: %s", e)
        app.extensions["library_watcher"] = None
