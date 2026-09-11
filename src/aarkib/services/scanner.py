from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from aarkib.config import (
    NAMED_DIR_REGEX,
    Config,
    get_env_library_dirs,
    split_path_string,
)
from aarkib.extensions import db
from aarkib.models import (
    Author,
    Book,
    Library,
    Series,
    Tag,
)
from aarkib.services.media_service import (
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    library_path_conditions,
)
from aarkib.services.parsers.base import extract_metadata_from_file
from aarkib.services.thumbnail import generate_cover_webp

if TYPE_CHECKING:
    from typing import Any

    from flask import Flask

from aarkib.plugins import plugin_registry

logger = logging.getLogger(__name__)

DEFAULT_EXTENSIONS = {
    ".epub",
    ".cbz",
    ".zip",
    ".cbr",
    ".mp4",
    ".mkv",
    ".webm",
    ".avi",
    ".mov",
    ".m4v",
    ".mp3",
    ".m4a",
    ".m4b",
    ".flac",
    ".ogg",
    ".opus",
    ".wav",
    ".aac",
}


def get_supported_extensions() -> set[str]:
    """Returns the set of all media file extensions supported by active plugins."""
    registered = plugin_registry.get_all_supported_extensions()
    return registered if registered else DEFAULT_EXTENSIONS


SUPPORTED_EXTENSIONS = DEFAULT_EXTENSIONS


def compute_sort_title(title: str) -> str:
    """Returns a clean sort title by stripping leading articles (The, A, An)."""
    if not title:
        return ""
    lower = title.lower()
    for prefix in ("the ", "a ", "an "):
        if lower.startswith(prefix):
            return title[len(prefix) :].strip()
    return title


def compute_sha256(file_path: Path, chunk_size: int = 65536) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_library_dirs_from_config(app: Flask | None = None) -> list[Path]:
    """Resolves one or more library directories directly from app config and environment variables."""
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
                data_dir = Path(app.config.get("DATA_DIR", "data"))
                if (data_dir / "books").exists() and not (data_dir / "media").exists():
                    raw_candidates.append(data_dir / "books")
                else:
                    raw_candidates.append(data_dir / "media")
        else:
            if Path("data/books").exists() and not Path("data/media").exists():
                raw_candidates.append(Path("data/books"))
            else:
                raw_candidates.append(Path("data/media"))

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
        data_dir = (
            Path(app.config.get("DATA_DIR", "data"))
            if app is not None
            else Path("data")
        )
        if (data_dir / "books").exists() and not (data_dir / "media").exists():
            return [data_dir / "books"]
        return [data_dir / "media"]

    return final_paths


def sync_and_get_libraries(app: Flask | None = None) -> list[Library]:
    """Synchronizes configured environment/default directories with the database Library table.

    Returns the complete list of persisted Library records.
    """
    from sqlalchemy import inspect

    try:
        inspector = inspect(db.engine)
        if not inspector.has_table("libraries"):
            return []
    except Exception:
        return []

    try:
        existing_libs = list(
            db.session.scalars(select(Library).order_by(Library.id.asc())).all()
        )
    except Exception:
        db.session.rollback()
        return []

    # If database has no libraries yet, seed from config/env/defaults.
    # If database already has libraries, only sync any newly declared explicit env vars or custom app config dirs.
    if not existing_libs:
        target_dirs = get_library_dirs_from_config(app)
    else:
        target_dirs = get_env_library_dirs()
        if app is not None:
            custom_dirs = app.config.get("LIBRARY_DIRS")
            if custom_dirs and custom_dirs != Config.LIBRARY_DIRS:
                items = (
                    custom_dirs
                    if isinstance(custom_dirs, (list, tuple, set))
                    else [custom_dirs]
                )
                for d in items:
                    p = Path(d).expanduser() if not isinstance(d, Path) else d
                    if p not in target_dirs:
                        target_dirs.append(p)

    # Build lookup of registered paths
    existing_paths: set[str] = set()
    for lib in existing_libs:
        existing_paths.add(lib.path)
        try:
            existing_paths.add(str(Path(lib.path).expanduser().resolve()))
        except Exception:
            logger.debug(
                "Failed to resolve library path %s: %s", lib.path, exc_info=True
            )

    # Named environment map for friendly names (e.g. AARKIB_LIBRARY_DIR_MANGA)
    named_map: dict[str, str] = {}
    for k, v in os.environ.items():
        m = NAMED_DIR_REGEX.match(k)
        if m:
            suffix = m.group(1)
            if not suffix.isdigit():
                display_name = suffix.replace("_", " ").title()
                for p_str in split_path_string(v):
                    if p_str.strip():
                        try:
                            norm = str(Path(p_str).expanduser().resolve())
                            named_map[norm] = display_name
                        except Exception:
                            logger.debug(
                                "Failed to resolve named dir %s: %s",
                                p_str,
                                exc_info=True,
                            )

    has_new = False
    for p in target_dirs:
        p_expanded = p.expanduser()
        p_raw = str(p_expanded)
        try:
            p_res = str(p_expanded.resolve())
        except Exception:
            p_res = p_raw

        if p_raw in existing_paths or p_res in existing_paths:
            continue

        # Determine display name
        if p_res in named_map:
            name = named_map[p_res]
        elif p_raw in named_map:
            name = named_map[p_raw]
        else:
            folder_name = p_expanded.name
            if not folder_name or folder_name in (".", "/", "data"):
                name = (
                    "Media"
                    if not existing_libs
                    else f"Library {len(existing_libs) + 1}"
                )
            else:
                name = folder_name.replace("_", " ").replace("-", " ").title()

        # Determine default media_type based on folder/name context
        lower_name = (folder_name or name or "").lower()
        if any(w in lower_name for w in ("comic", "manga", "cbz")):
            media_type = "comic"
        elif any(
            w in lower_name for w in ("video", "movie", "film", "show", "tv", "anime")
        ):
            media_type = "video"
        elif "book" in lower_name:
            media_type = "book"
        else:
            media_type = "all"

        # Unique slug
        base_slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-") or "media"
        slug = base_slug
        c = 1
        all_slugs = {item.slug for item in existing_libs}
        while slug in all_slugs:
            c += 1
            slug = f"{base_slug}-{c}"
        all_slugs.add(slug)

        new_lib = Library(
            slug=slug,
            name=name,
            path=p_raw,
            media_type=media_type,
        )
        db.session.add(new_lib)
        existing_libs.append(new_lib)
        existing_paths.add(p_raw)
        existing_paths.add(p_res)
        has_new = True

    if has_new:
        try:
            db.session.commit()
        except Exception as exc:
            logger.warning("Error committing synced libraries: %s", exc)
            db.session.rollback()

    try:
        return list(
            db.session.scalars(select(Library).order_by(Library.id.asc())).all()
        )
    except Exception:
        return existing_libs


def get_library_dirs(app: Flask | None = None) -> list[Path]:
    """Resolves one or more library directories from app config, database, or environment variables."""
    try:
        from flask import current_app, has_app_context

        target_app = (
            app
            if app is not None
            else (current_app._get_current_object() if has_app_context() else None)
        )
        if target_app is not None:
            if has_app_context():
                libs = sync_and_get_libraries(target_app)
            else:
                with target_app.app_context():
                    libs = sync_and_get_libraries(target_app)
            if libs:
                return [Path(lib.path) for lib in libs]
    except Exception:
        logger.debug("Library directory resolution skipped: %s", exc_info=True)

    return get_library_dirs_from_config(app)


def get_library_definitions(app: Flask | None = None) -> list[dict[str, Any]]:
    """Resolves all configured library definitions with human-friendly metadata, media_type, and counts."""
    try:
        from flask import current_app, has_app_context

        target_app = (
            app
            if app is not None
            else (current_app._get_current_object() if has_app_context() else None)
        )
        if target_app is not None:
            if has_app_context():
                libs = sync_and_get_libraries(target_app)
            else:
                with target_app.app_context():
                    libs = sync_and_get_libraries(target_app)
            if libs:
                total_books = db.session.scalar(select(func.count(Book.id))) or 0
                definitions: list[dict[str, Any]] = []
                for lib in libs:
                    p = Path(lib.path).expanduser()
                    p_res, p_raw = library_path_conditions(lib)

                    lib_cond = or_(
                        Book.original_file_path.startswith(p_res),
                        Book.original_file_path.startswith(p_raw),
                        Book.original_file_path == str(p),
                    )
                    count = (
                        db.session.scalar(select(func.count(Book.id)).where(lib_cond))
                        or 0
                    )
                    if count == 0 and len(libs) == 1 and total_books > 0:
                        count = total_books

                    definitions.append(
                        {
                            "id": lib.slug,
                            "db_id": lib.id,
                            "name": lib.name,
                            "path": p,
                            "path_str": str(p),
                            "media_type": lib.media_type,
                            "count": count,
                        }
                    )
                return definitions
    except Exception as e:
        logger.debug("Database library definitions fallback: %s", e)

    # Fallback to pure config/env parsing
    dirs = get_library_dirs_from_config(app)
    definitions = []
    for idx, p in enumerate(dirs):
        folder_name = p.name
        name = (
            "Media"
            if not folder_name or folder_name in (".", "/", "data")
            else folder_name.replace("_", " ").title()
        )
        slug = (
            re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-") or f"lib-{idx + 1}"
        )
        definitions.append(
            {
                "id": slug,
                "db_id": None,
                "name": name,
                "path": p,
                "path_str": str(p),
                "media_type": "all",
                "count": 0,
            }
        )
    return definitions


def _extract_and_generate_cover(
    metadata,
    plugin,
    file_path: Path,
    file_hash: str,
    covers_dir: Path,
) -> str | None:
    """Extracts cover bytes (from metadata or plugin) and writes a WebP thumbnail."""
    cover_bytes = metadata.cover_bytes
    if not cover_bytes and plugin:
        cover_bytes = plugin.extract_cover(file_path)

    if cover_bytes:
        cover_filename = f"{file_hash[:16]}.webp"
        cover_output_path = covers_dir / cover_filename
        if generate_cover_webp(cover_bytes, cover_output_path):
            return cover_filename
    return None


def _resolve_media_type(
    metadata, resolved_path: str, library_media_type: str | None
) -> str:
    """Determines whether an item is a comic, video, or book.

    Precedence: explicit library media_type > matching configured library > metadata.
    """
    if library_media_type and library_media_type != "all":
        return library_media_type

    matching_type: str | None = None
    try:
        all_libs = db.session.scalars(select(Library)).all()
        for lib_record in all_libs:
            lib_p_res, lib_p_raw = library_path_conditions(lib_record)
            if resolved_path.startswith(lib_p_res) or resolved_path.startswith(
                lib_p_raw
            ):
                if lib_record.media_type and lib_record.media_type != "all":
                    matching_type = lib_record.media_type
                    break
    except Exception:
        logger.debug(
            "Failed to resolve library media type for %s: %s",
            resolved_path,
            exc_info=True,
        )

    if matching_type:
        return matching_type

    return getattr(metadata, "media_type", None) or (
        "comic"
        if metadata.file_format in ("cbz", "cbr", "zip")
        else "video"
        if metadata.file_format in VIDEO_EXTENSIONS
        else "audio"
        if metadata.file_format in AUDIO_EXTENSIONS
        else "book"
    )


def _assign_authors_tags_series(book: Book, metadata) -> None:
    """Resolves and assigns the authors, series, and tags on a Book record."""
    author_objs = []
    authors_list = getattr(metadata, "authors", None) or getattr(
        metadata, "creators", []
    )
    for author_name in authors_list:
        cleaned_name = author_name.strip()
        if not cleaned_name:
            continue
        author = db.session.scalar(select(Author).where(Author.name == cleaned_name))

        if not author:
            author = Author(name=cleaned_name)
            db.session.add(author)
        author_objs.append(author)
    book.authors = author_objs

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


def index_single_book(
    file_path: Path,
    covers_dir: Path,
    auto_enrich: bool = False,
    library_media_type: str | None = None,
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
            if (
                library_media_type
                and library_media_type != "all"
                and existing_book.media_type != library_media_type
            ):
                existing_book.media_type = library_media_type
                db.session.commit()
            return existing_book

        plugin = plugin_registry.get_plugin_for_extension(file_path.suffix)
        if plugin:
            metadata = plugin.parse_metadata(file_path)
        else:
            metadata = extract_metadata_from_file(file_path)

        if not metadata:
            return None

        # Cover processing
        cover_rel_path = _extract_and_generate_cover(
            metadata, plugin, file_path, file_hash, covers_dir
        )

        book = existing_book or Book(original_file_path=resolved_path)
        db.session.add(book)

        book.title = metadata.title or file_path.stem
        book.sort_title = compute_sort_title(book.title)
        book.file_format = metadata.file_format
        book.file_size = file_size
        book.file_hash = file_hash
        book.description = metadata.description
        book.publisher = metadata.publisher
        book.language = metadata.language or "en"
        book.isbn = getattr(metadata, "isbn", None)
        book.publication_date = metadata.publication_date
        book.page_count = getattr(metadata, "page_count", None)

        # Determine media type:
        book.media_type = _resolve_media_type(
            metadata, resolved_path, library_media_type
        )
        if cover_rel_path:
            book.cover_image_path = cover_rel_path

        # Technical playback, video & audio metadata attributes
        if hasattr(book, "duration"):
            book.duration = getattr(metadata, "duration", None)
        if hasattr(book, "bitrate"):
            book.bitrate = getattr(metadata, "bitrate", None)
        if hasattr(book, "resolution_width"):
            book.resolution_width = getattr(metadata, "resolution_width", None)
        if hasattr(book, "resolution_height"):
            book.resolution_height = getattr(metadata, "resolution_height", None)
        if hasattr(book, "codec"):
            book.codec = getattr(metadata, "codec", None)
        if hasattr(book, "season"):
            book.season = getattr(metadata, "season", None)
        if hasattr(book, "episode"):
            book.episode = getattr(metadata, "episode", None)
        if hasattr(book, "album"):
            book.album = getattr(metadata, "album", None)
        if hasattr(book, "track_number"):
            book.track_number = getattr(metadata, "track_number", None)
        if hasattr(book, "disc_number"):
            book.disc_number = getattr(metadata, "disc_number", None)

        _assign_authors_tags_series(book, metadata)

        db.session.commit()
        logger.info(
            "Indexed %s: %s (%s)", book.media_type, book.title, book.file_format
        )

        # Optional auto enrichment if enabled (books only)
        if auto_enrich and book.is_book:
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


def scan_library(app: Flask, library_id: str | int | None = None) -> dict[str, int]:
    """Scans configured library directories for changes. Supports scanning a specific library."""
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        auto_enrich = app.config.get("AUTO_ENRICH", False)
        covers_dir.mkdir(parents=True, exist_ok=True)

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

        added = 0
        existing_files: set[str] = set()
        supported = get_supported_extensions()

        for lib in libraries:
            lib_dir = Path(lib.path).expanduser()
            lib_dir.mkdir(parents=True, exist_ok=True)
            for root, _, filenames in os.walk(lib_dir, followlinks=True):
                for filename in filenames:
                    file_path = Path(root) / filename
                    if file_path.suffix.lower() in supported:
                        existing_files.add(str(file_path.resolve()))
                        book = index_single_book(
                            file_path,
                            covers_dir,
                            auto_enrich=auto_enrich,
                            library_media_type=lib.media_type,
                        )
                        if book:
                            added += 1

        # Clean up deleted files from DB
        if library_id is not None:
            deleted = 0
            for lib in libraries:
                p_res, p_raw = library_path_conditions(lib)
                lib_cond = or_(
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
