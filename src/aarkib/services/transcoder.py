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
FFPROBE_STREAM_TIMEOUT: int = 12
SUBTITLE_CONVERT_TIMEOUT: int = 10
REMUX_PROCESS_STOP_TIMEOUT: float = 1.5
TRANSCODE_PROCESS_STOP_TIMEOUT: float = 2.0

# Cache detected VAAPI device to avoid probing repeatedly
_CACHED_VAAPI_DEVICE: str | None = None
_VAAPI_CHECKED: bool = False
_VAAPI_LOCK = threading.Lock()


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


def detect_vaapi_device(device_override: str | None = None) -> str | None:
    """Dynamically detects usable Linux VAAPI render nodes with fallback.

    Scans /dev/dri/renderD* devices, checks process access permissions, and
    executes a lightweight live test probe to ensure hardware encoding works.
    """
    global _CACHED_VAAPI_DEVICE, _VAAPI_CHECKED

    if device_override:
        return device_override

    with _VAAPI_LOCK:
        if _VAAPI_CHECKED:
            return _CACHED_VAAPI_DEVICE

        # Check environment variable first
        env_device = os.getenv("AARKIB_VAAPI_DEVICE")
        candidate_nodes: list[str] = []
        if env_device:
            candidate_nodes.append(env_device)

        dri_dir = Path("/dev/dri")
        if dri_dir.is_dir():
            render_nodes = sorted(str(p) for p in dri_dir.glob("renderD*"))
            for node in render_nodes:
                if node not in candidate_nodes:
                    candidate_nodes.append(node)

        ffmpeg_bin = get_ffmpeg_binary()
        if not ffmpeg_bin or not candidate_nodes:
            _VAAPI_CHECKED = True
            _CACHED_VAAPI_DEVICE = None
            return None

        for node in candidate_nodes:
            if not os.path.exists(node):
                continue
            if not os.access(node, os.R_OK | os.W_OK):
                logger.debug(
                    "Skipping VAAPI node %s: insufficient read/write permissions", node
                )
                continue

            try:
                # Probe VAAPI hardware encoder capability
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
                if res.returncode == 0:
                    logger.info(
                        "Successfully validated VAAPI hardware encoder on device: %s",
                        node,
                    )
                    _CACHED_VAAPI_DEVICE = node
                    _VAAPI_CHECKED = True
                    return _CACHED_VAAPI_DEVICE
                else:
                    logger.debug(
                        "VAAPI probe failed on device %s (exit code %d)",
                        node,
                        res.returncode,
                    )
            except Exception as e:
                logger.debug("VAAPI probe exception on device %s: %s", node, e)

        logger.info(
            "No usable VAAPI hardware acceleration device found; defaulting to CPU software encoding."
        )
        _CACHED_VAAPI_DEVICE = None
        _VAAPI_CHECKED = True
        return None


def reset_vaapi_cache() -> None:
    """Resets the cached VAAPI device discovery (useful in tests)."""
    global _CACHED_VAAPI_DEVICE, _VAAPI_CHECKED
    with _VAAPI_LOCK:
        _CACHED_VAAPI_DEVICE = None
        _VAAPI_CHECKED = False


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
    """Determines the optimal playback strategy for a media item."""
    from aarkib.models.capabilities import ClientCapabilities

    caps: ClientCapabilities | None = None
    if isinstance(client_caps, ClientCapabilities):
        caps = client_caps
    elif isinstance(client_caps, dict):
        caps = ClientCapabilities.from_dict(client_caps)

    if streams_info is None:
        streams_info = probe_media_streams(file_path)

    ext = Path(file_path).suffix.lower()
    container_native = (
        (ext in caps.video.containers)
        if caps and caps.video.containers
        else (ext in WEB_NATIVE_CONTAINERS)
    )
    reasons: list[str] = []

    video = streams_info.get("video") or {}
    v_codec = (video.get("codec") or "").lower()
    pix_fmt = (video.get("pix_fmt") or "").lower()

    # Check video codec compatibility
    is_10bit = "10" in pix_fmt or "p010" in pix_fmt
    if caps:
        video_native = bool(
            v_codec and caps.supports_video(v_codec, ext, is_10bit=is_10bit)
        )
    else:
        video_native = bool(
            v_codec and v_codec in WEB_NATIVE_VIDEO_CODECS and not is_10bit
        )

    if not video_native and v_codec:
        if is_10bit and (not caps or not caps.video.supports_10bit):
            reasons.append(f"10-bit color ({pix_fmt}) requires transcoding")
        else:
            client_target = caps.device.client_name if caps else "browser"
            reasons.append(
                f"Video codec '{v_codec}' is not natively supported by {client_target}"
            )

    # Check audio codecs compatibility
    audio_list = streams_info.get("audio", [])
    primary_audio = audio_list[0] if audio_list else {}
    a_codec = (primary_audio.get("codec") or "").lower()

    if caps:
        audio_native = bool(not a_codec or caps.supports_audio(a_codec, ext))
    else:
        audio_native = bool(not a_codec or a_codec in WEB_NATIVE_AUDIO_CODECS)

    if not audio_native and a_codec:
        client_target = caps.device.client_name if caps else "browser"
        reasons.append(
            f"Audio codec '{a_codec}' is not natively supported by {client_target}"
        )

    # Evaluate overall strategy using structural pattern matching
    match (container_native, video_native, audio_native):
        case (True, True, True):
            strategy = PlaybackStrategy.DIRECT_PLAY
        case (False, True, True) if ext in REMUXABLE_CONTAINERS:
            strategy = PlaybackStrategy.DIRECT_REMUX
            reasons.append(
                f"Container '{ext}' can be remuxed to MP4 on-the-fly with zero re-encoding"
            )
        case (_, True, False):
            strategy = PlaybackStrategy.AUDIO_TRANSCODE
            reasons.append(
                "Video stream can be copied directly while audio is transcoded to AAC"
            )
        case _:
            strategy = PlaybackStrategy.FULL_TRANSCODE
            if not reasons:
                reasons.append(
                    "Transcoding required for optimal browser playback compatibility"
                )

    return {
        "strategy": strategy.value,
        "reasons": reasons,
        "container": ext,
        "container_native": container_native,
        "video_codec": v_codec,
        "video_native": video_native,
        "audio_codec": a_codec,
        "audio_native": audio_native,
        "duration": streams_info.get("duration"),
        "resolution": {
            "width": video.get("width"),
            "height": video.get("height"),
        }
        if video.get("width")
        else None,
        "audio_tracks_count": len(audio_list),
        "subtitles_count": len(streams_info.get("subtitles", [])),
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

        # Detect VAAPI device if available and not explicitly disabled
        detected_vaapi = detect_vaapi_device(vaapi_device)

        # Build FFmpeg command line
        ffmpeg_bin = get_ffmpeg_binary() or "ffmpeg"
        cmd = [ffmpeg_bin, "-y"]

        if seek_offset > 0:
            cmd.extend(["-ss", str(seek_offset)])

        if detected_vaapi:
            cmd.extend(
                [
                    "-hwaccel",
                    "vaapi",
                    "-vaapi_device",
                    detected_vaapi,
                ]
            )

        cmd.extend(["-i", str(file_path)])
        cmd.extend(["-map", "0:v:0", "-map", f"0:a:{audio_track_index}?"])

        # Resolution scaling & codec parameters
        preset = RESOLUTION_PRESETS.get(resolution, RESOLUTION_PRESETS["original"])
        target_w = preset["width"]
        bitrate = preset["video_bitrate"]

        if detected_vaapi:
            vf_scale = (
                f"scale=w='min({target_w},iw)':h=-2,format=nv12,hwupload"
                if target_w
                else "format=nv12,hwupload"
            )
            cmd.extend(
                [
                    "-vf",
                    vf_scale,
                    "-c:v",
                    "h264_vaapi",
                    "-b:v",
                    f"{bitrate}k",
                    "-maxrate",
                    f"{int(bitrate * 1.25)}k",
                    "-bufsize",
                    f"{bitrate * 2}k",
                ]
            )
        else:
            vf_scale = f"scale=w='min({target_w},iw)':h=-2" if target_w else None
            if vf_scale:
                cmd.extend(["-vf", vf_scale])
            cmd.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                    "-maxrate",
                    f"{int(bitrate * 1.25)}k",
                    "-bufsize",
                    f"{bitrate * 2}k",
                    "-pix_fmt",
                    "yuv420p",
                ]
            )

        # Audio settings: AAC stereo
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ac", "2"])

        # HLS fMP4 flags: list_size 0 for full timeline seeking, independent segments
        playlist_path = session_dir / "playlist.m3u8"
        segment_pattern = str(session_dir / "segment_%05d.m4s")

        cmd.extend(
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

        proc: subprocess.Popen | None = None
        if get_ffmpeg_binary():
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid if hasattr(os, "setsid") else None,
                )
                logger.info(
                    "Launched HLS transcode process %d for session %s",
                    proc.pid,
                    session_id,
                )
            except Exception as e:
                logger.error(
                    "Failed to spawn FFmpeg process for session %s: %s", session_id, e
                )

        session = TranscodeSession(
            session_id=session_id,
            media_item_id=media_item_id,
            file_path=file_path,
            output_dir=session_dir,
            process=proc,
            target_resolution=resolution,
            vaapi_device=detected_vaapi,
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
