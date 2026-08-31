from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from buukuu.extensions import db
from buukuu.models import Author, Book, Series, Tag
from buukuu.services.parsers.base import extract_metadata_from_file
from buukuu.services.thumbnail import generate_cover_webp

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".epub", ".cbz", ".zip", ".cbr"}


def compute_sha256(file_path: Path, chunk_size: int = 65536) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_library_dirs(app: Flask) -> list[Path]:
    """Resolves one or more library directories from app config."""
    raw = app.config.get("LIBRARY_DIR", "data/books")
    if isinstance(raw, (list, tuple)):
        dirs = [Path(p) for p in raw]
    elif isinstance(raw, Path):
        dirs = [raw]
    else:
        raw_str = str(raw).strip()
        if ";" in raw_str:
            parts = [p.strip() for p in raw_str.split(";") if p.strip()]
        elif "," in raw_str:
            parts = [p.strip() for p in raw_str.split(",") if p.strip()]
        elif ":" in raw_str and not (len(raw_str) > 1 and raw_str[1] == ":"):
            parts = [p.strip() for p in raw_str.split(":") if p.strip()]
        elif raw_str:
            parts = [raw_str]
        else:
            parts = ["data/books"]
        dirs = [Path(p) for p in parts]
    return dirs


def index_single_book(
    file_path: Path,
    covers_dir: Path,
    auto_enrich: bool = False,
) -> Book | None:
    """Parses and updates or inserts a single book record in the database."""
    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS or not file_path.is_file():
        return None

    try:
        resolved_path = str(file_path.resolve())
        file_size = file_path.stat().st_size
        file_hash = compute_sha256(file_path)

        existing_book = db.session.scalar(
            select(Book).where(Book.original_file_path == resolved_path)
        )
        if existing_book and existing_book.file_hash == file_hash:
            return existing_book

        metadata = extract_metadata_from_file(file_path)
        if not metadata:
            return None

        # Cover processing
        cover_rel_path = None
        if metadata.cover_bytes:
            cover_filename = f"{file_hash[:16]}.webp"
            cover_output_path = covers_dir / cover_filename
            if generate_cover_webp(metadata.cover_bytes, cover_output_path):
                cover_rel_path = cover_filename

        book = existing_book or Book(original_file_path=resolved_path)
        db.session.add(book)

        book.title = metadata.title or file_path.stem
        book.sort_title = book.title.lstrip("The ").lstrip("A ").lstrip("An ")
        book.file_format = metadata.file_format
        book.file_size = file_size
        book.file_hash = file_hash
        book.description = metadata.description
        book.publisher = metadata.publisher
        book.language = metadata.language or "en"
        book.isbn = metadata.isbn
        book.publication_date = metadata.publication_date
        book.page_count = metadata.page_count
        if cover_rel_path:
            book.cover_image_path = cover_rel_path

        # Handle Authors
        author_objs = []
        for author_name in metadata.authors:
            cleaned_name = author_name.strip()
            if not cleaned_name:
                continue
            author = db.session.scalar(
                select(Author).where(Author.name == cleaned_name)
            )
            if not author:
                author = Author(name=cleaned_name)
                db.session.add(author)
            author_objs.append(author)
        book.authors = author_objs

        # Handle Series
        if metadata.series:
            cleaned_series = metadata.series.strip()
            series_obj = db.session.scalar(
                select(Series).where(Series.name == cleaned_series)
            )
            if not series_obj:
                series_obj = Series(name=cleaned_series)
                db.session.add(series_obj)
            book.series = series_obj
            book.series_index = metadata.series_index
        else:
            book.series = None
            book.series_index = None

        # Handle Tags
        tag_objs = []
        for tag_name in metadata.tags:
            cleaned_tag = tag_name.strip().title()
            if not cleaned_tag:
                continue
            tag_obj = db.session.scalar(select(Tag).where(Tag.name == cleaned_tag))
            if not tag_obj:
                tag_obj = Tag(name=cleaned_tag)
                db.session.add(tag_obj)
            tag_objs.append(tag_obj)
        book.tags = tag_objs

        db.session.commit()
        logger.info("Indexed book: %s (%s)", book.title, book.file_format)

        # Optional auto enrichment if enabled
        if auto_enrich:
            try:
                from buukuu.services.enricher import enrich_book

                enrich_book(book, covers_dir, overwrite=False)
            except Exception as e:
                logger.debug("Auto enrich error for %s: %s", book.title, e)

        return book

    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to index %s: %s", file_path, exc)
        return None


def scan_library(app: Flask) -> dict[str, int]:
    """Scans all configured library directories (and symlinked directories) for changes."""
    with app.app_context():
        library_dirs = get_library_dirs(app)
        covers_dir = Path(app.config["COVERS_DIR"])
        auto_enrich = app.config.get("AUTO_ENRICH", False)
        for lib_dir in library_dirs:
            lib_dir.mkdir(parents=True, exist_ok=True)
        covers_dir.mkdir(parents=True, exist_ok=True)

        added = 0
        existing_files = set()

        for lib_dir in library_dirs:
            for root, _, filenames in os.walk(lib_dir, followlinks=True):
                for filename in filenames:
                    file_path = Path(root) / filename
                    if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                        existing_files.add(str(file_path.resolve()))
                        book = index_single_book(
                            file_path, covers_dir, auto_enrich=auto_enrich
                        )
                        if book:
                            added += 1

        # Clean up deleted files from DB
        all_books = db.session.scalars(select(Book)).all()
        deleted = 0
        for book in all_books:
            if (
                book.original_file_path not in existing_files
                and not Path(book.original_file_path).exists()
            ):
                db.session.delete(book)
                deleted += 1

        if deleted > 0:
            db.session.commit()

        logger.info("Library scan complete: %d indexed, %d deleted.", added, deleted)
        return {
            "scanned": len(existing_files),
            "added_or_updated": added,
            "deleted": deleted,
        }


class LibraryChangeHandler(FileSystemEventHandler):
    def __init__(self, app: Flask):
        self.app = app

    def _trigger_index(self, path_str: str) -> None:
        file_path = Path(path_str)
        if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            with self.app.app_context():
                covers_dir = Path(self.app.config["COVERS_DIR"])
                time.sleep(0.5)  # allow file write to finish
                if file_path.exists():
                    index_single_book(file_path, covers_dir)
                else:
                    book = db.session.scalar(
                        select(Book).where(
                            Book.original_file_path == str(file_path.resolve())
                        )
                    )
                    if book:
                        db.session.delete(book)
                        db.session.commit()

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._trigger_index(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._trigger_index(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._trigger_index(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._trigger_index(event.src_path)
            if hasattr(event, "dest_path"):
                self._trigger_index(event.dest_path)


def start_library_watcher(app: Flask) -> Observer | None:
    """Starts a background filesystem observer on all configured library directories."""
    if not app.config.get("WATCH_LIBRARY", True):
        return None

    library_dirs = get_library_dirs(app)
    event_handler = LibraryChangeHandler(app)
    observer = Observer()
    for lib_dir in library_dirs:
        lib_dir.mkdir(parents=True, exist_ok=True)
        observer.schedule(event_handler, str(lib_dir), recursive=True)
    observer.daemon = True
    observer.start()
    logger.info(
        "Library watcher started for %s", ", ".join(str(d) for d in library_dirs)
    )
    return observer
