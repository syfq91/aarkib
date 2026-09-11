from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aarkib.plugins.base import MediaPlugin
from aarkib.services.parsers.base import BaseParsedMetadata

if TYPE_CHECKING:
    from flask import Blueprint, Flask


class AudioMediaPlugin(MediaPlugin):
    """Built-in media plugin for audio tracks, music, and audiobooks."""

    name = "audio"
    media_type = "audio"
    supported_extensions = {
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
