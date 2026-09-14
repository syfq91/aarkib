from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class MediaType(StrEnum):
    """Supported and planned media types for Aarkib."""

    BOOK = "book"
    COMIC = "comic"
    AUDIO = "audio"
    VIDEO = "video"
    MOVIE = "movie"
    TV = "tv"
    AUDIOBOOK = "audiobook"
    MUSIC = "music"
    PODCAST = "podcast"


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
    file_mtime: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    cover_image_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Generalized metadata
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default="en"
    )
    publication_date: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # External metadata & field locking
    external_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    locked_fields: Mapped[str | None] = mapped_column(Text, nullable=True)

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
        if self.media_type == MediaType.BOOK.value:
            return not self.is_comic
        if self.media_type:
            return self.media_type == MediaType.BOOK.value
        return (self.file_format or "").lower() not in ("cbz", "cbr", "zip")

    @property
    def is_comic(self) -> bool:
        """Check if this item is a comic book or graphic novel."""
        if self.media_type == MediaType.COMIC.value:
            return True
        return (self.file_format or "").lower() in ("cbz", "cbr", "zip")

    @property
    def is_audio(self) -> bool:
        """Check if this item is an audio track, music, or audiobook."""
        if self.media_type:
            return self.media_type in (
                MediaType.AUDIO.value,
                MediaType.AUDIOBOOK.value,
                MediaType.MUSIC.value,
                MediaType.PODCAST.value,
            )
        return (self.file_format or "").lower() in (
            "mp3",
            "m4a",
            "m4b",
            "flac",
            "ogg",
            "opus",
            "wav",
            "aac",
        )

    @property
    def is_audiobook(self) -> bool:
        """Check if this item is specifically an audiobook."""
        if self.media_type == MediaType.AUDIOBOOK.value:
            return True
        if self.media_type in (MediaType.AUDIO.value, None, ""):
            return (self.file_format or "").lower() == "m4b"
        return False

    @property
    def is_music(self) -> bool:
        """Check if this item is specifically a music track."""
        if self.media_type == MediaType.MUSIC.value:
            return True
        return self.is_audio and not self.is_audiobook

    @property
    def is_podcast(self) -> bool:
        """Check if this item is a podcast episode."""
        return self.media_type == MediaType.PODCAST.value

    @property
    def is_video(self) -> bool:
        """Check if this item is a video, movie, or TV show."""
        if self.media_type:
            return self.media_type in (
                MediaType.VIDEO.value,
                MediaType.MOVIE.value,
                MediaType.TV.value,
            )
        return (self.file_format or "").lower() in (
            "mp4",
            "mkv",
            "webm",
            "avi",
            "mov",
            "m4v",
        )

    @property
    def is_movie(self) -> bool:
        """Check if this item is specifically a movie."""
        return self.media_type in (MediaType.MOVIE.value, "movie")

    @property
    def is_tv(self) -> bool:
        """Check if this item is specifically a TV show episode."""
        return self.media_type in (MediaType.TV.value, "tv")

    @property
    def formatted_file_size(self) -> str:
        """Human-readable representation of the file size."""
        size = float(self.file_size or 0)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0 or unit == "TB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024.0
        return f"{self.file_size} B"

    def get_locked_fields(self) -> list[str]:
        """Returns list of locked field names."""
        if not self.locked_fields:
            return []
        try:
            val = json.loads(self.locked_fields)
            return [str(item) for item in val] if isinstance(val, list) else []
        except Exception:
            return [f.strip() for f in self.locked_fields.split(",") if f.strip()]

    def set_locked_fields(self, fields: list[str]) -> None:
        """Sets list of locked field names."""
        cleaned = sorted({str(f).strip() for f in fields if f and str(f).strip()})
        self.locked_fields = json.dumps(cleaned) if cleaned else None

    def is_field_locked(self, field_name: str) -> bool:
        """Checks if a field is locked from automatic updates."""
        return field_name in self.get_locked_fields()

    def lock_field(self, field_name: str) -> None:
        """Locks a specific field name."""
        current = set(self.get_locked_fields())
        current.add(field_name)
        self.set_locked_fields(list(current))

    def unlock_field(self, field_name: str) -> None:
        """Unlocks a specific field name."""
        current = set(self.get_locked_fields())
        current.discard(field_name)
        self.set_locked_fields(list(current))


class PlayableItemMixin:
    """Shared playback attributes for audio and video media."""

    duration: Mapped[float | None] = mapped_column(nullable=True)  # in seconds
    bitrate: Mapped[int | None] = mapped_column(Integer, nullable=True)  # in kbps


class AudiobookItemMixin(PlayableItemMixin):
    """Blueprint mixin for audiobook metadata attributes."""

    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    narrator: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    chapters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    abridged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    @property
    def chapters(self) -> list[dict[str, Any]]:
        """Returns parsed list of chapter dictionaries."""
        if not self.chapters_json:
            return []
        try:
            data = json.loads(self.chapters_json)
            return data if isinstance(data, list) else []
        except Exception:
            return []


class AudioTrackMixin(PlayableItemMixin):
    """Blueprint mixin for music track metadata attributes."""

    album: Mapped[str | None] = mapped_column(String(255), nullable=True)
    album_artist: Mapped[str | None] = mapped_column(String(255), nullable=True)
    track_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disc_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    release_year: Mapped[str | None] = mapped_column(String(10), nullable=True)
    genre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_compilation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class VideoItemMixin(PlayableItemMixin):
    """Blueprint mixin for video media metadata attributes."""

    resolution_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(50), nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PodcastItemMixin(PlayableItemMixin):
    """Blueprint mixin for podcast episode metadata attributes."""

    episode_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    podcast_feed_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    podcast_guid: Mapped[str | None] = mapped_column(
        String(500), nullable=True, index=True
    )
