from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from aarkib.plugins.base import MediaPlugin
from aarkib.services.parsers.base import BaseParsedMetadata

if TYPE_CHECKING:
    from flask import Blueprint, Flask


class AudioMediaPlugin(MediaPlugin):
    """Built-in media plugin for generic audio tracks and fallback playback."""

    name = "audio"
    media_type = "audio"
    supported_extensions: ClassVar[set[str]] = {
        ".mp3",
        ".m4a",
        ".m4b",
        ".flac",
        ".ogg",
        ".opus",
        ".wav",
        ".aac",
    }

    def parse_metadata(self, file_path: Path) -> BaseParsedMetadata | None:
        """Parses audio metadata from ID3/FLAC/Vorbis/WAV tags and filename."""
        from aarkib.services.parsers.audio import parse_audio

        return parse_audio(file_path)

    def extract_cover(self, file_path: Path) -> bytes | None:
        """Extracts embedded album artwork or neighboring folder/cover art."""
        from aarkib.services.parsers.audio import extract_audio_cover

        return extract_audio_cover(file_path)

    def get_player_url(
        self, item_id: int, file_format: str | None = None
    ) -> str | None:
        """Returns the in-browser audio player URL."""
        return f"/reader/audio/{item_id}"

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        return None

    def check_health(self) -> dict[str, Any]:
        """Performs health check for audio plugin."""
        return {
            "status": "ok",
            "plugin": self.name,
            "media_type": self.media_type,
            "supported_extensions": sorted(self.supported_extensions),
        }


class AudiobookMediaPlugin(MediaPlugin):
    """Built-in media plugin for dedicated audiobook (.m4b) files and chapter markers."""

    name = "audiobook"
    media_type = "audiobook"
    supported_extensions: ClassVar[set[str]] = {".m4b"}

    def parse_metadata(self, file_path: Path) -> BaseParsedMetadata | None:
        """Parses audiobook metadata and chapter markers."""
        from aarkib.services.parsers.audio import parse_audiobook

        return parse_audiobook(file_path)

    def extract_cover(self, file_path: Path) -> bytes | None:
        """Extracts cover artwork."""
        from aarkib.services.parsers.audio import extract_audio_cover

        return extract_audio_cover(file_path)

    def get_player_url(
        self, item_id: int, file_format: str | None = None
    ) -> str | None:
        """Returns the dedicated audiobook player URL."""
        return f"/reader/audiobook/{item_id}"

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        return None

    def check_health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "plugin": self.name,
            "media_type": self.media_type,
            "supported_extensions": sorted(self.supported_extensions),
        }


class MusicMediaPlugin(MediaPlugin):
    """Built-in media plugin for dedicated music tracks, albums, and playlists."""

    name = "music"
    media_type = "music"
    supported_extensions: ClassVar[set[str]] = {
        ".mp3",
        ".flac",
        ".wav",
        ".ogg",
        ".opus",
        ".aac",
        ".m4a",
    }

    def parse_metadata(self, file_path: Path) -> BaseParsedMetadata | None:
        """Parses music track metadata."""
        from aarkib.services.parsers.audio import parse_music

        return parse_music(file_path)

    def extract_cover(self, file_path: Path) -> bytes | None:
        """Extracts album artwork."""
        from aarkib.services.parsers.audio import extract_audio_cover

        return extract_audio_cover(file_path)

    def get_player_url(
        self, item_id: int, file_format: str | None = None
    ) -> str | None:
        """Returns the music player URL."""
        return f"/reader/music/{item_id}"

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        return None

    def check_health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "plugin": self.name,
            "media_type": self.media_type,
            "supported_extensions": sorted(self.supported_extensions),
        }
