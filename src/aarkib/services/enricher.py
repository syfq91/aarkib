from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models import MediaItem
from aarkib.services.thumbnail import generate_cover_webp

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)


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
        resolve_or_create_creators,
        resolve_or_create_tags,
    )
    from aarkib.services.metadata import metadata_registry

    # 1. Detach search parameters before external network I/O
    item_id = item.id
    media_type = item.media_type or "all"
    is_book = getattr(item, "is_book", False)
    item_isbn = getattr(item, "isbn", None)
    item_title = item.title
    first_author = item.creators[0].name if getattr(item, "creators", None) else None
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
        search_query = ""
        if is_book and item_isbn:
            search_query = f"isbn:{item_isbn}"
        else:
            search_query = item_title
            if is_book and first_author and first_author != "Unknown Author":
                search_query += f" {first_author}"

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
    provider_src = details.provider or "external"

    # Title
    if (
        not target_item.is_field_locked("title")
        and (not target_item.title or overwrite)
        and details.title
    ):
        target_item.title = details.title
        changes.append("title")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("title", provider_src)

    # Overview / Description
    if (
        not target_item.is_field_locked("description")
        and (not target_item.description or overwrite)
        and details.overview
    ):
        target_item.description = details.overview
        changes.append("description")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("description", provider_src)

    # Creators
    if (
        not target_item.is_field_locked("creators")
        and (not target_item.creators or overwrite)
        and details.creators
    ):
        target_item.creators = resolve_or_create_creators(details.creators)
        changes.append("creators")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("creators", provider_src)

    # Publisher
    if (
        not target_item.is_field_locked("publisher")
        and (not target_item.publisher or overwrite)
        and details.publisher
    ):
        target_item.publisher = details.publisher
        changes.append("publisher")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("publisher", provider_src)

    # Release / Publication date
    if (
        not target_item.is_field_locked("publication_date")
        and (not target_item.publication_date or overwrite)
        and details.release_date
    ):
        target_item.publication_date = str(details.release_date)[:10]
        changes.append("publication_date")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("publication_date", provider_src)

    # Language
    if (
        not target_item.is_field_locked("language")
        and (not target_item.language or overwrite)
        and details.language
    ):
        target_item.language = details.language
        changes.append("language")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("language", provider_src)

    # Genres / Tags
    if (
        not target_item.is_field_locked("tags")
        and not target_item.is_field_locked("genres")
        and (not target_item.tags or overwrite)
        and details.genres
    ):
        target_item.tags = resolve_or_create_tags(details.genres)
        changes.append(f"tags ({len(target_item.tags)})")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("tags", provider_src)

    # Video specifics
    if (
        hasattr(target_item, "season")
        and not target_item.is_field_locked("season")
        and details.season is not None
    ):
        target_item.season = details.season
        changes.append("season")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("season", provider_src)
    if (
        hasattr(target_item, "episode")
        and not target_item.is_field_locked("episode")
        and details.episode is not None
    ):
        target_item.episode = details.episode
        changes.append("episode")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("episode", provider_src)
    if (
        hasattr(target_item, "duration")
        and not target_item.is_field_locked("duration")
        and details.duration
        and not target_item.duration
    ):
        target_item.duration = details.duration
        changes.append("duration")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("duration", provider_src)

    # Music specifics
    if (
        hasattr(target_item, "album")
        and not target_item.is_field_locked("album")
        and details.album
        and (not target_item.album or overwrite)
    ):
        target_item.album = details.album
        changes.append("album")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("album", provider_src)

    # Book / Comic specifics
    if (
        hasattr(target_item, "page_count")
        and not target_item.is_field_locked("page_count")
        and (not target_item.page_count or overwrite)
        and details.page_count
    ):
        target_item.page_count = details.page_count
        changes.append("page_count")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("page_count", provider_src)

    if (
        hasattr(target_item, "isbn")
        and not target_item.is_field_locked("isbn")
        and (not target_item.isbn or overwrite)
        and details.isbn
    ):
        target_item.isbn = details.isbn
        changes.append("isbn")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("isbn", provider_src)

    # Cover
    if cover_filename:
        target_item.cover_image_path = cover_filename
        changes.append("cover_image")
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance("cover_image", provider_src)

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
    progress_callback: Callable[[float, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    **kwargs: Any,
) -> dict[str, int]:
    """Batch enriches all books in the database using enrich_all_media."""
    return enrich_all_media(
        app,
        overwrite=overwrite,
        provider=provider,
        media_type="book",
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        **kwargs,
    )


def enrich_all_media(
    app: Flask,
    overwrite: bool = False,
    provider: str = "all",
    media_type: str = "all",
    progress_callback: Callable[[float, str], None] | None = None,
    cancel_event: threading.Event | None = None,
    **kwargs: Any,
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
        total = len(item_ids)

        for idx, item_id in enumerate(item_ids):
            if cancel_event is not None and cancel_event.is_set():
                logger.info("enrich_all_media cancelled by user request.")
                return {
                    "total": total,
                    "enriched": enriched_count,
                    "skipped": skipped_count,
                    "cancelled": 1,
                }

            if progress_callback:
                pct = (idx / max(total, 1)) * 100.0
                progress_callback(pct, f"Enriching item {idx + 1}/{total}...")

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

        if progress_callback:
            progress_callback(
                100.0,
                f"Enrichment complete: {enriched_count} enriched, {skipped_count} skipped",
            )

        return {
            "total": len(item_ids),
            "enriched": enriched_count,
            "skipped": skipped_count,
        }
