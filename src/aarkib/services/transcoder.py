from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Generator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aarkib.config import get_ffmpeg_binary, get_ffprobe_binary

if TYPE_CHECKING:
    from aarkib.models.capabilities import ClientCapabilities


logger = logging.getLogger(__name__)

VAAPI_PROBE_TIMEOUT: int = 5
QSV_PROBE_TIMEOUT: int = 5
FFPROBE_STREAM_TIMEOUT: int = 12
SUBTITLE_CONVERT_TIMEOUT: int = 10
REMUX_PROCESS_STOP_TIMEOUT: float = 1.5
TRANSCODE_PROCESS_STOP_TIMEOUT: float = 2.0

# Cache detected transcode capabilities to avoid probing repeatedly
_CACHED_TRANSCODE_CAPS: TranscodeCapabilities | None = None
_TRANSCODE_CAPS_LOCK = threading.Lock()
_CACHED_VAAPI_DEVICE: str | None = None
_VAAPI_CHECKED: bool = False
_VAAPI_LOCK = threading.Lock()


@dataclass
class TranscodeCapabilities:
    """Hardware and software video transcoding capabilities."""

    software_available: bool = True
    vaapi_device: str | None = None
    qsv_available: bool = False
    active_backend: str = "auto"

    @property
    def is_hardware_accelerated(self) -> bool:
        return bool(self.vaapi_device or self.qsv_available)

    def to_dict(self) -> dict[str, Any]:
        return {
            "software_available": self.software_available,
            "vaapi_device": self.vaapi_device,
            "qsv_available": self.qsv_available,
            "active_backend": self.active_backend,
            "is_hardware_accelerated": self.is_hardware_accelerated,
        }


@dataclass(frozen=True)
class TranscodeProfile:
    """Resolved FFmpeg transcoding parameters and argument lists."""

    backend: str  # "software", "vaapi", "qsv"
    video_codec: str  # "libx264", "h264_vaapi", "h264_qsv"
    device: str | None = None
    hwaccel_args: tuple[str, ...] = ()
    filter_args: tuple[str, ...] = ()
    encoder_args: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "video_codec": self.video_codec,
            "device": self.device,
            "hwaccel_args": list(self.hwaccel_args),
            "filter_args": list(self.filter_args),
            "encoder_args": list(self.encoder_args),
        }


def _probe_vaapi_node(ffmpeg_bin: str, node: str) -> bool:
    """Probes whether a Linux VA-API render node supports h264_vaapi encoding."""
    try:
        probe_cmd = [
            ffmpeg_bin,
            "-v",
            "quiet",
            "-hwaccel",
            "vaapi",
            "-vaapi_device",
            node,
            "-f",
            "lavfi",
            "-i",
            "testsrc=d=0.1",
            "-vf",
            "format=nv12,hwupload",
            "-c:v",
            "h264_vaapi",
            "-f",
            "null",
            "-",
        ]
        res = subprocess.run(
            probe_cmd, capture_output=True, timeout=VAAPI_PROBE_TIMEOUT
        )
        return res.returncode == 0
    except Exception as e:
        logger.debug("VAAPI probe exception on device %s: %s", node, e)
        return False


def _probe_qsv_support(ffmpeg_bin: str) -> bool:
    """Probes whether Intel QuickSync Video (h264_qsv) encoder is functional."""
    try:
        probe_cmd = [
            ffmpeg_bin,
            "-v",
            "quiet",
            "-f",
            "lavfi",
            "-i",
            "testsrc=d=0.1",
            "-c:v",
            "h264_qsv",
            "-f",
            "null",
            "-",
        ]
        res = subprocess.run(probe_cmd, capture_output=True, timeout=QSV_PROBE_TIMEOUT)
        return res.returncode == 0
    except Exception as e:
        logger.debug("QSV probe exception: %s", e)
        return False


def detect_transcode_capabilities(
    device_override: str | None = None,
    force_refresh: bool = False,
) -> TranscodeCapabilities:
    """Detects available hardware and software video transcoding capabilities.

    Probes Linux VA-API render nodes (/dev/dri/renderD*) and Intel QuickSync Video
    (h264_qsv) encoder support with bounded 5s timeouts and safe subprocess execution.
    """
    global _CACHED_TRANSCODE_CAPS, _CACHED_VAAPI_DEVICE, _VAAPI_CHECKED

    if not force_refresh and not device_override:
        with _TRANSCODE_CAPS_LOCK:
            if _CACHED_TRANSCODE_CAPS is not None:
                return _CACHED_TRANSCODE_CAPS

    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        caps = TranscodeCapabilities(
            software_available=False,
            vaapi_device=None,
            qsv_available=False,
            active_backend="software",
        )
        if not device_override:
            with _TRANSCODE_CAPS_LOCK:
                _CACHED_TRANSCODE_CAPS = caps
            with _VAAPI_LOCK:
                _CACHED_VAAPI_DEVICE = None
                _VAAPI_CHECKED = True
        return caps

    # 1. Linux VA-API render node discovery & probe
    vaapi_node: str | None = None
    if device_override:
        vaapi_node = device_override
    else:
        candidate_nodes: list[str] = []
        env_device = os.getenv("AARKIB_VAAPI_DEVICE")
        if env_device:
            candidate_nodes.append(env_device)

        dri_dir = Path("/dev/dri")
        if dri_dir.is_dir():
            for p in sorted(dri_dir.glob("renderD*")):
                s_path = str(p)
                if s_path not in candidate_nodes:
                    candidate_nodes.append(s_path)

        for node in candidate_nodes:
            if not os.path.exists(node):
                continue
            if not os.access(node, os.R_OK | os.W_OK):
                logger.debug(
                    "Skipping VAAPI node %s: insufficient read/write permissions", node
                )
                continue
            if _probe_vaapi_node(ffmpeg_bin, node):
                logger.info(
                    "Successfully validated VAAPI hardware encoder on device: %s", node
                )
                vaapi_node = node
                break
            else:
                logger.debug("VAAPI probe failed on device %s", node)

    # 2. Intel QSV probe
    qsv_supported = _probe_qsv_support(ffmpeg_bin)
    if qsv_supported:
        logger.info("Successfully validated Intel QuickSync Video (h264_qsv) encoder")

    caps = TranscodeCapabilities(
        software_available=True,
        vaapi_device=vaapi_node,
        qsv_available=qsv_supported,
        active_backend="auto",
    )

    if not device_override:
        with _TRANSCODE_CAPS_LOCK:
            _CACHED_TRANSCODE_CAPS = caps
        with _VAAPI_LOCK:
            _CACHED_VAAPI_DEVICE = vaapi_node
            _VAAPI_CHECKED = True

    return caps


def detect_vaapi_device(device_override: str | None = None) -> str | None:
    """Dynamically detects usable Linux VAAPI render nodes with fallback.

    Maintained for backward compatibility. Delegates to detect_transcode_capabilities.
    """
    caps = detect_transcode_capabilities(device_override=device_override)
    return caps.vaapi_device


def reset_transcode_cache() -> None:
    """Resets cached transcode capabilities and VA-API discovery."""
    global _CACHED_TRANSCODE_CAPS, _CACHED_VAAPI_DEVICE, _VAAPI_CHECKED
    with _TRANSCODE_CAPS_LOCK:
        _CACHED_TRANSCODE_CAPS = None
    with _VAAPI_LOCK:
        _CACHED_VAAPI_DEVICE = None
        _VAAPI_CHECKED = False


def reset_vaapi_cache() -> None:
    """Resets the cached VAAPI device discovery (useful in tests)."""
    reset_transcode_cache()


def resolve_transcode_profile(
    requested_backend: str = "auto",
    capabilities: TranscodeCapabilities | None = None,
    target_width: int | None = None,
    bitrate_kbps: int = 4500,
) -> TranscodeProfile:
    """Resolves an optimal FFmpeg TranscodeProfile based on requested backend and capabilities.

    Selects appropriate FFmpeg hardware acceleration flags and video encoders
    with safe, non-shell argument lists and graceful CPU software fallback.
    """
    if capabilities is None:
        capabilities = detect_transcode_capabilities()

    requested = (requested_backend or "auto").lower().strip()

    # Determine effective backend
    chosen_backend = "software"
    if requested == "vaapi":
        if capabilities.vaapi_device:
            chosen_backend = "vaapi"
        else:
            logger.warning(
                "VA-API backend requested but no usable VA-API device detected; falling back to CPU software encoding"
            )
            chosen_backend = "software"
    elif requested == "qsv":
        if capabilities.qsv_available:
            chosen_backend = "qsv"
        else:
            logger.warning(
                "QSV backend requested but Intel QSV encoder is unavailable; falling back to CPU software encoding"
            )
            chosen_backend = "software"
    elif requested == "software":
        chosen_backend = "software"
    else:  # "auto" or other
        if capabilities.vaapi_device:
            chosen_backend = "vaapi"
        elif capabilities.qsv_available:
            chosen_backend = "qsv"
        else:
            chosen_backend = "software"

    # Build structured argument lists based on chosen backend
    if chosen_backend == "vaapi":
        device = capabilities.vaapi_device or "/dev/dri/renderD128"
        hwaccel_args = ("-hwaccel", "vaapi", "-vaapi_device", device)
        filter_str = (
            f"scale=w='min({target_width},iw)':h=-2,format=nv12,hwupload"
            if target_width
            else "format=nv12,hwupload"
        )
        filter_args = ("-vf", filter_str)
        encoder_args = (
            "-c:v",
            "h264_vaapi",
            "-b:v",
            f"{bitrate_kbps}k",
            "-maxrate",
            f"{int(bitrate_kbps * 1.25)}k",
            "-bufsize",
            f"{bitrate_kbps * 2}k",
        )
        return TranscodeProfile(
            backend="vaapi",
            video_codec="h264_vaapi",
            device=device,
            hwaccel_args=hwaccel_args,
            filter_args=filter_args,
            encoder_args=encoder_args,
        )

    if chosen_backend == "qsv":
        hwaccel_args = ()
        filter_args = (
            ("-vf", f"scale=w='min({target_width},iw)':h=-2") if target_width else ()
        )
        encoder_args = (
            "-c:v",
            "h264_qsv",
            "-preset",
            "veryfast",
            "-b:v",
            f"{bitrate_kbps}k",
            "-maxrate",
            f"{int(bitrate_kbps * 1.25)}k",
            "-bufsize",
            f"{bitrate_kbps * 2}k",
        )
        return TranscodeProfile(
            backend="qsv",
            video_codec="h264_qsv",
            device=None,
            hwaccel_args=hwaccel_args,
            filter_args=filter_args,
            encoder_args=encoder_args,
        )

    # Software fallback (libx264)
    hwaccel_args = ()
    filter_args = (
        ("-vf", f"scale=w='min({target_width},iw)':h=-2") if target_width else ()
    )
    encoder_args = (
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-maxrate",
        f"{int(bitrate_kbps * 1.25)}k",
        "-bufsize",
        f"{bitrate_kbps * 2}k",
        "-pix_fmt",
        "yuv420p",
    )
    return TranscodeProfile(
        backend="software",
        video_codec="libx264",
        device=None,
        hwaccel_args=hwaccel_args,
        filter_args=filter_args,
        encoder_args=encoder_args,
    )


class PlaybackStrategy(StrEnum):
    DIRECT_PLAY = "direct_play"
    DIRECT_REMUX = "direct_remux"
    AUDIO_TRANSCODE = "audio_transcode"
    FULL_TRANSCODE = "full_transcode"


WEB_NATIVE_CONTAINERS: set[str] = {".mp4", ".m4v", ".webm"}
REMUXABLE_CONTAINERS: set[str] = {".mkv", ".mov", ".avi"}

WEB_NATIVE_VIDEO_CODECS: set[str] = {
    "h264",
    "avc",
    "avc1",
    "vp8",
    "vp9",
    "av1",
}

WEB_NATIVE_AUDIO_CODECS: set[str] = {
    "aac",
    "mp3",
    "opus",
    "vorbis",
    "flac",
    "wav",
}

INCOMPATIBLE_AUDIO_CODECS: set[str] = {
    "dts",
    "ac3",
    "eac3",
    "truehd",
    "mlp",
    "dca",
}

RESOLUTION_PRESETS: dict[str, dict[str, Any]] = {
    "1080p": {"width": 1920, "height": 1080, "video_bitrate": 4500},
    "720p": {"width": 1280, "height": 720, "video_bitrate": 2500},
    "480p": {"width": 854, "height": 480, "video_bitrate": 1200},
    "original": {"width": None, "height": None, "video_bitrate": 5000},
}


def probe_media_streams(file_path: Path) -> dict[str, Any]:
    """Inspects media streams (video, audio, subtitles) using ffprobe or container parser."""
    file_path = Path(file_path)
    result: dict[str, Any] = {
        "file_path": str(file_path),
        "container": file_path.suffix.lower(),
        "duration": None,
        "size": file_path.stat().st_size if file_path.exists() else 0,
        "video": None,
        "audio": [],
        "subtitles": [],
    }

    ffprobe_bin = get_ffprobe_binary()
    if not ffprobe_bin or not file_path.exists():
        # Fallback to basic pure-Python inspection if possible
        from aarkib.services.parsers.video import read_mp4_metadata

        mp4_meta = read_mp4_metadata(file_path)
        if mp4_meta:
            result["duration"] = mp4_meta.get("duration")
            result["video"] = {
                "codec": "h264",
                "width": mp4_meta.get("resolution_width"),
                "height": mp4_meta.get("resolution_height"),
                "bitrate": None,
            }
        return result

    try:
        cmd = [
            ffprobe_bin,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ]
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=FFPROBE_STREAM_TIMEOUT
        )
        if res.returncode != 0:
            return result

        data = json.loads(res.stdout)
        fmt = data.get("format", {})
        if "duration" in fmt:
            try:
                result["duration"] = round(float(fmt["duration"]), 2)
            except ValueError, TypeError:
                pass

        audio_idx = 0
        sub_idx = 0
        for stream in data.get("streams", []):
            stype = stream.get("codec_type")
            codec = (stream.get("codec_name") or "").lower()
            tags = stream.get("tags") or {}

            if stype == "video" and result["video"] is None:
                v_width = stream.get("width")
                v_height = stream.get("height")
                v_bitrate = stream.get("bit_rate") or fmt.get("bit_rate")
                result["video"] = {
                    "codec": codec,
                    "profile": (stream.get("profile") or "").lower(),
                    "width": int(v_width) if v_width else None,
                    "height": int(v_height) if v_height else None,
                    "bitrate": int(v_bitrate) if v_bitrate else None,
                    "pix_fmt": stream.get("pix_fmt"),
                    "duration": float(stream["duration"])
                    if "duration" in stream
                    else result["duration"],
                }
            elif stype == "audio":
                a_channels = stream.get("channels")
                result["audio"].append(
                    {
                        "index": audio_idx,
                        "stream_index": stream.get("index"),
                        "codec": codec,
                        "channels": int(a_channels) if a_channels else None,
                        "channel_layout": stream.get("channel_layout"),
                        "sample_rate": stream.get("sample_rate"),
                        "language": tags.get("language")
                        or tags.get("LANGUAGE")
                        or "und",
                        "title": tags.get("title")
                        or tags.get("TITLE")
                        or f"Audio Track {audio_idx + 1}",
                    }
                )
                audio_idx += 1
            elif stype == "subtitle":
                disp = stream.get("disposition") or {}
                result["subtitles"].append(
                    {
                        "index": sub_idx,
                        "stream_index": stream.get("index"),
                        "codec": codec,
                        "language": tags.get("language")
                        or tags.get("LANGUAGE")
                        or "und",
                        "title": tags.get("title")
                        or tags.get("TITLE")
                        or f"Subtitle {sub_idx + 1}",
                        "is_default": bool(disp.get("default")),
                        "is_forced": bool(disp.get("forced")),
                    }
                )
                sub_idx += 1

    except Exception as e:
        logger.debug("ffprobe stream probing failed for %s: %s", file_path, e)

    # If ffprobe didn't detect video or duration (e.g. minimal synthetic/truncated MP4),
    # supplement with pure-Python moov/mvhd/tkhd parser
    if result["video"] is None or result["duration"] is None:
        from aarkib.services.parsers.video import read_mp4_metadata

        mp4_meta = read_mp4_metadata(file_path)
        if mp4_meta:
            if result["duration"] is None and mp4_meta.get("duration"):
                result["duration"] = mp4_meta.get("duration")
            if result["video"] is None and mp4_meta.get("resolution_width"):
                result["video"] = {
                    "codec": "h264",
                    "width": mp4_meta.get("resolution_width"),
                    "height": mp4_meta.get("resolution_height"),
                    "bitrate": None,
                }

    return result


def evaluate_playback_strategy(
    file_path: Path,
    streams_info: dict[str, Any] | None = None,
    client_caps: ClientCapabilities | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Determines the optimal playback strategy for a media item.

    Delegates deterministic decision planning to PlaybackService while preserving
    the legacy dictionary return shape for backward compatibility.
    """
    from aarkib.models.playback import PlaybackMode
    from aarkib.services.playback_service import playback_service

    file_path = Path(file_path)
    plan = playback_service.plan_for_file(
        file_path=file_path,
        streams_info=streams_info,
        capabilities=client_caps,
    )

    # Map PlaybackPlan mode to legacy PlaybackStrategy enum
    if plan.mode == PlaybackMode.DIRECT:
        strategy = PlaybackStrategy.DIRECT_PLAY
    elif plan.mode == PlaybackMode.REMUX:
        strategy = PlaybackStrategy.DIRECT_REMUX
    elif plan.mode == PlaybackMode.TRANSCODE:
        if plan.diagnostics.get("copy_video"):
            strategy = PlaybackStrategy.AUDIO_TRANSCODE
        else:
            strategy = PlaybackStrategy.FULL_TRANSCODE
    else:
        strategy = PlaybackStrategy.FULL_TRANSCODE

    diag = plan.diagnostics
    ext = file_path.suffix.lower()

    return {
        "strategy": strategy.value,
        "reasons": list(plan.reasons),
        "container": ext,
        "container_native": diag.get("container_native", True),
        "video_codec": plan.video_codec,
        "video_native": diag.get("video_native", True),
        "audio_codec": plan.audio_codec,
        "audio_native": diag.get("audio_native", True),
        "duration": diag.get("duration"),
        "resolution": diag.get("resolution"),
        "audio_tracks_count": diag.get("audio_tracks_count", 0),
        "subtitles_count": diag.get("subtitles_count", 0),
    }


def generate_vtt_subtitles(file_path: Path, subtitle_index: int = 0) -> bytes:
    """Extracts and converts embedded text subtitles (SRT, ASS) to WebVTT format."""
    file_path = Path(file_path)
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not file_path.exists():
        return b"WEBVTT\n\nNOTE Subtitle engine unavailable\n"

    try:
        cmd = [
            ffmpeg_bin,
            "-v",
            "quiet",
            "-i",
            str(file_path),
            "-map",
            f"0:s:{subtitle_index}",
            "-f",
            "webvtt",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=SUBTITLE_CONVERT_TIMEOUT)
        if res.returncode == 0 and res.stdout.startswith(b"WEBVTT"):
            return res.stdout
    except Exception as e:
        logger.debug(
            "Failed extracting subtitle index %d from %s: %s",
            subtitle_index,
            file_path,
            e,
        )

    return b"WEBVTT\n\nNOTE Subtitle track not available as text WebVTT\n"


def generate_ass_subtitles(file_path: Path, subtitle_index: int = 0) -> bytes:
    """Extracts embedded subtitles in ASS/SSA format for high-fidelity client rendering (JASSUB)."""
    file_path = Path(file_path)
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not file_path.exists():
        return (
            b"[Script Info]\nTitle: Subtitle engine unavailable\nScriptType: v4.00+\n\n"
            b"[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

    try:
        # 1. Attempt fast direct stream-copy if subtitle track is already ASS/SSA
        cmd_copy = [
            ffmpeg_bin,
            "-v",
            "quiet",
            "-i",
            str(file_path),
            "-map",
            f"0:s:{subtitle_index}",
            "-c:s",
            "copy",
            "-f",
            "ass",
            "-",
        ]
        res = subprocess.run(
            cmd_copy, capture_output=True, timeout=SUBTITLE_CONVERT_TIMEOUT
        )
        if res.returncode == 0 and b"[Script Info]" in res.stdout:
            return res.stdout

        # 2. Fallback to subtitle format conversion if source track is SRT, VTT, or mov_text
        cmd_conv = [
            ffmpeg_bin,
            "-v",
            "quiet",
            "-i",
            str(file_path),
            "-map",
            f"0:s:{subtitle_index}",
            "-c:s",
            "ass",
            "-f",
            "ass",
            "-",
        ]
        res_conv = subprocess.run(
            cmd_conv, capture_output=True, timeout=SUBTITLE_CONVERT_TIMEOUT
        )
        if res_conv.returncode == 0 and b"[Script Info]" in res_conv.stdout:
            return res_conv.stdout
    except Exception as e:
        logger.debug(
            "Failed extracting ASS subtitle index %d from %s: %s",
            subtitle_index,
            file_path,
            e,
        )

    return (
        b"[Script Info]\nTitle: Subtitle track not available as ASS\nScriptType: v4.00+\n\n"
        b"[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def stream_remux_pipe(
    file_path: Path,
    seek_seconds: float = 0.0,
    audio_transcode: bool = False,
) -> Generator[bytes]:
    """Streams container-remuxed fragmented MP4 directly from FFmpeg stdout."""
    file_path = Path(file_path)
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin or not file_path.exists():
        return

    cmd = [ffmpeg_bin]
    if seek_seconds > 0:
        cmd.extend(["-ss", str(seek_seconds)])
    cmd.extend(["-i", str(file_path)])

    if audio_transcode:
        # Copy video stream, re-encode audio to AAC
        cmd.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2"])
    else:
        # Zero-CPU direct copy of both video and audio
        cmd.extend(["-c", "copy"])

    cmd.extend(
        [
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            "pipe:1",
        ]
    )

    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=64 * 1024,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
        if proc.stdout is None:
            raise RuntimeError("FFmpeg process stdout pipe is not available")
        while True:
            chunk = proc.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    except GeneratorExit:
        logger.debug("Client disconnected from remux stream %s", file_path)
    except Exception as e:
        logger.debug("Remux stream error: %s", e)
    finally:
        if proc and proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
        if proc and proc.poll() is None:
            try:
                if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                else:
                    proc.terminate()
                proc.wait(timeout=REMUX_PROCESS_STOP_TIMEOUT)
            except Exception:
                try:
                    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        proc.kill()
                except Exception as exc:
                    logger.debug("Force kill failed for remux proc: %s", exc)


@dataclass
class TranscodeSession:
    session_id: str
    media_item_id: int
    file_path: Path
    output_dir: Path
    process: subprocess.Popen | None = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    target_resolution: str = "original"
    vaapi_device: str | None = None
    backend: str = "software"
    seek_offset: float = 0.0
    audio_track_index: int = 0
    is_active: bool = True

    def touch(self) -> None:
        self.last_activity = time.time()


class TranscodeSupervisor:
    """Manages active FFmpeg HLS transcode sessions, heartbeats, and scratch directory lifecycle."""

    def __init__(
        self,
        idle_timeout: float = 60.0,
        reaper_interval: float = 10.0,
    ):
        self.idle_timeout = idle_timeout
        self.reaper_interval = reaper_interval
        self._sessions: dict[str, TranscodeSession] = {}
        self._lock = threading.Lock()
        self._reaper_thread: threading.Thread | None = None
        self._stop_reaper = threading.Event()
        self.start_reaper()
        atexit.register(self.cleanup_all)

    def start_reaper(self) -> None:
        """Starts background idle reaper thread."""
        if self._reaper_thread is not None and self._reaper_thread.is_alive():
            return
        self._stop_reaper.clear()
        self._reaper_thread = threading.Thread(
            target=self._reaper_loop,
            name="aarkib-transcode-reaper",
            daemon=True,
        )
        self._reaper_thread.start()

    def stop_reaper(self) -> None:
        """Stops background idle reaper thread."""
        self._stop_reaper.set()
        if self._reaper_thread and self._reaper_thread.is_alive():
            self._reaper_thread.join(timeout=2.0)

    def _reaper_loop(self) -> None:
        """Periodically checks and reaps inactive sessions."""
        while not self._stop_reaper.is_set():
            if self._stop_reaper.wait(timeout=self.reaper_interval):
                break
            now = time.time()
            expired_ids: list[str] = []
            with self._lock:
                for sid, s in self._sessions.items():
                    if now - s.last_activity > self.idle_timeout:
                        expired_ids.append(sid)

            for sid in expired_ids:
                logger.info(
                    "Reaping idle transcode session %s (inactivity > %ds)",
                    sid,
                    self.idle_timeout,
                )
                self.stop_session(sid)

    def get_session(self, session_id: str) -> TranscodeSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def touch_session(self, session_id: str) -> None:
        session = self.get_session(session_id)
        if session:
            session.touch()

    def create_or_get_hls_session(
        self,
        media_item_id: int,
        file_path: Path,
        transcode_base_dir: Path,
        resolution: str = "original",
        seek_offset: float = 0.0,
        audio_track_index: int = 0,
        vaapi_device: str | None = None,
        backend: str | None = None,
    ) -> TranscodeSession:
        """Spawns an FFmpeg HLS transcoding session or returns an active matching one."""
        file_path = Path(file_path)
        transcode_base_dir = Path(transcode_base_dir)

        # Check if identical active session already exists
        with self._lock:
            for s in self._sessions.values():
                if (
                    s.is_active
                    and s.media_item_id == media_item_id
                    and s.target_resolution == resolution
                    and abs(s.seek_offset - seek_offset) < 1.0
                    and s.audio_track_index == audio_track_index
                ):
                    s.touch()
                    return s

        session_id = f"hls_{media_item_id}_{uuid.uuid4().hex[:8]}"
        session_dir = transcode_base_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        preset = RESOLUTION_PRESETS.get(resolution, RESOLUTION_PRESETS["original"])
        target_w = preset["width"]
        bitrate = preset["video_bitrate"]

        req_backend = backend or os.getenv("AARKIB_TRANSCODE_BACKEND", "auto")
        trans_caps = detect_transcode_capabilities(device_override=vaapi_device)
        profile = resolve_transcode_profile(
            requested_backend=req_backend,
            capabilities=trans_caps,
            target_width=target_w,
            bitrate_kbps=bitrate,
        )

        ffmpeg_bin = get_ffmpeg_binary() or "ffmpeg"
        playlist_path = session_dir / "playlist.m3u8"
        segment_pattern = str(session_dir / "segment_%05d.m4s")

        def _build_cmd(p: TranscodeProfile) -> list[str]:
            c = [ffmpeg_bin, "-y"]
            if seek_offset > 0:
                c.extend(["-ss", str(seek_offset)])
            if p.hwaccel_args:
                c.extend(p.hwaccel_args)
            c.extend(["-i", str(file_path)])
            c.extend(["-map", "0:v:0", "-map", f"0:a:{audio_track_index}?"])
            if p.filter_args:
                c.extend(p.filter_args)
            if p.encoder_args:
                c.extend(p.encoder_args)
            c.extend(["-c:a", "aac", "-b:a", "192k", "-ac", "2"])
            c.extend(
                [
                    "-f",
                    "hls",
                    "-hls_time",
                    "6",
                    "-hls_list_size",
                    "0",
                    "-hls_segment_type",
                    "fmp4",
                    "-hls_flags",
                    "independent_segments",
                    "-hls_segment_filename",
                    segment_pattern,
                    str(playlist_path),
                ]
            )
            return c

        def _spawn_proc(c: list[str]) -> subprocess.Popen | None:
            if not get_ffmpeg_binary():
                return None
            try:
                p = subprocess.Popen(
                    c,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid if hasattr(os, "setsid") else None,
                )
                logger.info(
                    "Launched HLS transcode process %d for session %s (backend: %s)",
                    p.pid,
                    session_id,
                    profile.backend,
                )
                return p
            except Exception as e:
                logger.error(
                    "Failed to spawn FFmpeg process for session %s: %s", session_id, e
                )
                return None

        cmd = _build_cmd(profile)
        proc = _spawn_proc(cmd)

        # Runtime fallback: if hardware acceleration failed on startup, fallback to software CPU encoding
        if profile.backend != "software" and proc is not None:
            time.sleep(0.1)
            if proc.poll() is not None and proc.returncode != 0:
                logger.warning(
                    "Hardware transcode backend '%s' failed on startup (exit %d). Falling back to CPU software encoding.",
                    profile.backend,
                    proc.returncode,
                )
                profile = resolve_transcode_profile(
                    requested_backend="software",
                    capabilities=trans_caps,
                    target_width=target_w,
                    bitrate_kbps=bitrate,
                )
                cmd = _build_cmd(profile)
                proc = _spawn_proc(cmd)

        session = TranscodeSession(
            session_id=session_id,
            media_item_id=media_item_id,
            file_path=file_path,
            output_dir=session_dir,
            process=proc,
            target_resolution=resolution,
            vaapi_device=profile.device if profile.backend == "vaapi" else None,
            backend=profile.backend,
            seek_offset=seek_offset,
            audio_track_index=audio_track_index,
        )

        with self._lock:
            self._sessions[session_id] = session

        # Wait up to 1.5s for initial playlist to be generated
        start_wait = time.time()
        while time.time() - start_wait < 1.5:
            if playlist_path.exists() and playlist_path.stat().st_size > 50:
                break
            time.sleep(0.1)

        return session

    def stop_session(self, session_id: str) -> None:
        """Terminates session process and prunes its scratch directory."""
        session: TranscodeSession | None = None
        with self._lock:
            session = self._sessions.pop(session_id, None)

        if not session:
            return

        session.is_active = False
        proc = session.process
        if proc and proc.poll() is None:
            try:
                if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                else:
                    proc.terminate()
                proc.wait(timeout=TRANSCODE_PROCESS_STOP_TIMEOUT)
            except Exception:
                try:
                    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        proc.kill()
                except Exception as exc:
                    logger.debug("Force kill failed for transcode proc: %s", exc)

        try:
            if session.output_dir.exists():
                shutil.rmtree(session.output_dir, ignore_errors=True)
                logger.debug("Pruned transcode directory for session %s", session_id)
        except Exception as e:
            logger.debug(
                "Error pruning transcode directory %s: %s", session.output_dir, e
            )

    def cleanup_all(self) -> None:
        """Stops all active sessions and cleans all directories (called on shutdown)."""
        self.stop_reaper()
        session_ids = list(self._sessions.keys())
        for sid in session_ids:
            self.stop_session(sid)

    def clean_stale_directories(self, transcode_base_dir: Path) -> None:
        """Prunes stale transcode directories left behind by previous crashes or restarts."""
        transcode_base_dir = Path(transcode_base_dir)
        if not transcode_base_dir.is_dir():
            return
        try:
            for item in transcode_base_dir.iterdir():
                if item.is_dir() and item.name.startswith("hls_"):
                    shutil.rmtree(item, ignore_errors=True)
                    logger.debug("Cleaned stale transcode directory: %s", item)
        except Exception as e:
            logger.debug("Error cleaning stale transcode directories: %s", e)


# Global singleton transcode supervisor
transcode_supervisor = TranscodeSupervisor()
