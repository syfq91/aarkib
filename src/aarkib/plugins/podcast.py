from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aarkib.plugins.base import MediaPlugin
from aarkib.services.parsers.base import BaseParsedMetadata

if TYPE_CHECKING:
    from flask import Blueprint, Flask


class PodcastMediaPlugin(MediaPlugin):
    """Built-in media plugin for local podcast episodes and episodic audio shows."""

    name = "podcast"
    media_type = "podcast"
    supported_extensions = {
        ".mp3",
        ".m4a",
        ".ogg",
        ".opus",
        ".aac",
    }

    def parse_metadata(self, file_path: Path) -> BaseParsedMetadata | None:
        """Parses podcast episode metadata from ID3/MP4 tags and filename heuristics."""
        from aarkib.services.parsers.podcast import parse_podcast

        return parse_podcast(file_path)

    def extract_cover(self, file_path: Path) -> bytes | None:
        """Extracts embedded podcast artwork or neighboring folder/cover art."""
        from aarkib.services.parsers.audio import extract_audio_cover

        return extract_audio_cover(file_path)

    def get_player_url(
        self, item_id: int, file_format: str | None = None
    ) -> str | None:
        """Returns the dedicated podcast player URL."""
        return f"/reader/podcast/{item_id}"

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        return None

    def check_health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "plugin": self.name,
            "media_type": self.media_type,
            "supported_extensions": sorted(self.supported_extensions),
        }
