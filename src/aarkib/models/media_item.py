from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from aarkib.extensions import db
from aarkib.models.creator import media_creators
from aarkib.models.media import (
    AudiobookItemMixin,
    AudioTrackMixin,
    MediaItemMixin,
    MediaType,
    PodcastItemMixin,
    VideoItemMixin,
)
from aarkib.models.tag import media_tags

if TYPE_CHECKING:
    from aarkib.models.collection import Collection
    from aarkib.models.creator import Creator
    from aarkib.models.library import Library
    from aarkib.models.playlist import PlaylistItem, UserFavorite
    from aarkib.models.progress import Bookmark, UserProgress
    from aarkib.models.tag import Tag


class MediaItem(
    db.Model,
    MediaItemMixin,
    VideoItemMixin,
    AudiobookItemMixin,
    AudioTrackMixin,
    PodcastItemMixin,
):
    """Unified catalog model representing books, comics, videos, and audio in Aarkib."""

    __tablename__ = "media_items"
    __table_args__ = (
        Index("ix_media_items_collection_series", "collection_id", "series_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Book & text metadata
    isbn: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Collection / series metadata
    collection_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("collections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    series_index: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Library folder metadata
    library_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("libraries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    collection: Mapped[Collection | None] = relationship(
        "Collection", back_populates="media_items"
    )
    library: Mapped[Library | None] = relationship(
        "Library", back_populates="media_items"
    )
    creators: Mapped[list[Creator]] = relationship(
        "Creator", secondary=media_creators, back_populates="media_items"
    )
    tags: Mapped[list[Tag]] = relationship(
        "Tag", secondary=media_tags, back_populates="media_items"
    )
    progress_records: Mapped[list[UserProgress]] = relationship(
        "UserProgress", back_populates="media_item", cascade="all, delete-orphan"
    )
    bookmarks: Mapped[list[Bookmark]] = relationship(
        "Bookmark", back_populates="media_item", cascade="all, delete-orphan"
    )
    favorited_by: Mapped[list[UserFavorite]] = relationship(
        "UserFavorite", back_populates="media_item", cascade="all, delete-orphan"
    )
    playlist_entries: Mapped[list[PlaylistItem]] = relationship(
        "PlaylistItem", back_populates="media_item", cascade="all, delete-orphan"
    )

    # Generalized domain synonyms
    series = synonym("collection")
    series_id = synonym("collection_id")
    authors = synonym("creators")

    def __init__(self, **kwargs: Any) -> None:
        if "media_type" not in kwargs or not kwargs["media_type"]:
            fmt = (kwargs.get("file_format") or "").lower()
            if fmt in ("cbz", "cbr", "zip"):
                kwargs["media_type"] = MediaType.COMIC.value
            elif fmt in ("mp4", "mkv", "webm", "avi", "mov", "m4v"):
                kwargs["media_type"] = MediaType.VIDEO.value
            elif fmt in ("mp3", "m4a", "m4b", "flac", "ogg", "opus", "wav", "aac"):
                kwargs["media_type"] = MediaType.AUDIO.value
            else:
                kwargs["media_type"] = MediaType.BOOK.value
        # Remap legacy kwargs if provided
        if "series" in kwargs and "collection" not in kwargs:
            kwargs["collection"] = kwargs.pop("series")
        if "series_id" in kwargs and "collection_id" not in kwargs:
            kwargs["collection_id"] = kwargs.pop("series_id")
        if "authors" in kwargs and "creators" not in kwargs:
            kwargs["creators"] = kwargs.pop("authors")
        super().__init__(**kwargs)

    @property
    def formatted_duration(self) -> str:
        """Returns playback duration formatted as '1h 45m' or '45m 12s'."""
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
        """Returns standard video resolution label (4K, 1080p, 720p, etc.)."""
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
    def creators_display(self) -> str:
        """Returns comma-separated creator names with media-aware fallbacks."""
        if not self.creators:
            if self.is_video:
                return "Unknown Director"
            elif self.is_audiobook:
                if self.author:
                    return (
                        f"{self.author} (Narrated by {self.narrator})"
                        if self.narrator
                        else self.author
                    )
                return "Unknown Author"
            elif self.is_podcast:
                return "Unknown Host"
            elif self.is_music or self.is_audio:
                return "Unknown Artist"
            return "Unknown Author"

        names = ", ".join(c.name for c in self.creators)
        if self.is_audiobook and self.narrator:
            return f"{names} (Narrated by {self.narrator})"
        return names

    @property
    def authors_display(self) -> str:
        """Alias for creators_display."""
        return self.creators_display

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
        elif self.media_type == MediaType.AUDIOBOOK.value:
            return f"/reader/audiobook/{self.id}"
        elif self.media_type == MediaType.MUSIC.value:
            return f"/reader/music/{self.id}"
        elif self.media_type == MediaType.PODCAST.value:
            return f"/reader/podcast/{self.id}"
        elif (self.file_format or "").lower() in (
            "mp3",
            "m4a",
            "m4b",
            "flac",
            "ogg",
            "opus",
            "wav",
            "aac",
        ):
            return f"/reader/audio/{self.id}"
        elif self.is_video:
            return f"/reader/video/{self.id}"
        return f"/reader/epub/{self.id}"

    @property
    def reader_url(self) -> str:
        """Alias for player_url."""
        return self.player_url

    def __repr__(self) -> str:
        return f"<MediaItem {self.id}: {self.title}>"


Book = MediaItem
Item = MediaItem

db.Model.registry._class_registry["MediaItem"] = MediaItem
db.Model.registry._class_registry["Book"] = MediaItem
db.Model.registry._class_registry["Item"] = MediaItem
