from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models import MediaItem, MetadataSource
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
    item_isbn = getattr(item, "isbn", None)
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
    confidence_val = 1.0
    source_id_val = None

    if candidate_external_id and candidate_provider:
        details = metadata_registry.fetch_details(
            candidate_provider,
            candidate_external_id,
            media_type=media_type,
        )
        source_id_val = f"{candidate_provider}:{candidate_external_id}"
        confidence_val = 1.0
    else:
        from aarkib.services.metadata.matcher import MetadataMatcher

        matcher = MetadataMatcher()
        candidate_matches = matcher.find_candidates(
            item_or_query=item,
            media_type=media_type,
            year=item_year,
            creators=[first_author] if first_author else None,
            isbn=item_isbn,
            provider_name=provider if provider != "all" else None,
        )
        best = matcher.select_best_match(candidate_matches, min_confidence=0.5)
        if best:
            details = metadata_registry.fetch_details(
                best.provider, best.id, media_type=media_type
            )
            confidence_val = best.confidence_score
            source_id_val = f"{best.provider}:{best.id}"

    if not details:
        return {"status": "not_found", "media_id": item_id, "changes": []}

    # 2. Download and prepare cover art while session is closed
    cover_filename = None
    can_download_cover = (("cover_image" not in locked_fields) or overwrite) and (
        not has_cover or overwrite
    )
    if can_download_cover:
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
    final_source_id = source_id_val or f"{provider_src}:{details.id}"

    def can_update(field_name: str, has_val: bool) -> bool:
        if target_item.is_field_locked(field_name):
            return overwrite
        return (not has_val) or overwrite

    def record_prov(field_name: str, val: Any) -> None:
        if hasattr(target_item, "set_field_provenance"):
            target_item.set_field_provenance(
                field_name,
                provider_src,
                source_type=MetadataSource.AUTOMATIC,
                source_id=final_source_id,
                confidence=confidence_val,
                value=val,
            )

    # Title
    if can_update("title", bool(target_item.title)) and details.title:
        target_item.title = details.title
        changes.append("title")
        record_prov("title", details.title)

    # Overview / Description
    if can_update("description", bool(target_item.description)) and details.overview:
        target_item.description = details.overview
        changes.append("description")
        record_prov("description", details.overview)

    # Creators
    if can_update("creators", bool(target_item.creators)) and details.creators:
        target_item.creators = resolve_or_create_creators(details.creators)
        changes.append("creators")
        record_prov("creators", details.creators)

    # Publisher
    if can_update("publisher", bool(target_item.publisher)) and details.publisher:
        target_item.publisher = details.publisher
        changes.append("publisher")
        record_prov("publisher", details.publisher)

    # Release / Publication date
    if (
        can_update("publication_date", bool(target_item.publication_date))
        and details.release_date
    ):
        target_item.publication_date = str(details.release_date)[:10]
        changes.append("publication_date")
        record_prov("publication_date", target_item.publication_date)

    # Language
    if (
        can_update(
            "language",
            bool(target_item.language and target_item.language != "en"),
        )
        and details.language
    ):
        target_item.language = details.language
        changes.append("language")
        record_prov("language", details.language)

    # Genres / Tags
    if (
        can_update("tags", bool(target_item.tags))
        and not target_item.is_field_locked("genres")
        and details.genres
    ):
        target_item.tags = resolve_or_create_tags(details.genres)
        changes.append(f"tags ({len(target_item.tags)})")
        record_prov("tags", [t.name for t in target_item.tags])

    # Video specifics
    if (
        hasattr(target_item, "season")
        and can_update("season", target_item.season is not None)
        and details.season is not None
    ):
        target_item.season = details.season
        changes.append("season")
        record_prov("season", details.season)
    if (
        hasattr(target_item, "episode")
        and can_update("episode", target_item.episode is not None)
        and details.episode is not None
    ):
        target_item.episode = details.episode
        changes.append("episode")
        record_prov("episode", details.episode)
    if (
        hasattr(target_item, "duration")
        and can_update("duration", bool(target_item.duration))
        and details.duration
    ):
        target_item.duration = details.duration
        changes.append("duration")
        record_prov("duration", details.duration)

    # Music specifics
    if (
        hasattr(target_item, "album")
        and can_update("album", bool(target_item.album))
        and details.album
    ):
        target_item.album = details.album
        changes.append("album")
        record_prov("album", details.album)

    # Book / Comic specifics
    if (
        hasattr(target_item, "page_count")
        and can_update("page_count", bool(target_item.page_count))
        and details.page_count
    ):
        target_item.page_count = details.page_count
        changes.append("page_count")
        record_prov("page_count", details.page_count)

    if (
        hasattr(target_item, "isbn")
        and can_update("isbn", bool(target_item.isbn))
        and details.isbn
    ):
        target_item.isbn = details.isbn
        changes.append("isbn")
        record_prov("isbn", details.isbn)

    # Cover
    if cover_filename and can_update("cover_image", bool(target_item.cover_image_path)):
        target_item.cover_image_path = cover_filename
        changes.append("cover_image")
        record_prov("cover_image", cover_filename)

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
