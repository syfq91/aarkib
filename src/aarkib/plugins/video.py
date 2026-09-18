from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from aarkib.plugins.base import MediaPlugin
from aarkib.services.parsers.base import BaseParsedMetadata

if TYPE_CHECKING:
    from flask import Blueprint, Flask


class VideoMediaPlugin(MediaPlugin):
    """Built-in media plugin for movies and TV show videos (MP4, MKV, WEBM, AVI, MOV, M4V)."""

    name = "video"
    media_type = "video"
    supported_media_types: ClassVar[set[str]] = {"video", "movie", "tv"}
    supported_extensions: ClassVar[set[str]] = {
        ".mp4",
        ".mkv",
        ".webm",
        ".avi",
        ".mov",
        ".m4v",
    }
    config_keys: ClassVar[list[str]] = ["TMDB_API_KEY"]

    def parse_metadata(self, file_path: Path) -> BaseParsedMetadata | None:
        """Parses video metadata from container and filename."""
        from aarkib.services.parsers.video import parse_video

        return parse_video(file_path)

    def extract_cover(self, file_path: Path) -> bytes | None:
        """Extracts poster/cover bytes or video thumbnail frame."""
        from aarkib.services.parsers.video import extract_video_cover

        return extract_video_cover(file_path)

    def get_player_url(
        self, item_id: int, file_format: str | None = None
    ) -> str | None:
        """Returns the in-browser video player URL."""
        return f"/reader/video/{item_id}"

    def get_playback_info(
        self, item: Any, user_id: int | None = None
    ) -> dict[str, Any]:
        """Returns video playback descriptor including transcoding strategy and stream URLs."""
        info = super().get_playback_info(item, user_id=user_id)
        from aarkib.services.playback_service import playback_service

        strategy = "direct_play"
        plan = None
        file_path_str = getattr(item, "original_file_path", None)
        if file_path_str:
            file_p = Path(file_path_str)
            if file_p.exists():
                try:
                    plan = playback_service.plan_for_file(
                        file_path=file_p,
                        media_item_id=getattr(item, "id", None),
                        media_type=getattr(item, "media_type", None),
                    )
                    if plan.mode.value == "direct":
                        strategy = "direct_play"
                    elif plan.mode.value == "remux":
                        strategy = "direct_remux"
                    elif plan.mode.value == "transcode":
                        strategy = (
                            "audio_transcode"
                            if plan.diagnostics.get("copy_video")
                            else "full_transcode"
                        )
                except Exception:
                    pass

        info.update(
            {
                "playback_strategy": strategy,
                "plan": plan.to_dict() if plan else None,
                "playback_plan": plan.to_dict() if plan else None,
                "stream_url": f"/api/media/{item.id}/stream",
                "hls_url": f"/api/media/{item.id}/hls/master.m3u8",
                "mime_type": "video/mp4",
                "resolution_width": getattr(item, "resolution_width", None),
                "resolution_height": getattr(item, "resolution_height", None),
                "codec": getattr(item, "codec", None),
                "season": getattr(item, "season", None),
                "episode": getattr(item, "episode", None),
            }
        )
        return info

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        """Video routes are served under the reader blueprint."""
        return None

    def check_health(self) -> dict[str, Any]:
        """Performs health check for video plugin."""
        from aarkib.config import get_ffmpeg_binary, get_ffprobe_binary

        res = super().check_health()
        res.update(
            {
                "ffmpeg_available": bool(get_ffmpeg_binary()),
                "ffprobe_available": bool(get_ffprobe_binary()),
            }
        )
        return res


class MovieMediaPlugin(VideoMediaPlugin):
    """Built-in media plugin for movies (MP4, MKV, WEBM, AVI, MOV, M4V)."""

    name = "movie"
    media_type = "movie"


class TVMediaPlugin(VideoMediaPlugin):
    """Built-in media plugin for TV shows and series (MP4, MKV, WEBM, AVI, MOV, M4V)."""

    name = "tv"
    media_type = "tv"
