from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from buukuu.extensions import db
from buukuu.models.author import book_authors
from buukuu.models.tag import book_tags

if TYPE_CHECKING:
    from buukuu.models.author import Author
    from buukuu.models.progress import Bookmark, UserProgress
    from buukuu.models.series import Series
    from buukuu.models.tag import Tag


class Book(db.Model):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    sort_title: Mapped[str | None] = mapped_column(
        String(500), nullable=True, index=True
    )

    # File details
    original_file_path: Mapped[str] = mapped_column(
        String(1000), unique=True, nullable=False
    )
    file_format: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True
    )  # 'epub' or 'cbz'
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cover_image_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Metadata
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default="en"
    )
    isbn: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    publication_date: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Series metadata
    series_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("series.id", ondelete="SET NULL"), nullable=True, index=True
    )
    series_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    series: Mapped[Series | None] = relationship("Series", back_populates="books")
    authors: Mapped[list[Author]] = relationship(
        "Author", secondary=book_authors, back_populates="books"
    )
    tags: Mapped[list[Tag]] = relationship(
        "Tag", secondary=book_tags, back_populates="books"
    )
    progress_records: Mapped[list[UserProgress]] = relationship(
        "UserProgress", back_populates="book", cascade="all, delete-orphan"
    )
    bookmarks: Mapped[list[Bookmark]] = relationship(
        "Bookmark", back_populates="book", cascade="all, delete-orphan"
    )

    @property
    def authors_display(self) -> str:
        if not self.authors:
            return "Unknown Author"
        return ", ".join(a.name for a in self.authors)

    @property
    def tags_display(self) -> list[str]:
        return [t.name for t in self.tags]

    def __repr__(self) -> str:
        return f"<Book {self.id}: {self.title}>"
