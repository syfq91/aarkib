from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from aarkib.config import (
    NAMED_DIR_REGEX,
    Config,
    get_env_library_dirs,
    split_path_string,
)
from aarkib.extensions import db
from aarkib.models import Author, Book, Series, Tag
from aarkib.services.parsers.base import extract_metadata_from_file
from aarkib.services.thumbnail import generate_cover_webp

if TYPE_CHECKING:
    from typing import Any

    from flask import Flask

from aarkib.plugins import plugin_registry

logger = logging.getLogger(__name__)

DEFAULT_EXTENSIONS = {".epub", ".cbz", ".zip", ".cbr"}


def get_supported_extensions() -> set[str]:
    """Returns the set of all media file extensions supported by active plugins."""
    registered = plugin_registry.get_all_supported_extensions()
    return registered if registered else DEFAULT_EXTENSIONS


SUPPORTED_EXTENSIONS = DEFAULT_EXTENSIONS


def compute_sha256(file_path: Path, chunk_size: int = 65536) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_library_dirs(app: Flask | None = None) -> list[Path]:
    """Resolves one or more library directories from app config and environment variables."""
    raw_candidates: list[Any] = []

    if app is not None:
        raw_dirs = app.config.get("LIBRARY_DIRS")
        raw_dir = app.config.get("LIBRARY_DIR")

        if raw_dirs is not None and raw_dirs != Config.LIBRARY_DIRS:
            if isinstance(raw_dirs, (list, tuple, set)):
                raw_candidates.extend(raw_dirs)
            else:
                raw_candidates.append(raw_dirs)

        if raw_dir is not None and raw_dir != Config.LIBRARY_DIR:
            if isinstance(raw_dir, (list, tuple, set)):
                raw_candidates.extend(raw_dir)
            else:
                raw_candidates.append(raw_dir)

    # Check explicitly defined environment variables
    env_paths = get_env_library_dirs()
    if env_paths:
        raw_candidates.extend(env_paths)

    # If neither app config override nor explicit env vars were found
    if not raw_candidates:
        if app is not None:
            raw = app.config.get("LIBRARY_DIRS") or app.config.get("LIBRARY_DIR")
            if raw is not None:
                if isinstance(raw, (list, tuple, set)):
                    raw_candidates.extend(raw)
                else:
                    raw_candidates.append(raw)
            else:
                data_dir = app.config.get("DATA_DIR", "data")
                raw_candidates.append(Path(data_dir) / "books")
        else:
            raw_candidates.append(Path("data/books"))

    # Parse and deduplicate
    final_paths: list[Path] = []
    seen: set[str] = set()

    for item in raw_candidates:
        if isinstance(item, Path):
            path_strs = [str(item)]
        elif isinstance(item, str):
            path_strs = split_path_string(item)
        elif isinstance(item, (list, tuple, set)):
            path_strs = []
            for sub in item:
                if isinstance(sub, Path):
                    path_strs.append(str(sub))
                elif isinstance(sub, str):
                    path_strs.extend(split_path_string(sub))
        else:
            path_strs = [str(item)]

        for p_str in path_strs:
            if not p_str or not p_str.strip():
                continue
            path_obj = Path(p_str).expanduser()
            try:
                norm_key = str(path_obj.resolve())
            except Exception:
                norm_key = str(path_obj)

            if norm_key not in seen:
                seen.add(norm_key)
                final_paths.append(path_obj)

    if not final_paths:
        fallback = (
            Path(app.config.get("DATA_DIR", "data")) / "books"
            if app is not None
            else Path("data/books")
        )
        return [fallback]

    return final_paths


def get_library_definitions(app: Flask | None = None) -> list[dict[str, Any]]:
    """Resolves all configured library definitions with human-friendly metadata and display names."""
    dirs = get_library_dirs(app)
    if not dirs:
        return []

    # Discover explicit named environment variables (e.g. AARKIB_LIBRARY_DIR_MANGA)
    named_map: dict[str, str] = {}
    for k, v in os.environ.items():
        m = NAMED_DIR_REGEX.match(k)
        if m:
            suffix = m.group(1)
            if not suffix.isdigit():
                display_name = suffix.replace("_", " ").title()
                for p_str in split_path_string(v):
                    if not p_str.strip():
                        continue
                    try:
                        norm = str(Path(p_str).expanduser().resolve())
                        named_map[norm] = display_name
                    except Exception:
                        pass

    # Also check app config for custom library names if defined (e.g. app.config["LIBRARY_NAMES"])
    if app is not None:
        cfg_names = app.config.get("LIBRARY_NAMES")
        if isinstance(cfg_names, dict):
            for k, v in cfg_names.items():
                try:
                    norm = str(Path(k).expanduser().resolve())
                    named_map[norm] = str(v)
                except Exception:
                    pass

    definitions: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}
    seen_names: dict[str, int] = {}

    for idx, p in enumerate(dirs):
        try:
            p_resolved = p.resolve()
            p_str = str(p_resolved)
        except Exception:
            p_resolved = p
            p_str = str(p)

        # Determine friendly display name
        if p_str in named_map:
            name = named_map[p_str]
        else:
            folder_name = p.name
            if not folder_name or folder_name in (".", "/", "data"):
                name = "Books" if len(dirs) == 1 else f"Library {idx + 1}"
            else:
                name = folder_name.replace("_", " ").replace("-", " ").title()

        # Disambiguate duplicate names
        if name in seen_names:
            seen_names[name] += 1
            name = f"{name} ({seen_names[name]})"
        else:
            seen_names[name] = 1

        # Generate slug ID
        base_id = (
            re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-") or f"lib-{idx + 1}"
        )
        if base_id in seen_ids:
            seen_ids[base_id] += 1
            lib_id = f"{base_id}-{seen_ids[base_id]}"
        else:
            seen_ids[base_id] = 1
            lib_id = base_id

        definitions.append(
            {
                "id": lib_id,
                "name": name,
                "path": p_resolved,
                "path_str": p_str,
            }
        )

    return definitions


def index_single_book(
    file_path: Path,
    covers_dir: Path,
    auto_enrich: bool = False,
) -> Book | None:
    """Parses and updates or inserts a single book record in the database."""
    supported = get_supported_extensions()
    if file_path.suffix.lower() not in supported or not file_path.is_file():
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

        plugin = plugin_registry.get_plugin_for_extension(file_path.suffix)
        if plugin:
            metadata = plugin.parse_metadata(file_path)
        else:
            metadata = extract_metadata_from_file(file_path)

        if not metadata:
            return None

        # Cover processing
        cover_rel_path = None
        cover_bytes = metadata.cover_bytes
        if not cover_bytes and plugin:
            cover_bytes = plugin.extract_cover(file_path)

        if cover_bytes:
            cover_filename = f"{file_hash[:16]}.webp"
            cover_output_path = covers_dir / cover_filename
            if generate_cover_webp(cover_bytes, cover_output_path):
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
        book.isbn = getattr(metadata, "isbn", None)
        book.publication_date = metadata.publication_date
        book.page_count = getattr(metadata, "page_count", None)
        book.media_type = getattr(metadata, "media_type", None) or (
            "comic" if metadata.file_format in ("cbz", "cbr", "zip") else "book"
        )
        if cover_rel_path:
            book.cover_image_path = cover_rel_path

        # Handle Authors / Creators
        author_objs = []
        authors_list = getattr(metadata, "authors", None) or getattr(
            metadata, "creators", []
        )
        for author_name in authors_list:
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
                from aarkib.services.enricher import enrich_book

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
        supported = get_supported_extensions()

        for lib_dir in library_dirs:
            for root, _, filenames in os.walk(lib_dir, followlinks=True):
                for filename in filenames:
                    file_path = Path(root) / filename
                    if file_path.suffix.lower() in supported:
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
        if file_path.suffix.lower() in get_supported_extensions():
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
