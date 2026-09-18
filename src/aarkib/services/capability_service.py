from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from aarkib.models.capabilities import (
    AudioCapabilities,
    ClientCapabilities,
    DeviceCapabilities,
    StreamingCapabilities,
    SubtitleCapabilities,
    VideoCapabilities,
)

if TYPE_CHECKING:
    from flask import Request

logger = logging.getLogger(__name__)


class CapabilityService:
    """Detects, normalizes, and negotiates client media capabilities from HTTP requests."""

    HEADER_OVERRIDE: str = "X-Aarkib-Capabilities"
    QUERY_PARAM_OVERRIDE: str = "capabilities"

    def detect(
        self,
        request: Request | None = None,
        override_headers: dict[str, str] | None = None,
        override_json: dict[str, Any] | None = None,
    ) -> ClientCapabilities:
        """Determines client capabilities from explicit overrides, headers, or User-Agent heuristics."""
        # 1. Explicit JSON override takes top precedence
        if override_json:
            try:
                return ClientCapabilities.from_dict(override_json)
            except Exception as e:
                logger.warning("Failed parsing explicit override_json: %s", e)

        # 2. Extract headers dictionary
        headers: dict[str, str] = {}
        query_caps: str | None = None

        if request is None:
            try:
                from flask import has_request_context
                from flask import request as flask_req

                if has_request_context():
                    request = flask_req
            except Exception:
                pass

        if request is not None:
            # Flask request.headers is case-insensitive
            for k, v in request.headers.items():
                headers[k.lower()] = v
            query_caps = request.args.get(
                self.QUERY_PARAM_OVERRIDE
            ) or request.args.get("caps")

        if override_headers:
            for k, v in override_headers.items():
                headers[k.lower()] = v

        # 3. Check for X-Aarkib-Capabilities header override
        header_val = headers.get(self.HEADER_OVERRIDE.lower())
        if header_val:
            try:
                parsed = json.loads(header_val)
                if isinstance(parsed, dict):
                    return ClientCapabilities.from_dict(parsed)
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug("Malformed %s header: %s", self.HEADER_OVERRIDE, e)

        # 4. Check for query parameter override
        if query_caps:
            try:
                parsed = json.loads(query_caps)
                if isinstance(parsed, dict):
                    return ClientCapabilities.from_dict(parsed)
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(
                    "Malformed %s query parameter: %s", self.QUERY_PARAM_OVERRIDE, e
                )

        # 5. Inspect User-Agent and Client Hints
        user_agent = headers.get("user-agent", "").strip()
        return self._detect_from_user_agent(user_agent, headers)

    def _detect_from_user_agent(
        self,
        user_agent: str,
        headers: dict[str, str],
    ) -> ClientCapabilities:
        """Inspects User-Agent string and Client Hints to profile the connecting client."""
        if not user_agent:
            return self._profile_baseline()

        ua_lower = user_agent.lower()

        # KOReader e-reader
        if "koreader" in ua_lower:
            return self._profile_koreader(user_agent)

        # Subsonic / OpenSubsonic audio streaming clients
        if any(
            sub_client in ua_lower
            for sub_client in (
                "subsonic",
                "dsub",
                "symfonium",
                "ultrasonic",
                "finamp",
                "play:sub",
            )
        ):
            return self._profile_subsonic(user_agent, headers)

        # Jellyfin / Emby clients
        if (
            any(
                jf_client in ua_lower
                for jf_client in ("jellyfin", "emby", "findroid", "streamyfin")
            )
            or "x-emby-client" in headers
            or "x-jellyfin-client" in headers
        ):
            return self._profile_jellyfin(user_agent, headers)

        # Platform detection
        platform = "unknown"
        if "windows" in ua_lower:
            platform = "Windows"
        elif "macintosh" in ua_lower or "mac os x" in ua_lower:
            platform = "macOS"
        elif "iphone" in ua_lower:
            platform = "iOS"
        elif "ipad" in ua_lower:
            platform = "iPadOS"
        elif "android" in ua_lower:
            platform = "Android"
        elif "linux" in ua_lower:
            platform = "Linux"

        sec_ch_platform = headers.get("sec-ch-ua-platform", "").replace('"', "")
        if sec_ch_platform:
            platform = sec_ch_platform

        is_mobile = headers.get("sec-ch-ua-mobile") == "?1" or any(
            m in ua_lower for m in ("mobile", "android", "iphone", "ipod")
        )

        # Web Browsers
        # Chromium (Chrome, Edge, Brave, Opera, Vivaldi)
        # Note: Edge contains "Edg/", Opera contains "OPR/", Chrome contains "Chrome/"
        if "chrome/" in ua_lower or "chromium" in ua_lower:
            client_name = "Chrome"
            if "edg/" in ua_lower:
                client_name = "Edge"
            elif "opr/" in ua_lower or "opera" in ua_lower:
                client_name = "Opera"
            elif "brave" in ua_lower:
                client_name = "Brave"
            elif "vivaldi" in ua_lower:
                client_name = "Vivaldi"

            return self._profile_chromium(
                client_name=client_name,
                platform=platform,
                is_mobile=is_mobile,
            )

        # Firefox
        if "firefox/" in ua_lower or "fxios/" in ua_lower:
            return self._profile_firefox(
                platform=platform,
                is_mobile=is_mobile,
            )

        # Safari / WebKit (must not contain Chrome/Chromium)
        if "safari/" in ua_lower and "version/" in ua_lower:
            return self._profile_safari(
                platform=platform,
                is_mobile=is_mobile,
            )

        # Fallback to universal conservative baseline
        baseline = self._profile_baseline()
        baseline.device.platform = platform
        baseline.device.is_mobile = is_mobile
        return baseline

    def _profile_chromium(
        self,
        client_name: str = "Chrome",
        platform: str = "unknown",
        is_mobile: bool = False,
    ) -> ClientCapabilities:
        """Capability profile for modern Chromium-based browsers."""
        return ClientCapabilities(
            video=VideoCapabilities(
                codecs={"h264", "avc", "avc1", "vp8", "vp9", "av1"},
                containers={".mp4", ".m4v", ".webm"},
                max_width=3840,
                max_height=2160,
                supports_10bit=True,  # VP9 profile 2 and AV1 10-bit
                supports_hdr=True,
            ),
            audio=AudioCapabilities(
                codecs={"aac", "mp3", "opus", "flac", "vorbis", "wav"},
                containers={
                    ".mp4",
                    ".m4a",
                    ".webm",
                    ".ogg",
                    ".opus",
                    ".mp3",
                    ".flac",
                    ".wav",
                },
                max_channels=6,
            ),
            subtitles=SubtitleCapabilities(
                formats={"vtt", "ass", "ssa"},
                supports_ass_wasm=True,  # JASSUB WebAssembly renderer
            ),
            streaming=StreamingCapabilities(
                supports_hls=True,  # via hls.js
                supports_byte_range=True,
            ),
            device=DeviceCapabilities(
                client_type="browser",
                client_name=client_name,
                platform=platform,
                is_mobile=is_mobile,
                is_eink=False,
            ),
        )

    def _profile_firefox(
        self,
        platform: str = "unknown",
        is_mobile: bool = False,
    ) -> ClientCapabilities:
        """Capability profile for Mozilla Firefox."""
        return ClientCapabilities(
            video=VideoCapabilities(
                codecs={"h264", "avc", "avc1", "vp8", "vp9", "av1"},
                containers={".mp4", ".m4v", ".webm"},
                max_width=3840,
                max_height=2160,
                supports_10bit=True,
                supports_hdr=False,
            ),
            audio=AudioCapabilities(
                codecs={"aac", "mp3", "opus", "flac", "vorbis", "wav"},
                containers={
                    ".mp4",
                    ".m4a",
                    ".webm",
                    ".ogg",
                    ".opus",
                    ".mp3",
                    ".flac",
                    ".wav",
                },
                max_channels=6,
            ),
            subtitles=SubtitleCapabilities(
                formats={"vtt", "ass", "ssa"},
                supports_ass_wasm=True,
            ),
            streaming=StreamingCapabilities(
                supports_hls=True,  # via hls.js
                supports_byte_range=True,
            ),
            device=DeviceCapabilities(
                client_type="browser",
                client_name="Firefox",
                platform=platform,
                is_mobile=is_mobile,
                is_eink=False,
            ),
        )

    def _profile_safari(
        self,
        platform: str = "macOS",
        is_mobile: bool = False,
    ) -> ClientCapabilities:
        """Capability profile for Apple Safari / WebKit."""
        return ClientCapabilities(
            video=VideoCapabilities(
                codecs={"h264", "avc", "avc1", "hevc", "h265", "mp4v"},
                containers={".mp4", ".m4v", ".mov"},
                max_width=3840,
                max_height=2160,
                supports_10bit=True,  # HEVC Main10 hardware decoding on Apple Silicon
                supports_hdr=True,
            ),
            audio=AudioCapabilities(
                codecs={"aac", "mp3", "flac", "alac", "wav"},
                containers={".mp4", ".m4a", ".mp3", ".wav", ".flac"},
                max_channels=6,
            ),
            subtitles=SubtitleCapabilities(
                formats={"vtt", "ass", "ssa"},
                supports_ass_wasm=True,
            ),
            streaming=StreamingCapabilities(
                supports_hls=True,  # Native Apple HLS
                supports_byte_range=True,
            ),
            device=DeviceCapabilities(
                client_type="browser",
                client_name="Safari",
                platform=platform,
                is_mobile=is_mobile,
                is_eink=False,
            ),
        )

    def _profile_koreader(self, user_agent: str) -> ClientCapabilities:
        """Capability profile for KOReader e-ink document viewer."""
        return ClientCapabilities(
            video=VideoCapabilities(
                codecs=set(),
                containers=set(),
            ),
            audio=AudioCapabilities(
                codecs={"mp3"},
                containers={".mp3"},
                max_channels=2,
            ),
            subtitles=SubtitleCapabilities(formats=set()),
            streaming=StreamingCapabilities(
                supports_hls=False,
                supports_byte_range=True,
            ),
            device=DeviceCapabilities(
                client_type="koreader",
                client_name="KOReader",
                platform="E-Ink",
                is_mobile=False,
                is_eink=True,
            ),
        )

    def _profile_subsonic(
        self,
        user_agent: str,
        headers: dict[str, str],
    ) -> ClientCapabilities:
        """Capability profile for OpenSubsonic audio streaming applications."""
        client_name = "Subsonic Client"
        match = re.search(r"^([a-zA-Z0-9_\-]+)", user_agent)
        if match:
            client_name = match.group(1)

        return ClientCapabilities(
            video=VideoCapabilities(
                codecs=set(),
                containers=set(),
            ),
            audio=AudioCapabilities(
                codecs={"mp3", "aac", "flac", "opus", "ogg", "wav"},
                containers={".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav"},
                max_channels=2,
            ),
            subtitles=SubtitleCapabilities(formats=set()),
            streaming=StreamingCapabilities(
                supports_hls=False,
                supports_byte_range=True,
            ),
            device=DeviceCapabilities(
                client_type="subsonic",
                client_name=client_name,
                platform="unknown",
                is_mobile=True,
                is_eink=False,
            ),
        )

    def _profile_jellyfin(
        self,
        user_agent: str,
        headers: dict[str, str],
    ) -> ClientCapabilities:
        """Capability profile for Jellyfin / Emby ecosystem clients."""
        client_name = (
            headers.get("x-emby-client")
            or headers.get("x-jellyfin-client")
            or "Jellyfin Client"
        )

        return ClientCapabilities(
            video=VideoCapabilities(
                codecs={"h264", "avc", "hevc", "h265", "vp8", "vp9", "av1", "mpeg4"},
                containers={".mp4", ".mkv", ".webm", ".ts", ".m4v"},
                max_width=3840,
                max_height=2160,
                supports_10bit=True,
                supports_hdr=True,
            ),
            audio=AudioCapabilities(
                codecs={"aac", "mp3", "flac", "opus", "vorbis", "ac3", "eac3", "wav"},
                containers={".mp4", ".m4a", ".mkv", ".ogg", ".mp3", ".flac", ".wav"},
                max_channels=8,
            ),
            subtitles=SubtitleCapabilities(
                formats={"vtt", "ass", "ssa", "srt", "subrip"},
                supports_ass_wasm=True,
            ),
            streaming=StreamingCapabilities(
                supports_hls=True,
                supports_byte_range=True,
            ),
            device=DeviceCapabilities(
                client_type="jellyfin",
                client_name=client_name,
                platform="unknown",
                is_mobile=False,
                is_eink=False,
            ),
        )

    def _profile_baseline(self) -> ClientCapabilities:
        """Conservative universal fallback profile compatible with virtually all clients."""
        return ClientCapabilities(
            video=VideoCapabilities(
                codecs={"h264", "avc", "avc1"},
                containers={".mp4", ".m4v"},
                max_width=1920,
                max_height=1080,
                supports_10bit=False,
                supports_hdr=False,
            ),
            audio=AudioCapabilities(
                codecs={"aac", "mp3"},
                containers={".mp4", ".mp3", ".m4a"},
                max_channels=2,
            ),
            subtitles=SubtitleCapabilities(
                formats={"vtt"},
                supports_ass_wasm=False,
            ),
            streaming=StreamingCapabilities(
                supports_hls=True,
                supports_byte_range=True,
            ),
            device=DeviceCapabilities(
                client_type="unknown",
                client_name="Unknown",
                platform="unknown",
                is_mobile=False,
                is_eink=False,
            ),
        )


# Singleton instance
capability_service = CapabilityService()


def detect_client_capabilities(
    request: Request | None = None,
    override_headers: dict[str, str] | None = None,
    override_json: dict[str, Any] | None = None,
) -> ClientCapabilities:
    """Convenience helper to detect client capabilities from the active request or overrides."""
    return capability_service.detect(
        request=request,
        override_headers=override_headers,
        override_json=override_json,
    )
