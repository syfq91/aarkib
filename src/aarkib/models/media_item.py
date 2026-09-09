from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from aarkib.extensions import db
from aarkib.models.author import book_authors
from aarkib.models.media import (
    AudioTrackMixin,
    MediaItemMixin,
    MediaType,
    VideoItemMixin,
)
from aarkib.models.tag import book_tags

if TYPE_CHECKING:
    from aarkib.models.author import Author
    from aarkib.models.progress import Bookmark, UserProgress
    from aarkib.models.series import Series
    from aarkib.models.tag import Tag


class MediaItem(db.Model, MediaItemMixin, VideoItemMixin, AudioTrackMixin):
    """Unified catalog model representing books, comics, videos, and audio in Aarkib."""

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Book-specific metadata
    isbn: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Series / collection metadata
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

    # Generalized domain synonyms
    creators = synonym("authors")
    collection = synonym("series")

    def __init__(self, **kwargs: Any) -> None:
        if "media_type" not in kwargs or not kwargs["media_type"]:
            fmt = (kwargs.get("file_format") or "").lower()
            if fmt in ("cbz", "cbr", "zip"):
                kwargs["media_type"] = MediaType.COMIC.value
            elif fmt in ("mp4", "mkv", "webm", "avi", "mov", "m4v"):
                kwargs["media_type"] = MediaType.VIDEO.value
            elif fmt in ("mp3", "m4a", "flac", "ogg", "opus", "wav", "aac"):
                kwargs["media_type"] = MediaType.AUDIO.value
            else:
                kwargs["media_type"] = MediaType.BOOK.value
        super().__init__(**kwargs)

    @property
    def formatted_duration(self) -> str:
        """Returns video or audio duration formatted as '1h 45m' or '45m 12s'."""
        if not self.duration:
            return ""
        total_seconds = int(self.duration)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours}h {minutes:02d}m"
        if minutes > 0:
            return f"{minutes}m {seconds:02d}s"
        return f"{seconds}s"

    @property
    def resolution_label(self) -> str:
        """Returns standard resolution label (4K, 1080p, 720p, etc.)."""
        if not self.resolution_height:
            return ""
        h = self.resolution_height
        if h >= 2160:
            return "4K"
        elif h >= 1440:
            return "1440p"
        elif h >= 1080:
            return "1080p"
        elif h >= 720:
            return "720p"
        elif h >= 480:
            return "480p"
        return f"{h}p"

    @property
    def episode_code(self) -> str:
        """Returns episode code e.g. 'S01E02' if both season and episode are set."""
        if self.season is not None and self.episode is not None:
            return f"S{self.season:02d}E{self.episode:02d}"
        return ""

    @property
    def authors_display(self) -> str:
        if not self.authors:
            return (
                "Unknown Creator"
                if (self.is_video or self.is_audio)
                else "Unknown Author"
            )
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
        from aarkib.plugins import plugin_registry

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
        return f"<MediaItem {self.id}: {self.title}>"


# Canonical and backward-compatibility aliases
Book = MediaItem
Item = MediaItem

# Register "Book" and "Item" in SQLAlchemy's Declarative class registry so string relations resolve
db.Model.registry._class_registry["Book"] = MediaItem
db.Model.registry._class_registry["Item"] = MediaItem
