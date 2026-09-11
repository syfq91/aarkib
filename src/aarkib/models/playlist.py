from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.media_item import MediaItem
    from aarkib.models.user import User


class UserFavorite(db.Model):
    """Tracks starred / favorited media items per user."""

    __tablename__ = "user_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "media_item_id", name="uq_user_favorite"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    media_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("media_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="favorites")
    media_item: Mapped[MediaItem] = relationship(
        "MediaItem", back_populates="favorited_by"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "media_item_id": self.media_item_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Playlist(db.Model):
    """User-curated playlists for music tracks, audiobooks, or videos."""

    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default="music"
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    user: Mapped[User | None] = relationship("User", back_populates="playlists")
    items: Mapped[list[PlaylistItem]] = relationship(
        "PlaylistItem",
        back_populates="playlist",
        order_by="PlaylistItem.position",
        cascade="all, delete-orphan",
    )

    def to_dict(self, include_items: bool = False) -> dict[str, Any]:
        res: dict[str, Any] = {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "description": self.description,
            "media_type": self.media_type,
            "is_public": self.is_public,
            "item_count": len(self.items) if self.items is not None else 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_items and self.items:
            res["items"] = [item.to_dict() for item in self.items]
        return res


class PlaylistItem(db.Model):
    """Ordered entries inside a Playlist."""

    __tablename__ = "playlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playlist_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("playlists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    media_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("media_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    playlist: Mapped[Playlist] = relationship("Playlist", back_populates="items")
    media_item: Mapped[MediaItem] = relationship(
        "MediaItem", back_populates="playlist_entries"
    )

    def to_dict(self) -> dict[str, Any]:
        item_data = None
        if self.media_item:
            item_data = {
                "id": self.media_item.id,
                "title": self.media_item.title,
                "file_format": self.media_item.file_format,
                "media_type": self.media_item.media_type,
                "duration": getattr(self.media_item, "duration", None),
                "formatted_duration": getattr(
                    self.media_item, "formatted_duration", ""
                ),
                "creators": getattr(self.media_item, "creators_display", ""),
                "album": getattr(self.media_item, "album", None),
                "cover_image_path": self.media_item.cover_image_path,
                "player_url": self.media_item.player_url,
            }
        return {
            "id": self.id,
            "playlist_id": self.playlist_id,
            "media_item_id": self.media_item_id,
            "position": self.position,
            "added_at": self.added_at.isoformat() if self.added_at else None,
            "media_item": item_data,
        }
