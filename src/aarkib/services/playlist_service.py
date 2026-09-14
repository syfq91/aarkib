"""Playlist, playlist items, and user favorites domain service."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select, update

from aarkib.extensions import db
from aarkib.models import MediaItem, Playlist, PlaylistItem, UserFavorite

logger = logging.getLogger(__name__)


def list_playlists(user_id: int | None = None) -> list[Playlist]:
    """Lists playlists visible to the user (public or owned)."""
    cond = Playlist.is_public.is_(True)
    if user_id:
        cond = or_(cond, Playlist.user_id == user_id)

    return list(
        db.session.scalars(
            select(Playlist).where(cond).order_by(Playlist.updated_at.desc())
        ).all()
    )


def create_playlist(
    user_id: int | None,
    title: str,
    description: str | None = None,
    media_type: str = "music",
    is_public: bool = False,
) -> Playlist:
    """Creates a new playlist."""
    cleaned_title = str(title).strip()
    if not cleaned_title:
        raise ValueError("Playlist title is required")

    playlist = Playlist(
        user_id=user_id,
        title=cleaned_title,
        description=description,
        media_type=media_type,
        is_public=bool(is_public),
    )
    db.session.add(playlist)
    db.session.commit()
    return playlist


def get_playlist(playlist_id: int, user_id: int | None = None) -> Playlist:
    """Fetches a playlist by ID.

    Raises KeyError if not found.
    Raises PermissionError if private and user is not owner.
    """
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        raise KeyError("Playlist not found")
    if not playlist.is_public and (not user_id or playlist.user_id != user_id):
        raise PermissionError("Access denied to private playlist")
    return playlist


def add_playlist_item(
    playlist_id: int,
    user_id: int | None,
    media_item_id: int,
    position: int | None = None,
) -> PlaylistItem:
    """Adds a media item to a playlist.

    Raises KeyError if playlist or media item not found.
    Raises PermissionError if user is not playlist owner.
    """
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        raise KeyError("Playlist not found")
    if playlist.user_id is not None and playlist.user_id != user_id:
        raise PermissionError("Only the playlist owner can add items")

    media_item = db.session.get(MediaItem, media_item_id)
    if not media_item:
        raise KeyError("Media item not found")

    if position is None:
        position = len(playlist.items)

    playlist_item = PlaylistItem(
        playlist_id=playlist.id,
        media_item_id=media_item.id,
        position=int(position),
    )
    db.session.add(playlist_item)
    playlist.updated_at = datetime.now(UTC)
    db.session.commit()
    return playlist_item


def remove_playlist_item(
    playlist_id: int,
    user_id: int | None,
    item_id: int,
) -> bool:
    """Removes an item from a playlist (by PlaylistItem ID or MediaItem ID).

    Raises KeyError if playlist or playlist item not found.
    Raises PermissionError if user is not playlist owner.
    """
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        raise KeyError("Playlist not found")
    if playlist.user_id is not None and playlist.user_id != user_id:
        raise PermissionError("Only the playlist owner can remove items")

    target_entry = db.session.scalar(
        select(PlaylistItem).where(
            PlaylistItem.playlist_id == playlist_id,
            or_(PlaylistItem.id == item_id, PlaylistItem.media_item_id == item_id),
        )
    )
    if not target_entry:
        raise KeyError("Playlist item entry not found")

    db.session.delete(target_entry)
    playlist.updated_at = datetime.now(UTC)
    db.session.commit()
    return True


def reorder_playlist_items(
    playlist_id: int,
    user_id: int | None,
    item_ids: list[int],
) -> None:
    """Updates position ordering for items in a playlist.

    Raises KeyError if playlist not found.
    Raises PermissionError if user is not playlist owner.
    """
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        raise KeyError("Playlist not found")
    if playlist.user_id is not None and playlist.user_id != user_id:
        raise PermissionError("Only the playlist owner can reorder items")

    for idx, mid in enumerate(item_ids):
        db.session.execute(
            update(PlaylistItem)
            .where(
                PlaylistItem.playlist_id == playlist_id,
                or_(PlaylistItem.id == mid, PlaylistItem.media_item_id == mid),
            )
            .values(position=idx)
        )

    playlist.updated_at = datetime.now(UTC)
    db.session.commit()


def delete_playlist(playlist_id: int, user_id: int | None) -> bool:
    """Deletes a playlist.

    Raises KeyError if playlist not found.
    Raises PermissionError if user is not playlist owner.
    """
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        raise KeyError("Playlist not found")
    if playlist.user_id is not None and playlist.user_id != user_id:
        raise PermissionError("Only the playlist owner can delete this playlist")

    db.session.delete(playlist)
    db.session.commit()
    return True


def toggle_favorite(
    user_id: int,
    media_item_id: int,
    explicit_state: bool | None = None,
) -> bool:
    """Toggles or sets the favorited status of a media item for a user.

    Returns the new favorited state (True or False).
    Raises KeyError if media item does not exist.
    """
    item = db.session.get(MediaItem, media_item_id)
    if not item:
        raise KeyError("Media item not found")

    existing = db.session.scalar(
        select(UserFavorite).where(
            UserFavorite.user_id == user_id,
            UserFavorite.media_item_id == media_item_id,
        )
    )

    if explicit_state is None:
        if not existing:
            fav = UserFavorite(user_id=user_id, media_item_id=media_item_id)
            db.session.add(fav)
            db.session.commit()
            return True
        else:
            db.session.delete(existing)
            db.session.commit()
            return False
    else:
        if not explicit_state:
            if existing:
                db.session.delete(existing)
                db.session.commit()
            return False
        else:
            if not existing:
                fav = UserFavorite(user_id=user_id, media_item_id=media_item_id)
                db.session.add(fav)
                db.session.commit()
            return True


def list_favorites(user_id: int) -> list[UserFavorite]:
    """Returns all favorited records for the given user, ordered newest first."""
    return list(
        db.session.scalars(
            select(UserFavorite)
            .where(UserFavorite.user_id == user_id)
            .order_by(UserFavorite.created_at.desc())
        ).all()
    )
