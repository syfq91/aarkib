from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from aarkib.config import (
    Config,
    get_env_media_dirs,
)
from aarkib.extensions import db
from aarkib.models import (
    Author,
    Book,
    Library,
    MediaItem,
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
    ".pdf",
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


def get_media_dirs_from_config(app: Flask | None = None) -> list[Path]:
    """Resolves one or more media directories directly from app config and environment variables."""
    raw_candidates: list[Any] = []

    if app is not None:
        raw_dirs = app.config.get("MEDIA_DIRS")
        raw_dir = app.config.get("MEDIA_DIR")

        default_dirs = Config.MEDIA_DIRS
        default_dir = Config.MEDIA_DIR

        if raw_dirs is not None and raw_dirs != default_dirs:
            if isinstance(raw_dirs, (list, tuple, set)):
                raw_candidates.extend(raw_dirs)
            else:
                raw_candidates.append(raw_dirs)

        if raw_dir is not None and raw_dir != default_dir:
            if isinstance(raw_dir, (list, tuple, set)):
                raw_candidates.extend(raw_dir)
            else:
                raw_candidates.append(raw_dir)

    # Check explicitly defined environment variables
    env_paths = get_env_media_dirs()
    if env_paths:
        raw_candidates.extend(env_paths)

    # If neither app config override nor explicit env vars were found
    if not raw_candidates:
        if app is not None:
            raw = app.config.get("MEDIA_DIRS") or app.config.get("MEDIA_DIR")
            if raw is not None:
                if isinstance(raw, (list, tuple, set)):
                    raw_candidates.extend(raw)
                else:
                    raw_candidates.append(raw)
            else:
                data_dir = Path(app.config.get("DATA_DIR", "data"))
                raw_candidates.append(data_dir / "media")
        else:
            raw_candidates.append(Path("data/media"))

    # Parse and deduplicate
    final_paths: list[Path] = []
    seen: set[str] = set()

    for item in raw_candidates:
        if isinstance(item, (list, tuple, set)):
            path_objs = [
                Path(sub).expanduser() if not isinstance(sub, Path) else sub
                for sub in item
                if sub
            ]
        elif item:
            path_objs = [
                Path(item).expanduser() if not isinstance(item, Path) else item
            ]
        else:
            path_objs = []

        for path_obj in path_objs:
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
        return [data_dir / "media"]

    return final_paths


get_library_dirs_from_config = get_media_dirs_from_config


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
        target_dirs = get_media_dirs_from_config(app)
    else:
        target_dirs = get_env_media_dirs()
        if app is not None:
            custom_dirs = app.config.get("MEDIA_DIRS")
            default_dirs = Config.MEDIA_DIRS
            if custom_dirs and custom_dirs != default_dirs:
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
        folder_name = p_expanded.name
        if not folder_name or folder_name in (".", "/", "data"):
            name = "Media" if not existing_libs else f"Library {len(existing_libs) + 1}"
        else:
            name = folder_name.replace("_", " ").replace("-", " ").title()

        # Determine default media_type based on folder/name context
        lower_name = (folder_name or name or "").lower()
        if any(w in lower_name for w in ("comic", "manga", "cbz")):
            media_type = "book"
        elif any(w in lower_name for w in ("show", "tv", "series", "season")):
            media_type = "tv"
        elif any(w in lower_name for w in ("video", "movie", "film", "anime")):
            media_type = "movie"
        elif any(w in lower_name for w in ("podcast", "podcasts")):
            media_type = "podcast"
        elif any(w in lower_name for w in ("audiobook", "audiobooks")):
            media_type = "audiobook"
        elif any(w in lower_name for w in ("music", "song", "album")):
            media_type = "music"
        elif "book" in lower_name:
            media_type = "book"
        else:
            media_type = "book"

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
            if (
                resolved_path.startswith((lib_p_res, lib_p_raw))
                and lib_record.media_type
                and lib_record.media_type != "all"
            ):
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

    meta_type = getattr(metadata, "media_type", None)
    if meta_type and meta_type in (
        "comic",
        "video",
        "movie",
        "tv",
        "audiobook",
        "music",
        "podcast",
    ):
        return meta_type

    fmt = (getattr(metadata, "file_format", "") or "").lower()
    if fmt in ("cbz", "cbr", "zip"):
        return "comic"
    if fmt in VIDEO_EXTENSIONS:
        if (
            getattr(metadata, "season", None) is not None
            or getattr(metadata, "episode", None) is not None
        ):
            return "tv"
        return "movie"
    if (
        fmt == "m4b"
        or getattr(metadata, "chapters", None)
        or "Audiobook" in getattr(metadata, "tags", [])
    ):
        return "audiobook"
    if (
        "Podcast" in getattr(metadata, "tags", [])
        or "/podcast/" in resolved_path.lower()
        or "/podcasts/" in resolved_path.lower()
    ):
        return "podcast"
    if fmt in AUDIO_EXTENSIONS:
        return meta_type or "music"
    return "book"


def _assign_authors_tags_series(book: Book, metadata) -> None:
    """Resolves and assigns the authors, series, and tags on a Book record."""
    is_locked = getattr(book, "is_field_locked", lambda _field: False)

    if not is_locked("authors") and not is_locked("creators"):
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

    if not is_locked("series") and not is_locked("collection"):
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

    if not is_locked("tags") and not is_locked("genres"):
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


def index_media_file(
    file_path: Path,
    covers_dir: Path,
    auto_enrich: bool = False,
    library_media_type: str | None = None,
    library_id: int | None = None,
    metadata_provider: str | None = None,
) -> MediaItem | None:
    """Parses and updates or inserts a single media record in the database."""
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
            updated = False
            if (
                library_media_type
                and library_media_type != "all"
                and existing_book.media_type != library_media_type
            ):
                existing_book.media_type = library_media_type
                updated = True
            if (
                library_id is not None
                and getattr(existing_book, "library_id", None) != library_id
            ):
                existing_book.library_id = library_id
                updated = True
            if updated:
                db.session.commit()
            return existing_book

        plugin = None
        if library_media_type and library_media_type != "all":
            plugin = plugin_registry.get_plugin_for_media_type(library_media_type)
        if not plugin:
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
        if library_id is not None:
            book.library_id = library_id

        is_locked = getattr(book, "is_field_locked", lambda _field: False)

        if not is_locked("title"):
            book.title = metadata.title or file_path.stem
            book.sort_title = compute_sort_title(book.title)
        book.file_format = metadata.file_format
        book.file_size = file_size
        book.file_hash = file_hash

        if not is_locked("description"):
            book.description = metadata.description
        if not is_locked("publisher"):
            book.publisher = metadata.publisher
        if not is_locked("language"):
            book.language = metadata.language or "en"
        if not is_locked("isbn"):
            book.isbn = getattr(metadata, "isbn", None)
        if not is_locked("publication_date"):
            book.publication_date = metadata.publication_date
        if not is_locked("page_count"):
            book.page_count = getattr(metadata, "page_count", None)

        # Determine media type:
        book.media_type = _resolve_media_type(
            metadata, resolved_path, library_media_type
        )
        if cover_rel_path and not is_locked("cover_image"):
            book.cover_image_path = cover_rel_path

        # Technical playback, video & audio metadata attributes
        if hasattr(book, "duration") and not is_locked("duration"):
            book.duration = getattr(metadata, "duration", None)
        if hasattr(book, "bitrate") and not is_locked("bitrate"):
            book.bitrate = getattr(metadata, "bitrate", None)
        if hasattr(book, "resolution_width"):
            book.resolution_width = getattr(metadata, "resolution_width", None)
        if hasattr(book, "resolution_height"):
            book.resolution_height = getattr(metadata, "resolution_height", None)
        if hasattr(book, "codec"):
            book.codec = getattr(metadata, "codec", None)
        if hasattr(book, "season") and not is_locked("season"):
            book.season = getattr(metadata, "season", None)
        if hasattr(book, "episode") and not is_locked("episode"):
            book.episode = getattr(metadata, "episode", None)
        if hasattr(book, "album") and not is_locked("album"):
            book.album = getattr(metadata, "album", None)
        if hasattr(book, "album_artist") and not is_locked("album_artist"):
            book.album_artist = getattr(metadata, "album_artist", None)
        if hasattr(book, "track_number") and not is_locked("track_number"):
            book.track_number = getattr(metadata, "track_number", None)
        if hasattr(book, "disc_number") and not is_locked("disc_number"):
            book.disc_number = getattr(metadata, "disc_number", None)
        if hasattr(book, "release_year") and not is_locked("release_year"):
            book.release_year = getattr(metadata, "release_year", None)
        if hasattr(book, "genre") and not is_locked("genre"):
            book.genre = getattr(metadata, "genre", None)
        if hasattr(book, "is_compilation"):
            book.is_compilation = getattr(metadata, "is_compilation", False)
        if hasattr(book, "author") and not is_locked("author"):
            book.author = getattr(metadata, "author", None)
        if hasattr(book, "narrator") and not is_locked("narrator"):
            book.narrator = getattr(metadata, "narrator", None)
        if hasattr(book, "chapters_json") and not is_locked("chapters"):
            chapters = getattr(metadata, "chapters", None)
            book.chapters_json = json.dumps(chapters) if chapters else None
        if hasattr(book, "abridged"):
            book.abridged = getattr(metadata, "abridged", False)
        if hasattr(book, "episode_type") and not is_locked("episode_type"):
            book.episode_type = getattr(metadata, "episode_type", None)
        if hasattr(book, "podcast_feed_url") and not is_locked("podcast_feed_url"):
            book.podcast_feed_url = getattr(metadata, "feed_url", None)
        if hasattr(book, "podcast_guid") and not is_locked("podcast_guid"):
            book.podcast_guid = getattr(metadata, "guid", None)

        _assign_authors_tags_series(book, metadata)

        db.session.commit()
        logger.info(
            "Indexed %s: %s (%s)", book.media_type, book.title, book.file_format
        )

        # Optional auto enrichment if enabled
        if auto_enrich:
            try:
                from aarkib.services.enricher import enrich_media_item

                enrich_media_item(
                    book,
                    covers_dir,
                    overwrite=False,
                    provider=metadata_provider or "all",
                )
            except Exception as e:
                logger.debug("Auto enrich error for %s: %s", book.title, e)

        return book

    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to index %s: %s", file_path, exc)
        return None


index_single_book = index_media_file


def scan_library(
    app: Flask,
    library_id: str | int | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
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
        for lib in libraries:
            lib_dir = Path(lib.path).expanduser()
            lib_dir.mkdir(parents=True, exist_ok=True)
            for root, _, filenames in os.walk(lib_dir, followlinks=True):
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

        for idx, (lib, file_path) in enumerate(candidate_files):
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
            )
            if book:
                added += 1

            if progress_callback and (idx % 5 == 0 or idx == total_candidates - 1):
                pct = 10.0 + ((idx + 1) / max(total_candidates, 1)) * 80.0
                progress_callback(
                    pct, f"Indexing {file_path.name} ({idx + 1}/{total_candidates})"
                )

        # Clean up deleted files from DB
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

        if added > 0 or deleted > 0:
            try:
                from aarkib.services.search import rebuild_search_index

                rebuild_search_index()
            except Exception as e:
                logger.warning("FTS search index rebuild failed after scan: %s", e)

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
                    libraries = sync_and_get_libraries(self.app)
                    matched_lib = None
                    for lib in libraries:
                        p_res, p_raw = library_path_conditions(lib)
                        res_p = str(file_path.resolve())
                        if res_p.startswith(p_res) or str(file_path).startswith(p_raw):
                            matched_lib = lib
                            break
                    b = index_single_book(
                        file_path,
                        covers_dir,
                        library_media_type=matched_lib.media_type
                        if matched_lib
                        else None,
                        library_id=getattr(matched_lib, "id", None)
                        if matched_lib
                        else None,
                    )
                    if b:
                        from aarkib.services.search import sync_media_item_fts

                        sync_media_item_fts(b.id)
                else:
                    book = db.session.scalar(
                        select(Book).where(
                            Book.original_file_path == str(file_path.resolve())
                        )
                    )
                    if book:
                        b_id = book.id
                        db.session.delete(book)
                        db.session.commit()
                        from aarkib.services.search import remove_media_item_fts

                        remove_media_item_fts(b_id)

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

    # Stop any existing watcher first
    existing = (
        app.extensions.get("library_watcher") if hasattr(app, "extensions") else None
    )
    if existing and existing.is_alive():
        try:
            existing.stop()
            existing.join(timeout=2.0)
        except Exception as e:
            logger.debug("Error stopping existing library watcher: %s", e)

    library_dirs = get_library_dirs(app)
    event_handler = LibraryChangeHandler(app)
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
