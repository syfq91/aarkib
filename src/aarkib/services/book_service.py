"""Service layer for book/media catalog operations.

Centralizes reusable business logic so route handlers stay thin and the
author/series/tag entity resolution is not duplicated across the API and the
scanner.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import func, or_, select

from aarkib.extensions import db
from aarkib.models import Author, Book, Library, Series, Tag

VIDEO_EXTENSIONS = frozenset({"mp4", "mkv", "webm", "avi", "mov", "m4v"})
MEDIA_TYPE_CHOICES = frozenset({"all", "book", "comic", "video"})


def resolve_or_create_authors(names: list[str]) -> list[Author]:
    """Look up (or create) Author records for the given names and return them.

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


def resolve_or_create_series(name: str) -> Series:
    """Look up (or create) a Series record by name and return it."""
    cleaned = name.strip()
    series_obj = db.session.scalar(select(Series).where(Series.name == cleaned))
    if not series_obj:
        series_obj = Series(name=cleaned)
        db.session.add(series_obj)
    return series_obj


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


def edit_book_metadata(book: Book, data: dict) -> Book:
    """Apply user-supplied metadata edits to a book.

    Handles title, authors, series, tags, and optional descriptive fields.
    Returns the updated book; the caller is responsible for committing.
    """
    title = data.get("title")
    if title:
        book.title = str(title).strip()

    # Authors
    authors_input = data.get("authors")
    if authors_input is not None:
        if isinstance(authors_input, str):
            author_names = [a.strip() for a in authors_input.split(",") if a.strip()]
        else:
            author_names = [a for a in authors_input if a and str(a).strip()]
        book_authors = resolve_or_create_authors(author_names)
        if book_authors:
            book.authors = book_authors

    # Series
    series_name = data.get("series")
    series_index_raw = data.get("series_index")
    if series_name is not None and str(series_name).strip():
        series_obj = resolve_or_create_series(str(series_name))
        book.series = series_obj
        if series_index_raw not in (None, ""):
            try:
                book.series_index = float(series_index_raw)
            except ValueError:
                pass
        else:
            book.series_index = None
    elif "series" in data:
        book.series = None
        book.series_index = None

    # Tags / Categories
    tags_input = data.get("tags")
    if tags_input is not None:
        if isinstance(tags_input, str):
            tag_names = [t.strip() for t in tags_input.split(",") if t.strip()]
        else:
            tag_names = [t for t in tags_input if t and str(t).strip()]
        book_tags = resolve_or_create_tags(tag_names)
        if book_tags:
            book.tags = book_tags

    # Optional descriptive fields
    if "description" in data:
        book.description = data.get("description") or None
    if "publisher" in data:
        book.publisher = data.get("publisher") or None
    if "publication_date" in data:
        book.publication_date = data.get("publication_date") or None
    if "isbn" in data:
        book.isbn = data.get("isbn") or None
    if "language" in data:
        book.language = data.get("language") or "en"

    return book


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


def path_match_filter(path: str | Path):
    """Return SQLAlchemy OR conditions matching items within a library path."""
    p_res, p_raw = path_prefixes(path)
    return or_(
        Book.original_file_path.startswith(p_res),
        Book.original_file_path.startswith(p_raw),
    )


def count_books_in_library(library: Library) -> int:
    """Count catalog items indexed under a library folder."""
    p_res, p_raw = library_path_conditions(library)
    cond = or_(
        Book.original_file_path.startswith(p_res),
        Book.original_file_path.startswith(p_raw),
    )
    return db.session.scalar(select(func.count(Book.id)).where(cond)) or 0
