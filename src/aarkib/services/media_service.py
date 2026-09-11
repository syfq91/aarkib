"""Service layer for catalog operations across all media types.

Centralizes reusable business logic so route handlers stay thin and the
creator/series/tag entity resolution is not duplicated across the API and scanner.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import func, or_, select

from aarkib.extensions import db
from aarkib.models import Author, Library, MediaItem, Series, Tag

AUDIOBOOK_EXTENSIONS = frozenset({"m4b"})
MUSIC_EXTENSIONS = frozenset({"mp3", "m4a", "flac", "ogg", "opus", "wav", "aac"})
AUDIO_EXTENSIONS = AUDIOBOOK_EXTENSIONS | MUSIC_EXTENSIONS
VIDEO_EXTENSIONS = frozenset({"mp4", "mkv", "webm", "avi", "mov", "m4v"})
BOOK_EXTENSIONS = frozenset({"epub", "cbz", "cbr", "zip", "pdf", "mobi", "azw3"})
MEDIA_TYPE_CHOICES = frozenset(
    {"all", "book", "comic", "video", "audio", "audiobook", "music", "podcast"}
)

MAX_TITLE_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 50000
MAX_PUBLISHER_LENGTH = 255
MAX_LANGUAGE_LENGTH = 30
MAX_ISBN_LENGTH = 50


def resolve_or_create_authors(names: list[str]) -> list[Author]:
    """Look up (or create) Author/Creator records for the given names and return them.

    Expected to be called inside an active DB session; callers are responsible
    for flushing/committing.
    """
    author_objs: list[Author] = []
    for name in names:
        cleaned_name = str(name).strip()
        if not cleaned_name:
            continue
        author = db.session.scalar(select(Author).where(Author.name == cleaned_name))
        if not author:
            author = Author(name=cleaned_name)
            db.session.add(author)
        author_objs.append(author)
    return author_objs


resolve_or_create_creators = resolve_or_create_authors


def resolve_or_create_series(name: str) -> Series:
    """Look up (or create) a Series/Collection record by name and return it."""
    cleaned = name.strip()
    series_obj = db.session.scalar(select(Series).where(Series.name == cleaned))
    if not series_obj:
        series_obj = Series(name=cleaned)
        db.session.add(series_obj)
    return series_obj


resolve_or_create_collections = resolve_or_create_series


def resolve_or_create_tags(names: list[str]) -> list[Tag]:
    """Look up (or create) Tag records for the given names and return them."""
    tag_objs: list[Tag] = []
    for name in names:
        cleaned = str(name).strip().title()
        if not cleaned:
            continue
        tag_obj = db.session.scalar(select(Tag).where(Tag.name == cleaned))
        if not tag_obj:
            tag_obj = Tag(name=cleaned)
            db.session.add(tag_obj)
        tag_objs.append(tag_obj)
    return tag_objs


def edit_media_metadata(item: MediaItem, data: dict) -> MediaItem:
    """Apply user-supplied metadata edits to a media item.

    Handles title, authors/creators, series/collection, tags, descriptive fields,
    video episode codes, and audio track attributes.
    Returns the updated item; caller is responsible for committing.
    """
    title = data.get("title")
    if title:
        item.title = str(title).strip()[:MAX_TITLE_LENGTH]

    # Authors / Creators
    authors_input = data.get("authors") or data.get("creators")
    if authors_input is not None:
        if isinstance(authors_input, str):
            author_names = [a.strip() for a in authors_input.split(",") if a.strip()]
        else:
            author_names = [a for a in authors_input if a and str(a).strip()]
        resolved_authors = resolve_or_create_authors(author_names)
        if resolved_authors:
            item.authors = resolved_authors

    # Series / Collection
    series_name = data.get("series") or data.get("collection")
    series_index_raw = data.get("series_index")
    if series_name is not None and str(series_name).strip():
        series_obj = resolve_or_create_series(str(series_name))
        item.series = series_obj
        if series_index_raw not in (None, ""):
            try:
                item.series_index = float(series_index_raw)
            except ValueError:
                pass
        else:
            item.series_index = None
    elif "series" in data or "collection" in data:
        item.series = None
        item.series_index = None

    # Tags / Categories
    tags_input = data.get("tags")
    if tags_input is not None:
        if isinstance(tags_input, str):
            tag_names = [t.strip() for t in tags_input.split(",") if t.strip()]
        else:
            tag_names = [t for t in tags_input if t and str(t).strip()]
        resolved_tags = resolve_or_create_tags(tag_names)
        if resolved_tags:
            item.tags = resolved_tags

    # Optional descriptive fields
    if "description" in data:
        description = data.get("description") or None
        if description:
            description = str(description)[:MAX_DESCRIPTION_LENGTH]
        item.description = description
    if "publisher" in data:
        publisher = data.get("publisher") or None
        if publisher:
            publisher = str(publisher).strip()[:MAX_PUBLISHER_LENGTH]
        item.publisher = publisher
    if "publication_date" in data:
        item.publication_date = data.get("publication_date") or None
    if "isbn" in data:
        isbn = data.get("isbn") or None
        if isbn:
            isbn = str(isbn).strip()[:MAX_ISBN_LENGTH]
        item.isbn = isbn
    if "language" in data:
        language = data.get("language")
        if language:
            language = str(language).strip()[:MAX_LANGUAGE_LENGTH]
        item.language = language or "en"

    # Video-specific metadata
    if "season" in data:
        try:
            item.season = int(data["season"]) if data["season"] is not None else None
        except ValueError, TypeError:
            pass
    if "episode" in data:
        try:
            item.episode = int(data["episode"]) if data["episode"] is not None else None
        except ValueError, TypeError:
            pass

    # Audio-specific metadata
    if "album" in data:
        album = data.get("album") or None
        if album:
            album = str(album).strip()[:MAX_PUBLISHER_LENGTH]
        item.album = album
    if "track_number" in data:
        try:
            item.track_number = (
                int(data["track_number"]) if data["track_number"] is not None else None
            )
        except ValueError, TypeError:
            pass
    if "disc_number" in data:
        try:
            item.disc_number = (
                int(data["disc_number"]) if data["disc_number"] is not None else None
            )
        except ValueError, TypeError:
            pass

    return item


# Backward compatibility alias
edit_book_metadata = edit_media_metadata


def generate_slug(name: str) -> str:
    """Generate a URL-safe, unique slug from a library folder name."""
    base = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-") or "media"
    slug = base
    counter = 1
    all_slugs = set(db.session.scalars(select(Library.slug)).all())
    while slug in all_slugs:
        counter += 1
        slug = f"{base}-{counter}"
    return slug


def resolve_library(identifier) -> Library:
    """Look up a Library by integer ID or string slug; raises KeyError if absent."""
    if str(identifier).isdigit():
        lib = db.session.get(Library, int(identifier))
    else:
        lib = db.session.scalar(select(Library).where(Library.slug == identifier))
    if not lib:
        raise KeyError("Library not found")
    return lib


def path_prefixes(path: str | Path) -> tuple[str, str]:
    """Return (resolved_prefix, raw_prefix) for prefix-matching a filesystem path.

    Used to match catalog item paths that fall within a configured library
    folder while tolerating symlinked / non-normalized storage paths.
    """
    p = Path(path).expanduser()
    try:
        p_res = str(p.resolve()).rstrip("/\\") + "/"
    except Exception:
        p_res = str(p).rstrip("/\\") + "/"
    p_raw = str(p).rstrip("/\\") + "/"
    return p_res, p_raw


def library_path_conditions(library: Library) -> tuple[str, str]:
    """Return (resolved_prefix, raw_prefix) for prefix matching a library path."""
    return path_prefixes(library.path)


def path_match_filter(path: str | Path, library: Library | None = None):
    """Return SQLAlchemy OR conditions matching items within a library path."""
    p_res, p_raw = path_prefixes(path)
    conditions = [
        MediaItem.original_file_path.startswith(p_res),
        MediaItem.original_file_path.startswith(p_raw),
    ]
    if library and getattr(library, "id", None):
        conditions.append(MediaItem.library_id == library.id)
    return or_(*conditions)


def count_media_in_library(library: Library) -> int:
    """Count catalog items indexed under a library folder."""
    p_res, p_raw = library_path_conditions(library)
    conditions = [
        MediaItem.original_file_path.startswith(p_res),
        MediaItem.original_file_path.startswith(p_raw),
    ]
    if getattr(library, "id", None):
        conditions.append(MediaItem.library_id == library.id)
    return (
        db.session.scalar(select(func.count(MediaItem.id)).where(or_(*conditions))) or 0
    )


count_books_in_library = count_media_in_library
