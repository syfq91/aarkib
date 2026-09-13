from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from aarkib.plugins.base import MediaPlugin
from aarkib.services.parsers.base import BaseParsedMetadata

if TYPE_CHECKING:
    from flask import Blueprint, Flask


class PodcastMediaPlugin(MediaPlugin):
    """Built-in media plugin for local podcast episodes and episodic audio shows."""

    name = "podcast"
    media_type = "podcast"
    supported_extensions: ClassVar[set[str]] = {
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

    def get_playback_info(
        self, item: Any, user_id: int | None = None
    ) -> dict[str, Any]:
        """Returns podcast playback descriptor including feed URL, episode type, and GUID."""
        info = super().get_playback_info(item, user_id=user_id)
        info.update(
            {
                "playback_strategy": "direct_play",
                "stream_url": f"/api/media/{item.id}/stream",
                "episode_type": getattr(item, "episode_type", None),
                "podcast_feed_url": getattr(item, "podcast_feed_url", None),
                "podcast_guid": getattr(item, "podcast_guid", None),
                "season": getattr(item, "season", None),
                "episode": getattr(item, "episode", None),
            }
        )
        return info

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        return None

    def check_health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "plugin": self.name,
            "media_type": self.media_type,
            "supported_extensions": sorted(self.supported_extensions),
        }
