from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aarkib.models.capabilities import ClientCapabilities
from aarkib.models.media import MediaType
from aarkib.models.playback import PlaybackMode, PlaybackPlan

if TYPE_CHECKING:
    from aarkib.models.media_item import MediaItem

logger = logging.getLogger(__name__)

# Standard Web-Native & Remuxable Containers
WEB_NATIVE_CONTAINERS: set[str] = {".mp4", ".m4v", ".webm", ".mp3", ".wav", ".ogg"}
REMUXABLE_CONTAINERS: set[str] = {
    ".mkv",
    ".avi",
    ".mov",
    ".ts",
    ".m2ts",
    ".flv",
    ".wmv",
}

# Standard Web-Native Codecs
WEB_NATIVE_VIDEO_CODECS: set[str] = {"h264", "vp8", "vp9", "av1"}
WEB_NATIVE_AUDIO_CODECS: set[str] = {
    "aac",
    "mp3",
    "opus",
    "vorbis",
    "flac",
    "pcm_s16le",
    "wav",
}

RESOLUTION_PRESETS: dict[str, dict[str, Any]] = {
    "original": {"width": None, "video_bitrate": 4500},
    "1080p": {"width": 1920, "video_bitrate": 4000},
    "720p": {"width": 1280, "video_bitrate": 2200},
    "480p": {"width": 854, "video_bitrate": 1000},
    "360p": {"width": 640, "video_bitrate": 600},
}


class PlaybackService:
    """Deterministic media playback planning service.

    Evaluates client capabilities against media items or file metadata
    to select the optimal delivery strategy (direct, remux, transcode, optimize).
    Does NOT spawn or supervise FFmpeg processes.
    """

    def plan(
        self,
        item: MediaItem,
        capabilities: ClientCapabilities | dict[str, Any] | None = None,
        requested_resolution: str | None = None,
        hwaccel: str = "auto",
        streams_info: dict[str, Any] | None = None,
    ) -> PlaybackPlan:
        """Determines the optimal playback delivery plan for a database MediaItem."""
        file_path = Path(item.original_file_path)
        return self.plan_for_file(
            file_path=file_path,
            media_item_id=item.id,
            media_type=item.media_type,
            capabilities=capabilities,
            requested_resolution=requested_resolution,
            hwaccel=hwaccel,
            streams_info=streams_info,
        )

    def plan_for_file(
        self,
        file_path: Path | str,
        media_item_id: int | None = None,
        media_type: str | None = None,
        capabilities: ClientCapabilities | dict[str, Any] | None = None,
        requested_resolution: str | None = None,
        hwaccel: str = "auto",
        streams_info: dict[str, Any] | None = None,
    ) -> PlaybackPlan:
        """Determines the optimal playback delivery plan for a specific media file."""
        file_path = Path(file_path)
        ext = file_path.suffix.lower()

        caps: ClientCapabilities | None = None
        if isinstance(capabilities, ClientCapabilities):
            caps = capabilities
        elif isinstance(capabilities, dict):
            caps = ClientCapabilities.from_dict(capabilities)

        # 1. Books, Comics & E-Ink Documents
        if self._is_book_or_document(ext, media_type):
            return self._plan_book(
                file_path=file_path,
                ext=ext,
                media_item_id=media_item_id,
                capabilities=caps,
            )

        # 2. Audio (Music, Audiobook, Podcast)
        if self._is_audio(ext, media_type):
            return self._plan_audio(
                file_path=file_path,
                ext=ext,
                media_item_id=media_item_id,
                capabilities=caps,
                streams_info=streams_info,
            )

        # 3. Video (Movie, TV, Video)
        return self._plan_video(
            file_path=file_path,
            ext=ext,
            media_item_id=media_item_id,
            capabilities=caps,
            requested_resolution=requested_resolution,
            hwaccel=hwaccel,
            streams_info=streams_info,
        )

    # -------------------------------------------------------------------------
    # Media Classification Helpers
    # -------------------------------------------------------------------------

    def _is_book_or_document(self, ext: str, media_type: str | None) -> bool:
        if media_type in (MediaType.BOOK.value, MediaType.COMIC.value):
            return True
        return ext in {
            ".epub",
            ".cbz",
            ".cbr",
            ".zip",
            ".pdf",
            ".txt",
            ".mobi",
            ".azw3",
        }

    def _is_audio(self, ext: str, media_type: str | None) -> bool:
        if media_type in (
            MediaType.AUDIO.value,
            MediaType.AUDIOBOOK.value,
            MediaType.MUSIC.value,
            MediaType.PODCAST.value,
        ):
            return True
        return ext in {
            ".mp3",
            ".m4a",
            ".m4b",
            ".flac",
            ".ogg",
            ".opus",
            ".wav",
            ".aac",
            ".wma",
            ".aiff",
            ".alac",
            ".ape",
            ".wv",
            ".mka",
        }

    # -------------------------------------------------------------------------
    # Planner Engines
    # -------------------------------------------------------------------------

    def _plan_book(
        self,
        file_path: Path,
        ext: str,
        media_item_id: int | None,
        capabilities: ClientCapabilities | None,
    ) -> PlaybackPlan:
        """Plans delivery for books and comics."""
        clean_ext = ext.lstrip(".")
        direct_url = (
            f"/api/media/{media_item_id}/file" if media_item_id is not None else None
        )

        # Check for E-Ink client reading an EPUB
        if capabilities and capabilities.device.is_eink and ext == ".epub":
            opt_url = (
                f"/api/media/{media_item_id}/optimized"
                if media_item_id is not None
                else ""
            )
            return PlaybackPlan(
                mode=PlaybackMode.OPTIMIZE,
                container="epub",
                reasons=("EPUB optimized for E-Ink screen display",),
                stream_url=opt_url,
                direct_url=direct_url,
                diagnostics={"device": "eink", "format": "epub", "optimized": True},
            )

        # Default direct document delivery
        stream_url = direct_url or ""
        return PlaybackPlan(
            mode=PlaybackMode.DIRECT,
            container=clean_ext,
            reasons=("Direct document delivery",),
            stream_url=stream_url,
            direct_url=direct_url,
            diagnostics={"format": clean_ext, "direct": True},
        )

    def _plan_audio(
        self,
        file_path: Path,
        ext: str,
        media_item_id: int | None,
        capabilities: ClientCapabilities | None,
        streams_info: dict[str, Any] | None,
    ) -> PlaybackPlan:
        """Plans delivery for music, audiobooks, and podcasts."""
        clean_ext = ext.lstrip(".")
        direct_url = (
            f"/api/media/{media_item_id}/file" if media_item_id is not None else None
        )

        if streams_info is None:
            from aarkib.services.transcoder import probe_media_streams

            streams_info = probe_media_streams(file_path)

        audio_list = streams_info.get("audio", [])
        primary_audio = audio_list[0] if audio_list else {}
        a_codec = (primary_audio.get("codec") or clean_ext).lower()

        # Check codec and container compatibility
        container_supported = (
            (ext in capabilities.audio.containers)
            if (capabilities and capabilities.audio.containers)
            else (ext in {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wav", ".opus"})
        )
        if capabilities:
            codec_supported = capabilities.supports_audio(a_codec, container=None)
        else:
            codec_supported = bool(not a_codec or a_codec in WEB_NATIVE_AUDIO_CODECS)

        audio_native = container_supported and codec_supported

        if audio_native:
            stream_url = direct_url or ""
            return PlaybackPlan(
                mode=PlaybackMode.DIRECT,
                container=clean_ext,
                audio_codec=a_codec,
                reasons=("Direct audio playback supported natively by client",),
                stream_url=stream_url,
                direct_url=direct_url,
                diagnostics={
                    "audio_codec": a_codec,
                    "container": clean_ext,
                    "audio_native": True,
                    "duration": streams_info.get("duration"),
                },
            )

        # Incompatible audio codec requires transcoding to MP3
        stream_url = (
            f"/api/media/{media_item_id}/stream/audio"
            if media_item_id is not None
            else ""
        )
        return PlaybackPlan(
            mode=PlaybackMode.TRANSCODE,
            container="mp3",
            audio_codec="mp3",
            target_bitrate=192,
            reasons=(
                f"Audio codec '{a_codec}' or container '{ext}' not natively supported by client",
            ),
            stream_url=stream_url,
            direct_url=direct_url,
            diagnostics={
                "source_audio_codec": a_codec,
                "target_audio_codec": "mp3",
                "audio_native": False,
                "duration": streams_info.get("duration"),
            },
        )

    def _plan_video(
        self,
        file_path: Path,
        ext: str,
        media_item_id: int | None,
        capabilities: ClientCapabilities | None,
        requested_resolution: str | None,
        hwaccel: str,
        streams_info: dict[str, Any] | None,
    ) -> PlaybackPlan:
        """Plans delivery for video media items."""
        clean_ext = ext.lstrip(".")
        direct_url = (
            f"/api/media/{media_item_id}/file" if media_item_id is not None else None
        )

        if streams_info is None:
            from aarkib.services.transcoder import probe_media_streams

            streams_info = probe_media_streams(file_path)

        video = streams_info.get("video") or {}
        audio_list = streams_info.get("audio", [])
        primary_audio = audio_list[0] if audio_list else {}
        subtitles_list = streams_info.get("subtitles", [])

        v_codec = (video.get("codec") or "").lower()
        a_codec = (primary_audio.get("codec") or "").lower()
        pix_fmt = video.get("pix_fmt") or ""
        is_10bit = "10" in pix_fmt or "p010" in pix_fmt
        v_width = video.get("width")
        v_height = video.get("height")
        duration = streams_info.get("duration")

        reasons: list[str] = []

        # Container compatibility
        container_native = (
            (ext in capabilities.video.containers)
            if (capabilities and capabilities.video.containers)
            else (ext in WEB_NATIVE_CONTAINERS)
        )

        # Video codec compatibility
        if capabilities:
            video_native = bool(
                v_codec
                and capabilities.supports_video(
                    v_codec,
                    container=None,
                    is_10bit=is_10bit,
                    width=v_width,
                    height=v_height,
                )
            )
        else:
            video_native = bool(
                v_codec and v_codec in WEB_NATIVE_VIDEO_CODECS and not is_10bit
            )

        client_target = capabilities.device.client_name if capabilities else "browser"

        if not video_native and v_codec:
            if is_10bit and (not capabilities or not capabilities.video.supports_10bit):
                reasons.append(f"10-bit color ({pix_fmt}) requires transcoding")
            else:
                reasons.append(
                    f"Video codec '{v_codec}' is not natively supported by {client_target}"
                )

        # Audio codec compatibility
        if capabilities:
            audio_native = bool(
                not a_codec or capabilities.supports_audio(a_codec, container=None)
            )
        else:
            audio_native = bool(not a_codec or a_codec in WEB_NATIVE_AUDIO_CODECS)

        if not audio_native and a_codec:
            reasons.append(
                f"Audio codec '{a_codec}' is not natively supported by {client_target}"
            )

        # Requested resolution downscale check
        res_requested_downscale = False
        target_bitrate: int | None = None
        target_resolution_str = requested_resolution or "original"

        if requested_resolution and requested_resolution != "original":
            preset = RESOLUTION_PRESETS.get(requested_resolution)
            if preset:
                target_w = preset.get("width")
                target_bitrate = preset.get("video_bitrate")
                if target_w and v_width and target_w < v_width:
                    res_requested_downscale = True
                    video_native = False
                    reasons.append(
                        f"Resolution downscale to {requested_resolution} requested"
                    )

        # Subtitle tracks evaluation
        subtitles_plan = self._plan_subtitles(
            subtitles_list=subtitles_list,
            media_item_id=media_item_id,
            capabilities=capabilities,
        )

        # Hardware acceleration detection for transcode scenarios
        hwaccel_device: str = "software"
        if hwaccel in ("auto", "vaapi"):
            try:
                from aarkib.services.transcoder import detect_vaapi_device

                vaapi_node = detect_vaapi_device()
                if vaapi_node:
                    hwaccel_device = vaapi_node
            except Exception:
                pass

        common_diagnostics: dict[str, Any] = {
            "container": ext,
            "container_native": container_native,
            "video_codec": v_codec,
            "video_native": video_native,
            "audio_codec": a_codec,
            "audio_native": audio_native,
            "duration": duration,
            "resolution": {"width": v_width, "height": v_height} if v_width else None,
            "audio_tracks_count": len(audio_list),
            "subtitles_count": len(subtitles_list),
        }

        # Evaluate strategy using structural pattern matching
        match (container_native, video_native, audio_native):
            case (True, True, True):
                # Case 1: Direct Play
                stream_url = direct_url or ""
                return PlaybackPlan(
                    mode=PlaybackMode.DIRECT,
                    container=clean_ext,
                    video_codec=v_codec,
                    audio_codec=a_codec,
                    resolution="original",
                    target_bitrate=None,
                    reasons=tuple(reasons) or ("Direct playback supported natively",),
                    stream_url=stream_url,
                    direct_url=direct_url,
                    subtitles=tuple(subtitles_plan),
                    diagnostics={
                        **common_diagnostics,
                        "copy_video": True,
                        "copy_audio": True,
                    },
                )

            case (False, True, True) if ext in REMUXABLE_CONTAINERS:
                # Case 2: Direct Remux
                remux_url = (
                    f"/api/media/{media_item_id}/stream/remux"
                    if media_item_id is not None
                    else ""
                )
                reasons.append(
                    f"Container '{ext}' can be remuxed to MP4 on-the-fly with zero re-encoding"
                )
                return PlaybackPlan(
                    mode=PlaybackMode.REMUX,
                    container="mp4",
                    video_codec=v_codec,
                    audio_codec=a_codec,
                    resolution="original",
                    target_bitrate=None,
                    reasons=tuple(reasons),
                    stream_url=remux_url,
                    direct_url=direct_url,
                    subtitles=tuple(subtitles_plan),
                    diagnostics={
                        **common_diagnostics,
                        "copy_video": True,
                        "copy_audio": True,
                        "remux": True,
                    },
                )

            case (_, True, False):
                # Case 3: Audio Transcode with Direct Video Copy
                audio_transcode_url = (
                    f"/api/media/{media_item_id}/stream/remux?audio_transcode=true"
                    if media_item_id is not None
                    else ""
                )
                reasons.append(
                    "Video stream can be copied directly while audio is transcoded to AAC"
                )
                return PlaybackPlan(
                    mode=PlaybackMode.TRANSCODE,
                    container="mp4",
                    video_codec=v_codec,
                    audio_codec="aac",
                    resolution="original",
                    target_bitrate=None,
                    reasons=tuple(reasons),
                    stream_url=audio_transcode_url,
                    direct_url=direct_url,
                    subtitles=tuple(subtitles_plan),
                    diagnostics={
                        **common_diagnostics,
                        "copy_video": True,
                        "transcode_audio": True,
                        "target_audio_codec": "aac",
                    },
                )

            case _:
                # Case 4: Full Transcode (HLS)
                if not reasons:
                    reasons.append(
                        f"Transcoding required for optimal {client_target} playback compatibility"
                    )

                hls_query = (
                    f"?res={requested_resolution}"
                    if requested_resolution and requested_resolution != "original"
                    else ""
                )
                hls_url = (
                    f"/api/media/{media_item_id}/stream/hls/master.m3u8{hls_query}"
                    if media_item_id is not None
                    else ""
                )

                if target_bitrate is None:
                    preset = RESOLUTION_PRESETS.get(
                        target_resolution_str, RESOLUTION_PRESETS["original"]
                    )
                    target_bitrate = preset["video_bitrate"]

                return PlaybackPlan(
                    mode=PlaybackMode.TRANSCODE,
                    container="m3u8",
                    video_codec="h264",
                    audio_codec="aac",
                    resolution=target_resolution_str,
                    target_bitrate=target_bitrate,
                    reasons=tuple(reasons),
                    stream_url=hls_url,
                    direct_url=direct_url,
                    subtitles=tuple(subtitles_plan),
                    diagnostics={
                        **common_diagnostics,
                        "copy_video": False,
                        "transcode_video": True,
                        "transcode_audio": True,
                        "hwaccel": hwaccel_device,
                        "downscale": res_requested_downscale,
                    },
                )

    def _plan_subtitles(
        self,
        subtitles_list: list[dict[str, Any]],
        media_item_id: int | None,
        capabilities: ClientCapabilities | None,
    ) -> list[dict[str, Any]]:
        """Evaluates subtitle tracks against client capabilities for delivery mode."""
        planned: list[dict[str, Any]] = []

        for sub in subtitles_list:
            s_idx = sub.get("index", 0)
            s_codec = (sub.get("codec") or "").lower()
            s_lang = sub.get("language") or "und"
            s_title = sub.get("title") or f"Subtitle {s_idx + 1}"
            is_default = bool(sub.get("is_default"))
            is_forced = bool(sub.get("is_forced"))

            delivery = "native"
            url: str = ""

            if s_codec in ("ass", "ssa"):
                if capabilities and capabilities.subtitles.supports_ass_wasm:
                    delivery = "wasm"
                    url = (
                        f"/api/media/{media_item_id}/subtitles/{s_idx}.ass"
                        if media_item_id is not None
                        else ""
                    )
                elif capabilities and "ass" in capabilities.subtitles.formats:
                    delivery = "native"
                    url = (
                        f"/api/media/{media_item_id}/subtitles/{s_idx}.ass"
                        if media_item_id is not None
                        else ""
                    )
                else:
                    delivery = "vtt"
                    url = (
                        f"/api/media/{media_item_id}/subtitles/{s_idx}.vtt"
                        if media_item_id is not None
                        else ""
                    )
            elif s_codec in ("subrip", "srt", "mov_text"):
                delivery = "vtt"
                url = (
                    f"/api/media/{media_item_id}/subtitles/{s_idx}.vtt"
                    if media_item_id is not None
                    else ""
                )
            elif s_codec in ("webvtt", "vtt"):
                delivery = "native"
                url = (
                    f"/api/media/{media_item_id}/subtitles/{s_idx}.vtt"
                    if media_item_id is not None
                    else ""
                )
            elif s_codec in ("hdmv_pgs_subtitle", "dvd_subtitle", "pgs"):
                delivery = "burn_in"
                url = ""

            planned.append(
                {
                    "index": s_idx,
                    "codec": s_codec,
                    "language": s_lang,
                    "title": s_title,
                    "is_default": is_default,
                    "is_forced": is_forced,
                    "delivery": delivery,
                    "url": url,
                }
            )

        return planned


# Global singleton instance
playback_service = PlaybackService()


def plan_playback(
    item: MediaItem,
    capabilities: ClientCapabilities | dict[str, Any] | None = None,
    requested_resolution: str | None = None,
    hwaccel: str = "auto",
    streams_info: dict[str, Any] | None = None,
) -> PlaybackPlan:
    """Convenience helper for planning playback on a MediaItem."""
    return playback_service.plan(
        item=item,
        capabilities=capabilities,
        requested_resolution=requested_resolution,
        hwaccel=hwaccel,
        streams_info=streams_info,
    )
