from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
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


class UserProgress(db.Model):
    __tablename__ = "user_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "media_item_id", name="uq_user_item_progress"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    media_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("media_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Position: EPUB CFI string, CBZ page number string, or media playback timestamp (seconds)
    progress_location: Mapped[str] = mapped_column(
        String(500), nullable=False, default="0"
    )
    percentage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False, index=True
    )

    # OPDS Progression 1.0 Document extensions
    device_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chapter_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    references_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Enriched consumption & playback metrics
    position_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    playback_speed: Mapped[float | None] = mapped_column(
        Float, default=1.0, nullable=True
    )
    playback_type: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationships
    user: Mapped[User | None] = relationship("User", back_populates="progress_records")
    media_item: Mapped[MediaItem] = relationship(
        "MediaItem", back_populates="progress_records"
    )

    @property
    def media_id(self) -> int:
        return self.media_item_id

    @media_id.setter
    def media_id(self, value: int) -> None:
        self.media_item_id = value

    def __repr__(self) -> str:
        pct = self.percentage or 0.0
        return f"<UserProgress user={self.user_id} item={self.media_item_id} progress={pct:.1f}%>"


class Bookmark(db.Model):
    __tablename__ = "bookmarks"
    __table_args__ = (
        Index(
            "ix_bookmarks_item_user_created",
            "media_item_id",
            "user_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    media_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("media_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    location: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    user: Mapped[User | None] = relationship("User", back_populates="bookmarks")
    media_item: Mapped[MediaItem] = relationship(
        "MediaItem", back_populates="bookmarks"
    )

    @property
    def media_id(self) -> int:
        return self.media_item_id

    @media_id.setter
    def media_id(self, value: int) -> None:
        self.media_item_id = value

    def __repr__(self) -> str:
        return f"<Bookmark item={self.media_item_id} loc={self.location}>"
