from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from buukuu.extensions import db
from buukuu.models.author import book_authors
from buukuu.models.media import MediaItemMixin, MediaType
from buukuu.models.tag import book_tags

if TYPE_CHECKING:
    from buukuu.models.author import Author
    from buukuu.models.progress import Bookmark, UserProgress
    from buukuu.models.series import Series
    from buukuu.models.tag import Tag


class Book(db.Model, MediaItemMixin):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Book-specific metadata
    isbn: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Series metadata
    series_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("series.id", ondelete="SET NULL"), nullable=True, index=True
    )
    series_index: Mapped[float | None] = mapped_column(Float, nullable=True)

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

    def __init__(self, **kwargs: Any) -> None:
        if "media_type" not in kwargs or not kwargs["media_type"]:
            fmt = (kwargs.get("file_format") or "").lower()
            if fmt in ("cbz", "cbr", "zip"):
                kwargs["media_type"] = MediaType.COMIC.value
            else:
                kwargs["media_type"] = MediaType.BOOK.value
        super().__init__(**kwargs)

    @property
    def authors_display(self) -> str:
        if not self.authors:
            return "Unknown Author"
        return ", ".join(a.name for a in self.authors)

    @property
    def creators_display(self) -> str:
        """Alias for authors_display to support generalized media creators."""
        return self.authors_display

    @property
    def tags_display(self) -> list[str]:
        return [t.name for t in self.tags]

    @property
    def player_url(self) -> str:
        """Returns in-browser player or reader URL via plugin registry or fallback."""
        from buukuu.plugins import plugin_registry

        plugin = plugin_registry.get_plugin_for_media_type(self.media_type or "book")
        if plugin:
            url = plugin.get_player_url(self.id, self.file_format)
            if url:
                return url

        if (self.file_format or "").lower() in ("cbz", "zip", "cbr"):
            return f"/reader/cbz/{self.id}"
        return f"/reader/epub/{self.id}"

    @property
    def reader_url(self) -> str:
        """Alias for player_url."""
        return self.player_url

    def __repr__(self) -> str:
        return f"<Book {self.id}: {self.title}>"
