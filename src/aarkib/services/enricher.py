from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models import Book, Tag
from aarkib.services.thumbnail import generate_cover_webp

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)

USER_AGENT = "Aarkib/0.1.0 (https://github.com/syfq91/aarkib; media-server)"
DEFAULT_TIMEOUT = 10


@dataclass
class EnrichedMetadata:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    description: str | None = None
    publisher: str | None = None
    publication_date: str | None = None
    language: str | None = None
    page_count: int | None = None
    tags: list[str] = field(default_factory=list)
    cover_bytes: bytes | None = None
    cover_url: str | None = None
    isbn: str | None = None
    source: str = ""


def _http_get_json(url: str) -> dict[str, Any] | list[Any] | None:
    """Helper to perform HTTP GET requests returning parsed JSON."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                data = response.read().decode("utf-8")
                return json.loads(data)
    except Exception as exc:
        logger.debug("HTTP GET error for %s: %s", url, exc)
    return None


def _http_get_bytes(url: str) -> bytes | None:
    """Helper to download raw binary bytes (e.g., covers)."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                return response.read()
    except Exception as exc:
        logger.debug("Failed to download image from %s: %s", url, exc)
    return None


def fetch_from_google_books(
    isbn: str | None = None,
    title: str | None = None,
    author: str | None = None,
) -> EnrichedMetadata | None:
    """Queries the Google Books Volume API."""
    query_parts = []
    if isbn:
        clean_isbn = isbn.replace("-", "").strip()
        query_parts.append(f"isbn:{clean_isbn}")
    else:
        if title:
            query_parts.append(f'intitle:"{title}"')
        if author and author != "Unknown Author":
            query_parts.append(f'inauthor:"{author}"')

    if not query_parts:
        return None

    query = "+".join(query_parts)
    encoded_query = urllib.parse.quote(query, safe="+:")
    url = f"https://www.googleapis.com/books/v1/volumes?q={encoded_query}&maxResults=1"

    data = _http_get_json(url)
    if not isinstance(data, dict) or not data.get("items"):
        return None

    item = data["items"][0]
    volume_info = item.get("volumeInfo", {})

    cover_url = None
    image_links = volume_info.get("imageLinks", {})
    for key in ("extraLarge", "large", "medium", "small", "thumbnail"):
        if key in image_links:
            cover_url = image_links[key]
            # Force HTTPS
            if cover_url.startswith("http://"):
                cover_url = "https://" + cover_url[7:]
            break

    cover_bytes = _http_get_bytes(cover_url) if cover_url else None

    # ISBN extraction
    extracted_isbn = None
    for id_entry in volume_info.get("industryIdentifiers", []):
        if id_entry.get("type") in ("ISBN_13", "ISBN_10"):
            extracted_isbn = id_entry.get("identifier")
            if id_entry.get("type") == "ISBN_13":
                break

    return EnrichedMetadata(
        title=volume_info.get("title"),
        authors=volume_info.get("authors", []),
        description=volume_info.get("description"),
        publisher=volume_info.get("publisher"),
        publication_date=volume_info.get("publishedDate"),
        language=volume_info.get("language"),
        page_count=volume_info.get("pageCount"),
        tags=volume_info.get("categories", []),
        cover_url=cover_url,
        cover_bytes=cover_bytes,
        isbn=extracted_isbn or isbn,
        source="Google Books",
    )


def fetch_from_open_library(
    isbn: str | None = None,
    title: str | None = None,
    author: str | None = None,
) -> EnrichedMetadata | None:
    """Queries the Open Library Books and Search API."""
    if isbn:
        clean_isbn = isbn.replace("-", "").strip()
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{clean_isbn}&format=json&jscmd=data"
        data = _http_get_json(url)
        if isinstance(data, dict) and f"ISBN:{clean_isbn}" in data:
            book_data = data[f"ISBN:{clean_isbn}"]
            authors = [
                a.get("name") for a in book_data.get("authors", []) if a.get("name")
            ]
            publishers = [
                p.get("name") for p in book_data.get("publishers", []) if p.get("name")
            ]
            subjects = [
                s.get("name") for s in book_data.get("subjects", []) if s.get("name")
            ]
            cover_info = book_data.get("cover", {})
            cover_url = (
                cover_info.get("large")
                or cover_info.get("medium")
                or cover_info.get("small")
            )
            cover_bytes = _http_get_bytes(cover_url) if cover_url else None

            return EnrichedMetadata(
                title=book_data.get("title"),
                authors=authors,
                description=book_data.get("description", {}).get("value")
                if isinstance(book_data.get("description"), dict)
                else book_data.get("description"),
                publisher=publishers[0] if publishers else None,
                publication_date=book_data.get("publish_date"),
                page_count=book_data.get("number_of_pages"),
                tags=subjects[:10],
                cover_url=cover_url,
                cover_bytes=cover_bytes,
                isbn=clean_isbn,
                source="Open Library",
            )

    if title:
        params = {"title": title, "limit": "1"}
        if author and author != "Unknown Author":
            params["author"] = author
        query_str = urllib.parse.urlencode(params)
        url = f"https://openlibrary.org/search.json?{query_str}"
        data = _http_get_json(url)
        if isinstance(data, dict) and data.get("docs"):
            doc = data["docs"][0]
            cover_id = doc.get("cover_i")
            cover_url = (
                f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
                if cover_id
                else None
            )
            cover_bytes = _http_get_bytes(cover_url) if cover_url else None

            isbns = doc.get("isbn", [])
            found_isbn = isbns[0] if isbns else None

            return EnrichedMetadata(
                title=doc.get("title"),
                authors=doc.get("author_name", []),
                description=doc.get("first_sentence", {}).get("value")
                if isinstance(doc.get("first_sentence"), dict)
                else (
                    doc.get("first_sentence", [None])[0]
                    if isinstance(doc.get("first_sentence"), list)
                    else None
                ),
                publisher=doc.get("publisher", [None])[0]
                if isinstance(doc.get("publisher"), list)
                else None,
                publication_date=str(doc.get("first_publish_year"))
                if doc.get("first_publish_year")
                else None,
                language=doc.get("language", [None])[0]
                if isinstance(doc.get("language"), list)
                else None,
                tags=doc.get("subject", [])[:10]
                if isinstance(doc.get("subject"), list)
                else [],
                cover_url=cover_url,
                cover_bytes=cover_bytes,
                isbn=found_isbn or isbn,
                source="Open Library",
            )

    return None


def fetch_external_metadata(
    isbn: str | None = None,
    title: str | None = None,
    author: str | None = None,
    provider: str = "all",
) -> EnrichedMetadata | None:
    """Cascading lookup across configured metadata providers."""
    result: EnrichedMetadata | None = None

    if provider in ("all", "googlebooks"):
        result = fetch_from_google_books(isbn=isbn, title=title, author=author)

    if not result and provider in ("all", "openlibrary"):
        result = fetch_from_open_library(isbn=isbn, title=title, author=author)

    return result


def enrich_book(
    book: Book,
    covers_dir: Path,
    overwrite: bool = False,
    provider: str = "all",
) -> dict[str, Any]:
    """Enriches metadata for a single Book instance and saves changes to DB."""
    first_author = book.authors[0].name if book.authors else None
    meta = fetch_external_metadata(
        isbn=book.isbn,
        title=book.title,
        author=first_author,
        provider=provider,
    )

    if not meta:
        return {"status": "not_found", "book_id": book.id, "changes": []}

    changes: list[str] = []

    if (not book.description or overwrite) and meta.description:
        book.description = meta.description
        changes.append("description")

    if (not book.publisher or overwrite) and meta.publisher:
        book.publisher = meta.publisher
        changes.append("publisher")

    if (not book.publication_date or overwrite) and meta.publication_date:
        book.publication_date = str(meta.publication_date)[:10]
        changes.append("publication_date")

    if (not book.language or overwrite) and meta.language:
        book.language = meta.language
        changes.append("language")

    if (not book.page_count or overwrite) and meta.page_count:
        book.page_count = meta.page_count
        changes.append("page_count")

    if (not book.isbn or overwrite) and meta.isbn:
        book.isbn = meta.isbn
        changes.append("isbn")

    # Update or attach tags
    if meta.tags and (not book.tags or overwrite):
        existing_tags = {
            t.name.lower(): t for t in db.session.scalars(select(Tag)).all()
        }
        tag_objs = []
        for tag_name in meta.tags:
            cleaned = tag_name.strip().title()
            if not cleaned or len(cleaned) > 50:
                continue
            if cleaned.lower() in existing_tags:
                tag_objs.append(existing_tags[cleaned.lower()])
            else:
                new_tag = Tag(name=cleaned)
                db.session.add(new_tag)
                existing_tags[cleaned.lower()] = new_tag
                tag_objs.append(new_tag)
        book.tags = tag_objs
        changes.append(f"tags ({len(tag_objs)})")

    # Cover image download & WebP conversion
    if meta.cover_bytes and (not book.cover_image_path or overwrite):
        cover_filename = f"{book.file_hash[:16]}.webp"
        cover_output_path = covers_dir / cover_filename
        if generate_cover_webp(meta.cover_bytes, cover_output_path):
            book.cover_image_path = cover_filename
            changes.append("cover_image")

    if changes:
        db.session.commit()
        logger.info(
            "Enriched book ID %d (%s) with: %s", book.id, book.title, ", ".join(changes)
        )

    return {
        "status": "success" if changes else "no_changes_needed",
        "book_id": book.id,
        "title": book.title,
        "source": meta.source,
        "changes": changes,
    }


def enrich_all_books(
    app: Flask,
    overwrite: bool = False,
    provider: str = "all",
) -> dict[str, int]:
    """Batch enriches all books in the database."""
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        covers_dir.mkdir(parents=True, exist_ok=True)

        books = db.session.scalars(select(Book)).all()
        enriched_count = 0
        skipped_count = 0

        for b in books:
            res = enrich_book(b, covers_dir, overwrite=overwrite, provider=provider)
            if res.get("changes"):
                enriched_count += 1
            else:
                skipped_count += 1

        return {
            "total": len(books),
            "enriched": enriched_count,
            "skipped": skipped_count,
        }
