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
from aarkib.models import MediaItem
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


def _is_safe_http_url(url: str) -> bool:
    """Verifies that a URL strictly uses http or https schemes."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _http_get_json(url: str) -> dict[str, Any] | list[Any] | None:
    """Helper to perform HTTP GET requests returning parsed JSON."""
    if not _is_safe_http_url(url):
        logger.debug("Rejected non-HTTP/HTTPS URL: %s", url)
        return None
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
    if not _is_safe_http_url(url):
        logger.debug("Rejected non-HTTP/HTTPS URL: %s", url)
        return None
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
    book: MediaItem,
    covers_dir: Path,
    overwrite: bool = False,
    provider: str = "all",
) -> dict[str, Any]:
    """Compatibility wrapper that enriches a book using enrich_media_item."""
    res = enrich_media_item(book, covers_dir, overwrite=overwrite, provider=provider)
    if "book_id" not in res and "media_id" in res:
        res["book_id"] = res["media_id"]
    return res


def enrich_media_item(
    item: MediaItem,
    covers_dir: Path,
    overwrite: bool = False,
    provider: str = "all",
    candidate_external_id: str | None = None,
    candidate_provider: str | None = None,
) -> dict[str, Any]:
    """Enriches metadata for any media item (book, video, music, audiobook)."""
    from aarkib.services.media_service import (
        resolve_or_create_authors,
        resolve_or_create_tags,
    )
    from aarkib.services.metadata import metadata_registry

    # 1. Detach search parameters before external network I/O
    item_id = item.id
    media_type = item.media_type or "all"
    is_book = getattr(item, "is_book", False)
    item_isbn = getattr(item, "isbn", None)
    item_title = item.title
    first_author = item.authors[0].name if getattr(item, "authors", None) else None
    item_year = getattr(item, "publication_date", None) or getattr(
        item, "release_year", None
    )
    file_hash = item.file_hash
    has_cover = bool(item.cover_image_path)
    locked_fields = (
        set(item.get_locked_fields()) if hasattr(item, "get_locked_fields") else set()
    )

    # Invariant: Close session so no SQLite transaction/lock is held during network requests
    db.session.close()

    details = None
    if candidate_external_id and candidate_provider:
        details = metadata_registry.fetch_details(
            candidate_provider,
            candidate_external_id,
            media_type=media_type,
        )
    else:
        # If it's a book and no explicit candidate requested, use fetch_external_metadata
        if is_book:
            legacy_meta = fetch_external_metadata(
                isbn=item_isbn,
                title=item_title,
                author=first_author,
                provider=provider,
            )
            if legacy_meta:
                from aarkib.services.metadata.base import MediaMetadataDetails

                details = MediaMetadataDetails(
                    id=legacy_meta.isbn or legacy_meta.title or str(item_id),
                    provider=legacy_meta.source,
                    title=legacy_meta.title or item_title,
                    creators=legacy_meta.authors,
                    overview=legacy_meta.description,
                    poster_url=legacy_meta.cover_url,
                    poster_bytes=legacy_meta.cover_bytes,
                    release_date=legacy_meta.publication_date,
                    publisher=legacy_meta.publisher,
                    genres=legacy_meta.tags,
                    language=legacy_meta.language,
                    page_count=legacy_meta.page_count,
                    isbn=legacy_meta.isbn,
                )
        else:
            # Query registry
            search_query = item_title
            candidates = metadata_registry.search(
                media_type=media_type,
                query=search_query,
                year=item_year,
                provider_name=provider if provider != "all" else None,
            )
            if candidates and candidates[0].score >= 0.5:
                top = candidates[0]
                details = metadata_registry.fetch_details(
                    top.provider, top.id, media_type=media_type
                )

    if not details:
        return {"status": "not_found", "media_id": item_id, "changes": []}

    # 2. Download and prepare cover art while session is closed
    cover_filename = None
    if "cover_image" not in locked_fields and (not has_cover or overwrite):
        cover_bytes = details.poster_bytes
        if not cover_bytes and details.poster_url:
            from aarkib.services.metadata.client import ResilientHttpClient

            client = ResilientHttpClient(provider_name=details.provider)
            cover_bytes = client.get_bytes(details.poster_url)

        if cover_bytes:
            filename = f"{file_hash[:16]}.webp"
            cover_output_path = covers_dir / filename
            if generate_cover_webp(cover_bytes, cover_output_path):
                cover_filename = filename

    # 3. Re-acquire item and apply changes in a brief database write transaction
    target_item = db.session.get(MediaItem, item_id)
    if not target_item:
        db.session.close()
        return {"status": "not_found", "media_id": item_id, "changes": []}

    changes: list[str] = []

    # Title
    if (
        not target_item.is_field_locked("title")
        and (not target_item.title or overwrite)
        and details.title
    ):
        target_item.title = details.title
        changes.append("title")

    # Overview / Description
    if (
        not target_item.is_field_locked("description")
        and (not target_item.description or overwrite)
        and details.overview
    ):
        target_item.description = details.overview
        changes.append("description")

    # Creators / Authors / Directors
    if (
        not target_item.is_field_locked("creators")
        and not target_item.is_field_locked("authors")
        and (not target_item.authors or overwrite)
        and details.creators
    ):
        target_item.authors = resolve_or_create_authors(details.creators)
        changes.append("creators")

    # Publisher
    if (
        not target_item.is_field_locked("publisher")
        and (not target_item.publisher or overwrite)
        and details.publisher
    ):
        target_item.publisher = details.publisher
        changes.append("publisher")

    # Release / Publication date
    if (
        not target_item.is_field_locked("publication_date")
        and (not target_item.publication_date or overwrite)
        and details.release_date
    ):
        target_item.publication_date = str(details.release_date)[:10]
        changes.append("publication_date")

    # Language
    if (
        not target_item.is_field_locked("language")
        and (not target_item.language or overwrite)
        and details.language
    ):
        target_item.language = details.language
        changes.append("language")

    # Genres / Tags
    if (
        not target_item.is_field_locked("tags")
        and not target_item.is_field_locked("genres")
        and (not target_item.tags or overwrite)
        and details.genres
    ):
        target_item.tags = resolve_or_create_tags(details.genres)
        changes.append(f"tags ({len(target_item.tags)})")

    # Video specifics
    if (
        hasattr(target_item, "season")
        and not target_item.is_field_locked("season")
        and details.season is not None
    ):
        target_item.season = details.season
        changes.append("season")
    if (
        hasattr(target_item, "episode")
        and not target_item.is_field_locked("episode")
        and details.episode is not None
    ):
        target_item.episode = details.episode
        changes.append("episode")
    if (
        hasattr(target_item, "duration")
        and not target_item.is_field_locked("duration")
        and details.duration
        and not target_item.duration
    ):
        target_item.duration = details.duration
        changes.append("duration")

    # Music specifics
    if (
        hasattr(target_item, "album")
        and not target_item.is_field_locked("album")
        and details.album
        and (not target_item.album or overwrite)
    ):
        target_item.album = details.album
        changes.append("album")

    # Book / Comic specifics
    if (
        hasattr(target_item, "page_count")
        and not target_item.is_field_locked("page_count")
        and (not target_item.page_count or overwrite)
        and details.page_count
    ):
        target_item.page_count = details.page_count
        changes.append("page_count")

    if (
        hasattr(target_item, "isbn")
        and not target_item.is_field_locked("isbn")
        and (not target_item.isbn or overwrite)
        and details.isbn
    ):
        target_item.isbn = details.isbn
        changes.append("isbn")

    # Cover
    if cover_filename:
        target_item.cover_image_path = cover_filename
        changes.append("cover_image")

    # Stash external_id
    if details.id:
        target_item.external_id = f"{details.provider}:{details.id}"

    if changes:
        db.session.commit()
        from aarkib.services.search import sync_media_item_fts

        sync_media_item_fts(target_item.id)
        logger.info(
            "Enriched %s ID %d (%s) with: %s",
            target_item.media_type,
            target_item.id,
            target_item.title,
            ", ".join(changes),
        )
    else:
        db.session.close()

    return {
        "status": "success" if changes else "no_changes_needed",
        "media_id": target_item.id,
        "title": target_item.title,
        "source": details.provider,
        "changes": changes,
    }


def enrich_all_books(
    app: Flask,
    overwrite: bool = False,
    provider: str = "all",
) -> dict[str, int]:
    """Batch enriches all books in the database using enrich_all_media."""
    return enrich_all_media(
        app, overwrite=overwrite, provider=provider, media_type="book"
    )


def enrich_all_media(
    app: Flask,
    overwrite: bool = False,
    provider: str = "all",
    media_type: str = "all",
) -> dict[str, int]:
    """Batch enriches media items in the database matching media_type."""
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        covers_dir.mkdir(parents=True, exist_ok=True)

        stmt = select(MediaItem.id)
        if media_type and media_type != "all":
            stmt = stmt.where(MediaItem.media_type == media_type)

        item_ids = list(db.session.scalars(stmt).all())
        db.session.close()
        enriched_count = 0
        skipped_count = 0

        for item_id in item_ids:
            item = db.session.get(MediaItem, item_id)
            if not item:
                skipped_count += 1
                db.session.close()
                continue
            res = enrich_media_item(
                item, covers_dir, overwrite=overwrite, provider=provider
            )
            if res.get("changes"):
                enriched_count += 1
            else:
                skipped_count += 1
            db.session.close()

        return {
            "total": len(item_ids),
            "enriched": enriched_count,
            "skipped": skipped_count,
        }
