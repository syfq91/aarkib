"""Single-file media indexing, metadata extraction, and cover generation."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models import (
    Author,
    Book,
    Library,
    MediaItem,
    Series,
    Tag,
)
from aarkib.plugins import plugin_registry
from aarkib.services.media_service import (
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    library_path_conditions,
)
from aarkib.services.parsers.base import extract_metadata_from_file
from aarkib.services.thumbnail import generate_cover_webp

if TYPE_CHECKING:
    pass

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


def compute_sha256(file_path: Path, chunk_size: int = 1048576) -> str:
    """Computes full SHA-256 hash using 1MB read chunks for high I/O throughput."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()


FAST_FINGERPRINT_THRESHOLD = 32 * 1024 * 1024  # 32 MB
SAMPLE_CHUNK_SIZE = 65536  # 64 KB header and footer


def compute_fast_fingerprint(
    file_path: Path,
    threshold: int = FAST_FINGERPRINT_THRESHOLD,
    sample_size: int = SAMPLE_CHUNK_SIZE,
) -> str:
    """Computes a deterministic, fast identity fingerprint for media files.

    - For files <= threshold (e.g. EPUB, CBZ, images, small audio tracks <= 32MB):
        Returns full SHA-256 hash.
    - For files > threshold (e.g. 5GB–50GB video files):
        Hashes file_size (8 bytes) + first 64KB + last 64KB.
        Prefixed with "fp_" and fits within 64 chars.
    """
    stat_info = file_path.stat()
    file_size = stat_info.st_size

    if file_size <= threshold:
        return compute_sha256(file_path)

    hasher = hashlib.sha256()
    hasher.update(file_size.to_bytes(8, "big"))

    with open(file_path, "rb") as f:
        # Read header
        header = f.read(sample_size)
        hasher.update(header)

        # Read footer
        if file_size > sample_size:
            f.seek(max(0, file_size - sample_size))
            footer = f.read(sample_size)
            hasher.update(footer)

    return f"fp_{hasher.hexdigest()[:60]}"


def _extract_and_generate_cover(
    metadata: Any,
    plugin: Any,
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
    metadata: Any, resolved_path: str, library_media_type: str | None
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


def _assign_authors_tags_series(book: Book, metadata: Any) -> None:
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
    commit: bool = True,
) -> MediaItem | None:
    """Parses and updates or inserts a single media record in the database."""
    supported = get_supported_extensions()
    if file_path.suffix.lower() not in supported or not file_path.is_file():
        return None

    try:
        resolved_path = str(file_path.resolve())
        stat_info = file_path.stat()
        file_size = stat_info.st_size
        file_mtime = stat_info.st_mtime

        existing_book = db.session.scalar(
            select(Book).where(Book.original_file_path == resolved_path)
        )
        if (
            existing_book
            and existing_book.file_size == file_size
            and existing_book.file_mtime is not None
            and abs(existing_book.file_mtime - file_mtime) < 0.01
        ):
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
                if commit:
                    db.session.commit()
            return existing_book

        file_hash = compute_fast_fingerprint(file_path)

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
        book.file_mtime = file_mtime

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

        if commit:
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
