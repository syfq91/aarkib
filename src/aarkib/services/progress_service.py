"""User progress and bookmark management domain service."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models import Bookmark, MediaItem, UserProgress
from aarkib.services.events import EVENT_PLAYBACK_UPDATED, event_bus

logger = logging.getLogger(__name__)


def get_progress_for_items(
    user_id: int | None,
    item_ids: list[int],
    profile_id: int | None = None,
) -> dict[int, dict[str, Any]]:
    """Fetches progress summary map keyed by media_item_id, scoped by profile or user."""
    if not item_ids:
        return {}

    if profile_id is not None:
        target_cond = UserProgress.profile_id == profile_id
    elif user_id is not None:
        target_cond = UserProgress.user_id == user_id
    else:
        target_cond = UserProgress.user_id.is_(None)

    records = db.session.scalars(
        select(UserProgress).where(
            target_cond, UserProgress.media_item_id.in_(item_ids)
        )
    ).all()

    return {
        r.media_item_id: {
            "percentage": r.percentage,
            "location": r.progress_location,
            "position_seconds": r.position_seconds,
            "duration": r.duration,
            "playback_speed": r.playback_speed,
            "playback_type": r.playback_type,
            "is_completed": r.is_completed,
            "last_accessed_at": (
                r.last_accessed_at.isoformat() if r.last_accessed_at else None
            ),
        }
        for r in records
    }


def get_progress(
    user_id: int | None,
    item: MediaItem,
    profile_id: int | None = None,
) -> dict[str, Any]:
    """Fetches progress dictionary for a single media item, scoped by profile or user."""
    if profile_id is not None:
        target_cond = UserProgress.profile_id == profile_id
    elif user_id is not None:
        target_cond = UserProgress.user_id == user_id
    else:
        target_cond = UserProgress.user_id.is_(None)

    record = db.session.scalar(
        select(UserProgress).where(target_cond, UserProgress.media_item_id == item.id)
    )

    if record:
        return {
            "percentage": record.percentage,
            "location": record.progress_location,
            "position_seconds": record.position_seconds,
            "duration": record.duration,
            "playback_speed": record.playback_speed,
            "playback_type": record.playback_type,
            "is_completed": record.is_completed,
            "last_accessed_at": (
                record.last_accessed_at.isoformat() if record.last_accessed_at else None
            ),
        }

    return {
        "percentage": 0.0,
        "location": "0",
        "position_seconds": None,
        "duration": getattr(item, "duration", None),
        "playback_speed": 1.0,
        "playback_type": item.media_type,
        "is_completed": False,
    }


def update_progress(
    user_id: int | None,
    item: MediaItem,
    data: dict[str, Any],
    profile_id: int | None = None,
) -> UserProgress:
    """Updates or inserts reading/video progress for a media item, scoped by profile or user."""
    if profile_id is not None:
        target_cond = UserProgress.profile_id == profile_id
    elif user_id is not None:
        target_cond = UserProgress.user_id == user_id
    else:
        target_cond = UserProgress.user_id.is_(None)

    location = str(data.get("location", "0"))
    try:
        percentage = float(data.get("percentage", 0.0))
    except ValueError, TypeError:
        percentage = 0.0
    percentage = max(0.0, min(100.0, percentage))
    is_completed = bool(data.get("is_completed", False) or percentage >= 99.0)

    record = db.session.scalar(
        select(UserProgress).where(target_cond, UserProgress.media_item_id == item.id)
    )
    if not record:
        record = UserProgress(
            user_id=user_id,
            profile_id=profile_id,
            media_item_id=item.id,
        )
    elif profile_id is not None and record.profile_id is None:
        record.profile_id = profile_id

    # Only update location if new location is non-zero or record has no valid location
    if (location and location != "0") or not record.progress_location:
        record.progress_location = location

    # Don't reset a known positive percentage to 0 on race condition
    if percentage > 0.0 or not record.percentage:
        record.percentage = percentage

    # Enriched playback & consumption metrics
    pos_sec = data.get("position_seconds")
    if pos_sec is None and "position" in data:
        pos_sec = data.get("position")
    if pos_sec is not None:
        try:
            record.position_seconds = float(pos_sec)
        except ValueError, TypeError:
            pass
    elif location:
        try:
            record.position_seconds = float(location)
        except ValueError, TypeError:
            pass

    duration_val = data.get("duration") or getattr(item, "duration", None)
    if duration_val is not None:
        try:
            record.duration = float(duration_val)
        except ValueError, TypeError:
            pass

    speed_val = data.get("playback_speed")
    if speed_val is not None:
        try:
            record.playback_speed = max(0.25, min(4.0, float(speed_val)))
        except ValueError, TypeError:
            pass

    pb_type = data.get("playback_type") or item.media_type
    if pb_type:
        record.playback_type = str(pb_type)

    record.is_completed = is_completed
    record.last_accessed_at = datetime.now(UTC)
    db.session.add(record)
    db.session.commit()
    event_bus.emit(
        EVENT_PLAYBACK_UPDATED,
        {
            "media_id": item.id,
            "title": item.title,
            "media_type": getattr(item, "media_type", "book"),
            "location": record.progress_location,
            "percentage": record.percentage,
            "position_seconds": record.position_seconds,
            "duration": record.duration,
            "is_completed": record.is_completed,
            "playback_type": record.playback_type,
        },
    )
    return record


def list_bookmarks(
    media_item_id: int,
    user_id: int | None = None,
    profile_id: int | None = None,
) -> list[Bookmark]:
    """List bookmarks for a media item, optionally filtered by user or profile."""
    query = select(Bookmark).where(Bookmark.media_item_id == media_item_id)
    if profile_id is not None:
        query = query.where(Bookmark.profile_id == profile_id)
    elif user_id is not None:
        query = query.where(Bookmark.user_id == user_id)
    return list(db.session.scalars(query.order_by(Bookmark.created_at.desc())).all())


def add_bookmark(
    media_item_id: int,
    user_id: int | None,
    location: str,
    title: str | None = None,
    snippet: str | None = None,
    profile_id: int | None = None,
) -> Bookmark:
    """Create a bookmark for a media item, optionally associated with a profile."""
    if not location:
        raise ValueError("Location is required")

    bm = Bookmark(
        user_id=user_id,
        profile_id=profile_id,
        media_item_id=media_item_id,
        location=location,
        title=title or f"Bookmark at {location}",
        snippet=snippet,
    )
    db.session.add(bm)
    db.session.commit()
    return bm


def delete_bookmark(
    bookmark_id: int,
    user_id: int | None = None,
    profile_id: int | None = None,
) -> bool:
    """Delete a bookmark, checking authorization if owned by a specific user or profile.

    Returns True if deleted.
    Raises KeyError if bookmark does not exist.
    Raises PermissionError if bookmark belongs to another user or profile.
    """
    bm = db.session.get(Bookmark, bookmark_id)
    if not bm:
        raise KeyError("Bookmark not found")
    if (
        profile_id is not None
        and bm.profile_id is not None
        and bm.profile_id != profile_id
    ):
        raise PermissionError("Forbidden")
    if bm.user_id and user_id and bm.user_id != user_id:
        raise PermissionError("Forbidden")

    db.session.delete(bm)
    db.session.commit()
    return True
