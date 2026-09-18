from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VideoCapabilities:
    """Supported video codecs, containers, and display parameters."""

    codecs: set[str] = field(default_factory=set)
    containers: set[str] = field(default_factory=set)
    max_width: int | None = None
    max_height: int | None = None
    supports_10bit: bool = False
    supports_hdr: bool = False
    max_bitrate: int | None = None  # in kbps

    def to_dict(self) -> dict[str, Any]:
        return {
            "codecs": sorted(self.codecs),
            "containers": sorted(self.containers),
            "max_width": self.max_width,
            "max_height": self.max_height,
            "supports_10bit": self.supports_10bit,
            "supports_hdr": self.supports_hdr,
            "max_bitrate": self.max_bitrate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VideoCapabilities:
        codecs = set(data.get("codecs") or [])
        containers = {
            c if c.startswith(".") else f".{c}" for c in (data.get("containers") or [])
        }
        return cls(
            codecs={c.lower() for c in codecs},
            containers={c.lower() for c in containers},
            max_width=data.get("max_width"),
            max_height=data.get("max_height"),
            supports_10bit=bool(data.get("supports_10bit", False)),
            supports_hdr=bool(data.get("supports_hdr", False)),
            max_bitrate=data.get("max_bitrate"),
        )


@dataclass
class AudioCapabilities:
    """Supported audio codecs, containers, and channel configurations."""

    codecs: set[str] = field(default_factory=set)
    containers: set[str] = field(default_factory=set)
    max_channels: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "codecs": sorted(self.codecs),
            "containers": sorted(self.containers),
            "max_channels": self.max_channels,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioCapabilities:
        codecs = set(data.get("codecs") or [])
        containers = {
            c if c.startswith(".") else f".{c}" for c in (data.get("containers") or [])
        }
        return cls(
            codecs={c.lower() for c in codecs},
            containers={c.lower() for c in containers},
            max_channels=int(data.get("max_channels", 2)),
        )


@dataclass
class SubtitleCapabilities:
    """Supported subtitle formats and rendering features."""

    formats: set[str] = field(default_factory=set)
    supports_ass_wasm: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "formats": sorted(self.formats),
            "supports_ass_wasm": self.supports_ass_wasm,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubtitleCapabilities:
        formats = {f.lstrip(".").lower() for f in (data.get("formats") or []) if f}
        return cls(
            formats=formats,
            supports_ass_wasm=bool(data.get("supports_ass_wasm", False)),
        )


@dataclass
class StreamingCapabilities:
    """Supported streaming transport and seeking capabilities."""

    supports_hls: bool = True
    supports_byte_range: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "supports_hls": self.supports_hls,
            "supports_byte_range": self.supports_byte_range,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamingCapabilities:
        return cls(
            supports_hls=bool(data.get("supports_hls", True)),
            supports_byte_range=bool(data.get("supports_byte_range", True)),
        )


@dataclass
class DeviceCapabilities:
    """Client device identification and physical characteristics."""

    client_type: str = (
        "unknown"  # browser, jellyfin, subsonic, koreader, native, unknown
    )
    client_name: str = "Unknown"
    platform: str = "unknown"  # Linux, Windows, macOS, iOS, Android, E-Ink, unknown
    is_mobile: bool = False
    is_eink: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_type": self.client_type,
            "client_name": self.client_name,
            "platform": self.platform,
            "is_mobile": self.is_mobile,
            "is_eink": self.is_eink,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceCapabilities:
        return cls(
            client_type=str(data.get("client_type", "unknown")),
            client_name=str(data.get("client_name", "Unknown")),
            platform=str(data.get("platform", "unknown")),
            is_mobile=bool(data.get("is_mobile", False)),
            is_eink=bool(data.get("is_eink", False)),
        )


@dataclass
class ClientCapabilities:
    """Aggregated client playback and device capabilities."""

    video: VideoCapabilities = field(default_factory=VideoCapabilities)
    audio: AudioCapabilities = field(default_factory=AudioCapabilities)
    subtitles: SubtitleCapabilities = field(default_factory=SubtitleCapabilities)
    streaming: StreamingCapabilities = field(default_factory=StreamingCapabilities)
    device: DeviceCapabilities = field(default_factory=DeviceCapabilities)

    def supports_video(
        self,
        codec: str,
        container: str | None = None,
        is_10bit: bool = False,
        width: int | None = None,
        height: int | None = None,
    ) -> bool:
        """Determines if the client natively supports the specified video stream parameters."""
        c = codec.strip().lower()
        if c not in self.video.codecs:
            return False

        if container:
            normalized_container = (
                container.lower()
                if container.startswith(".")
                else f".{container.lower()}"
            )
            if normalized_container not in self.video.containers:
                return False

        if is_10bit and not self.video.supports_10bit:
            return False

        if (
            width is not None
            and self.video.max_width is not None
            and width > self.video.max_width
        ):
            return False

        if (
            height is not None
            and self.video.max_height is not None
            and height > self.video.max_height
        ):
            return False

        return True

    def supports_audio(
        self,
        codec: str,
        container: str | None = None,
        channels: int | None = None,
    ) -> bool:
        """Determines if the client natively supports the specified audio stream parameters."""
        c = codec.strip().lower()
        if c not in self.audio.codecs:
            return False

        if container:
            normalized_container = (
                container.lower()
                if container.startswith(".")
                else f".{container.lower()}"
            )
            if normalized_container not in self.audio.containers:
                return False

        if channels is not None and channels > self.audio.max_channels:
            return False

        return True

    def supports_subtitles(self, format_name: str) -> bool:
        """Determines if the client supports the subtitle format (native or WASM-rendered)."""
        fmt = format_name.strip().lstrip(".").lower()
        if fmt in ("ass", "ssa"):
            return "ass" in self.subtitles.formats or self.subtitles.supports_ass_wasm
        return fmt in self.subtitles.formats

    def to_dict(self) -> dict[str, Any]:
        """Serializes capabilities to a JSON-compatible dictionary."""
        return {
            "video": self.video.to_dict(),
            "audio": self.audio.to_dict(),
            "subtitles": self.subtitles.to_dict(),
            "streaming": self.streaming.to_dict(),
            "device": self.device.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClientCapabilities:
        """Constructs a ClientCapabilities instance from dictionary data with defaults."""
        video_data = data.get("video") if isinstance(data.get("video"), dict) else {}
        audio_data = data.get("audio") if isinstance(data.get("audio"), dict) else {}
        sub_data = (
            data.get("subtitles") if isinstance(data.get("subtitles"), dict) else {}
        )
        stream_data = (
            data.get("streaming") if isinstance(data.get("streaming"), dict) else {}
        )
        device_data = data.get("device") if isinstance(data.get("device"), dict) else {}

        return cls(
            video=VideoCapabilities.from_dict(video_data),
            audio=AudioCapabilities.from_dict(audio_data),
            subtitles=SubtitleCapabilities.from_dict(sub_data),
            streaming=StreamingCapabilities.from_dict(stream_data),
            device=DeviceCapabilities.from_dict(device_data),
        )
