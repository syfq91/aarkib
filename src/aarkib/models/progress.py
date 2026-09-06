from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.book import Book
    from aarkib.models.user import User


class UserProgress(db.Model):
    __tablename__ = "user_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "book_id", name="uq_user_book_progress"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Position: EPUB CFI string or CBZ page number string or URI reference
    progress_location: Mapped[str] = mapped_column(
        String(500), nullable=False, default="0"
    )
    percentage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False, index=True
    )

    # OPDS Progression 1.0 Document extensions
    device_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chapter_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    references_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped[User | None] = relationship("User", back_populates="progress_records")
    book: Mapped[Book] = relationship("Book", back_populates="progress_records")

    @property
    def media_id(self) -> int:
        """Alias for book_id to support generalized media progress tracking."""
        return self.book_id

    @media_id.setter
    def media_id(self, value: int) -> None:
        self.book_id = value

    @property
    def last_accessed_at(self) -> datetime:
        """Alias for last_read_at to support generalized media playback/reading."""
        return self.last_read_at

    @last_accessed_at.setter
    def last_accessed_at(self, value: datetime) -> None:
        self.last_read_at = value

    def __repr__(self) -> str:
        return f"<UserProgress user={self.user_id} book={self.book_id} progress={self.percentage:.1f}%>"


class Bookmark(db.Model):
    __tablename__ = "bookmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    user: Mapped[User | None] = relationship("User", back_populates="bookmarks")
    book: Mapped[Book] = relationship("Book", back_populates="bookmarks")

    @property
    def media_id(self) -> int:
        """Alias for book_id to support generalized media bookmarks."""
        return self.book_id

    @media_id.setter
    def media_id(self, value: int) -> None:
        self.book_id = value

    def __repr__(self) -> str:
        return f"<Bookmark book={self.book_id} loc={self.location}>"
