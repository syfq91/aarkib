from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PlaybackMode(StrEnum):
    """Delivery modes for media playback."""

    DIRECT = "direct"
    REMUX = "remux"
    TRANSCODE = "transcode"
    OPTIMIZE = "optimize"


@dataclass(frozen=True)
class PlaybackPlan:
    """Immutable, deterministic media playback delivery plan.

    Describes what format, codecs, container, and stream endpoints the server
    intends to deliver to a given client based on client capabilities and media metadata.
    Does not execute transcoding or delivery subprocesses.
    """

    mode: PlaybackMode
    container: str
    video_codec: str | None = None
    audio_codec: str | None = None
    resolution: str | None = None
    target_bitrate: int | None = None  # in kbps
    reasons: tuple[str, ...] = ()
    stream_url: str = ""
    direct_url: str | None = None
    subtitles: tuple[dict[str, Any], ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serializes the playback plan into a JSON-compatible dictionary."""
        return {
            "mode": self.mode.value
            if isinstance(self.mode, PlaybackMode)
            else str(self.mode),
            "container": self.container,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "resolution": self.resolution,
            "target_bitrate": self.target_bitrate,
            "reasons": list(self.reasons),
            "stream_url": self.stream_url,
            "direct_url": self.direct_url,
            "subtitles": [dict(s) for s in self.subtitles],
            "diagnostics": dict(self.diagnostics),
        }
