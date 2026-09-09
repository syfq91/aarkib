from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class MediaType(StrEnum):
    """Supported and planned media types for Aarkib."""

    BOOK = "book"
    COMIC = "comic"
    AUDIO = "audio"
    VIDEO = "video"


class MediaItemMixin:
    """Declarative mixin defining standard attributes shared by all media items.

    This abstraction provides the foundation for multi-media support (books, comics,
    audio, and video) while maintaining a uniform interface for metadata, storage,
    indexing, and sorting across the platform.
    """

    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    sort_title: Mapped[str | None] = mapped_column(
        String(500), nullable=True, index=True
    )
    media_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default=MediaType.BOOK.value, index=True
    )

    # Physical file storage & integrity
    original_file_path: Mapped[str] = mapped_column(
        String(1000), unique=True, nullable=False
    )
    file_format: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    cover_image_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Generalized metadata
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default="en"
    )
    publication_date: Mapped[str | None] = mapped_column(String(50), nullable=True)

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

    @property
    def is_book(self) -> bool:
        """Check if this item is a text book."""
        if self.media_type:
            return self.media_type == MediaType.BOOK.value
        return (self.file_format or "").lower() not in ("cbz", "cbr", "zip")

    @property
    def is_comic(self) -> bool:
        """Check if this item is a comic book or graphic novel."""
        if self.media_type:
            return self.media_type == MediaType.COMIC.value
        return (self.file_format or "").lower() in ("cbz", "cbr", "zip")

    @property
    def is_audio(self) -> bool:
        """Check if this item is an audio track or audiobook."""
        if self.media_type:
            return self.media_type == MediaType.AUDIO.value
        return (self.file_format or "").lower() in (
            "mp3",
            "m4a",
            "flac",
            "ogg",
            "opus",
            "wav",
            "aac",
        )

    @property
    def is_video(self) -> bool:
        """Check if this item is a video or movie."""
        return self.media_type == MediaType.VIDEO.value

    @property
    def formatted_file_size(self) -> str:
        """Human-readable representation of the file size."""
        size = float(self.file_size or 0)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0 or unit == "TB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024.0
        return f"{self.file_size} B"


class PlayableItemMixin:
    """Shared playback attributes for audio and video media."""

    duration: Mapped[float | None] = mapped_column(nullable=True)  # in seconds
    bitrate: Mapped[int | None] = mapped_column(Integer, nullable=True)  # in kbps


class AudioTrackMixin(PlayableItemMixin):
    """Blueprint mixin for audio track metadata attributes."""

    album: Mapped[str | None] = mapped_column(String(255), nullable=True)
    track_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disc_number: Mapped[int | None] = mapped_column(Integer, nullable=True)


class VideoItemMixin(PlayableItemMixin):
    """Blueprint mixin for video media metadata attributes."""

    resolution_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(50), nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
