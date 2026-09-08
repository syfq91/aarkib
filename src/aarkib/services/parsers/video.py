from __future__ import annotations

import io
import json
import logging
import re
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any

from aarkib.services.parsers.base import ParsedVideoMetadata

logger = logging.getLogger(__name__)

# Common video file extensions
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v"}

# TV Show pattern matchers
TV_PATTERNS = [
    re.compile(
        r"^(?P<show>.+?)[ ._-]+[sS](?P<season>\d{1,2})[eE](?P<episode>\d{1,2})(?:[ ._-]+(?P<title>.*?))?$",
        re.I,
    ),
    re.compile(
        r"^(?P<show>.+?)[ ._-]+(?P<season>\d{1,2})x(?P<episode>\d{1,2})(?:[ ._-]+(?P<title>.*?))?$",
        re.I,
    ),
    re.compile(
        r"^(?P<show>.+?)[ ._-]+[sS]eason[ ._-]*(?P<season>\d{1,2})[ ._-]+[eE]pisode[ ._-]*(?P<episode>\d{1,2})(?:[ ._-]+(?P<title>.*?))?$",
        re.I,
    ),
]

# Movie pattern matchers
MOVIE_PATTERN = re.compile(
    r"^(?P<title>.+?)[ ._(-]+(?P<year>19\d\d|20\d\d)[ ._)-]*(?P<rest>.*)$",
    re.I,
)

# Quality & release tags to strip from titles and convert into tags
TAG_PATTERNS = [
    (re.compile(r"\b2160p\b|\b4k\b|\buhd\b", re.I), "4K UHD"),
    (re.compile(r"\b1080p\b", re.I), "1080p"),
    (re.compile(r"\b720p\b", re.I), "720p"),
    (re.compile(r"\b480p\b", re.I), "480p"),
    (re.compile(r"\bhdr(?:10)?\b", re.I), "HDR"),
    (re.compile(r"\b(x265|hevc|h265)\b", re.I), "HEVC"),
    (re.compile(r"\b(x264|h264|avc)\b", re.I), "H.264"),
    (re.compile(r"\b(web-?dl|webrip)\b", re.I), "WEB"),
    (re.compile(r"\b(bluray|blu-ray|bdrip)\b", re.I), "BluRay"),
]

CLEAN_NOISE = re.compile(
    r"\b(1080p|720p|480p|2160p|4k|uhd|bluray|blu-ray|web-?dl|webrip|brrip|dvdrip|"
    r"h264|h265|x264|x265|hevc|aac|ac3|dts|remux|hdr|sdr|proper|repack)\b",
    re.I,
)


def _clean_title(text: str) -> str:
    """Cleans punctuation, delimiters, and release tags from a raw title string."""
    if not text:
        return ""
    cleaned = CLEAN_NOISE.sub("", text)
    cleaned = re.sub(r"[\[\]\(\)]", " ", cleaned)
    cleaned = re.sub(r"[._-]+", " ", cleaned).strip()
    words = cleaned.split()
    return " ".join(w.capitalize() for w in words)


def parse_video_filename(file_path: Path) -> dict[str, Any]:
    """Extracts title, series, season, episode, year, and tags from filename."""
    stem = file_path.stem
    tags: list[str] = []

    # Extract quality tags from stem
    for pat, tag_name in TAG_PATTERNS:
        if pat.search(stem) and tag_name not in tags:
            tags.append(tag_name)

    # 1. Check if TV show episode
    for tv_pat in TV_PATTERNS:
        m = tv_pat.match(stem)
        if m:
            show_name = _clean_title(m.group("show"))
            season = int(m.group("season"))
            episode = int(m.group("episode"))
            raw_title = m.group("title") or ""
            ep_title = _clean_title(raw_title)

            # Display title
            if ep_title:
                display_title = f"{ep_title}"
            else:
                display_title = f"Season {season}, Episode {episode}"

            if "TV Show" not in tags:
                tags.insert(0, "TV Show")

            series_index = round(season + (episode / 100.0), 2)

            return {
                "title": display_title,
                "series": show_name,
                "series_index": series_index,
                "season": season,
                "episode": episode,
                "tags": tags,
                "creators": [show_name] if show_name else [],
                "publication_date": None,
            }

    # 2. Check if movie with year
    movie_m = MOVIE_PATTERN.match(stem)
    if movie_m:
        title = _clean_title(movie_m.group("title"))
        year = movie_m.group("year")
        if "Movie" not in tags:
            tags.insert(0, "Movie")

        return {
            "title": title or stem,
            "series": None,
            "series_index": None,
            "season": None,
            "episode": None,
            "tags": tags,
            "creators": [],
            "publication_date": year,
        }

    # 3. Generic fallback video
    clean_stem = _clean_title(stem) or stem
    if "Video" not in tags:
        tags.insert(0, "Video")

    return {
        "title": clean_stem,
        "series": None,
        "series_index": None,
        "season": None,
        "episode": None,
        "tags": tags,
        "creators": [],
        "publication_date": None,
    }


def read_mp4_metadata(file_path: Path) -> dict[str, Any]:
    """Pure-Python parser for MP4/MOV container metadata (duration, width, height)."""
    meta: dict[str, Any] = {}
    if file_path.suffix.lower() not in (".mp4", ".m4v", ".mov"):
        return meta

    try:
        with open(file_path, "rb") as f:
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                box_size, box_type = struct.unpack(">I4s", header)
                if box_size == 1:
                    large_header = f.read(8)
                    if len(large_header) < 8:
                        break
                    box_size = struct.unpack(">Q", large_header)[0]
                    payload_size = box_size - 16
                elif box_size == 0:
                    payload_size = None
                else:
                    payload_size = box_size - 8

                if box_type == b"moov":
                    moov_bytes = f.read(payload_size) if payload_size else f.read()
                    _parse_moov_payload(moov_bytes, meta)
                    break
                else:
                    if payload_size is not None and payload_size > 0:
                        f.seek(payload_size, io.SEEK_CUR)
                    else:
                        break
    except Exception as e:
        logger.debug("Pure Python MP4 parse error for %s: %s", file_path, e)

    return meta


def _parse_moov_payload(payload: bytes, meta: dict[str, Any]) -> None:
    """Parses mvhd and trak boxes inside moov atom."""
    stream = io.BytesIO(payload)
    while True:
        hdr = stream.read(8)
        if len(hdr) < 8:
            break
        sub_size, sub_type = struct.unpack(">I4s", hdr)
        sub_payload_len = sub_size - 8
        if sub_payload_len < 0:
            break
        sub_payload = stream.read(sub_payload_len)

        if sub_type == b"mvhd" and len(sub_payload) >= 20:
            ver = sub_payload[0]
            if ver == 0:
                timescale, duration = struct.unpack(">II", sub_payload[12:20])
            elif ver == 1 and len(sub_payload) >= 32:
                timescale, duration = struct.unpack(">IQ", sub_payload[20:32])
            else:
                timescale, duration = 0, 0
            if timescale > 0:
                meta["duration"] = round(duration / timescale, 2)

        elif sub_type == b"trak":
            # Search for tkhd inside trak
            trak_stream = io.BytesIO(sub_payload)
            while True:
                thdr = trak_stream.read(8)
                if len(thdr) < 8:
                    break
                tsize, ttype = struct.unpack(">I4s", thdr)
                tpayload_len = tsize - 8
                if tpayload_len < 0:
                    break
                tpayload = trak_stream.read(tpayload_len)
                if ttype == b"tkhd" and len(tpayload) >= 8:
                    w_fixed, h_fixed = struct.unpack(">II", tpayload[-8:])
                    w, h = w_fixed >> 16, h_fixed >> 16
                    if w > 0 and h > 0 and "resolution_width" not in meta:
                        meta["resolution_width"] = w
                        meta["resolution_height"] = h


def read_ffprobe_metadata(file_path: Path) -> dict[str, Any] | None:
    """Uses ffprobe CLI if available to extract container and stream metadata."""
    if not shutil.which("ffprobe"):
        return None

    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-select_streams",
            "v:0",
            str(file_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return None

        data = json.loads(res.stdout)
        meta: dict[str, Any] = {}

        # Duration
        fmt = data.get("format", {})
        if "duration" in fmt:
            try:
                meta["duration"] = round(float(fmt["duration"]), 2)
            except ValueError, TypeError:
                pass

        # Video stream specs
        streams = data.get("streams", [])
        if streams:
            vstream = streams[0]
            if "width" in vstream and "height" in vstream:
                meta["resolution_width"] = int(vstream["width"])
                meta["resolution_height"] = int(vstream["height"])
            if "codec_name" in vstream:
                meta["codec"] = vstream["codec_name"].upper()
            if "duration" in vstream and "duration" not in meta:
                try:
                    meta["duration"] = round(float(vstream["duration"]), 2)
                except ValueError, TypeError:
                    pass

        return meta
    except Exception as e:
        logger.debug("ffprobe error for %s: %s", file_path, e)
        return None


def extract_video_cover(file_path: Path) -> bytes | None:
    """Extracts video poster artwork from neighboring image files or video frame via ffmpeg."""
    parent = file_path.parent
    stem = file_path.stem

    # 1. Look for matching poster / cover files in the same folder
    candidate_names = [
        f"{stem}-poster.jpg",
        f"{stem}-poster.webp",
        f"{stem}-poster.png",
        f"{stem}.jpg",
        f"{stem}.webp",
        f"{stem}.png",
        "poster.jpg",
        "poster.webp",
        "poster.png",
        "cover.jpg",
        "cover.webp",
        "cover.png",
        "folder.jpg",
    ]

    for c_name in candidate_names:
        c_path = parent / c_name
        if c_path.is_file():
            try:
                data = c_path.read_bytes()
                if len(data) > 100:
                    return data
            except Exception:
                pass

    # 2. If ffmpeg is installed, grab a snapshot frame at 5 seconds (or 10%)
    if shutil.which("ffmpeg"):
        try:
            cmd = [
                "ffmpeg",
                "-ss",
                "00:00:05",
                "-i",
                str(file_path),
                "-vframes",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-",
            ]
            res = subprocess.run(cmd, capture_output=True, timeout=10)
            if res.returncode == 0 and len(res.stdout) > 500:
                return res.stdout
        except Exception as e:
            logger.debug("ffmpeg frame capture error for %s: %s", file_path, e)

    return None


def parse_video(file_path: Path) -> ParsedVideoMetadata:
    """Combines filename metadata and technical container metadata into ParsedVideoMetadata."""
    info = parse_video_filename(file_path)

    # Technical metadata
    tech = read_ffprobe_metadata(file_path) or read_mp4_metadata(file_path)

    duration = tech.get("duration")
    width = tech.get("resolution_width")
    height = tech.get("resolution_height")
    codec = tech.get("codec")

    # Add resolution tag if detected
    tags = list(info["tags"])
    if height:
        if height >= 2160 and "4K UHD" not in tags:
            tags.append("4K UHD")
        elif height >= 1080 and "1080p" not in tags:
            tags.append("1080p")
        elif height >= 720 and "720p" not in tags:
            tags.append("720p")

    fmt = file_path.suffix.lower().lstrip(".")
    cover = extract_video_cover(file_path)

    return ParsedVideoMetadata(
        title=info["title"],
        creators=info["creators"],
        series=info["series"],
        series_index=info["series_index"],
        season=info["season"],
        episode=info["episode"],
        duration=duration,
        resolution_width=width,
        resolution_height=height,
        codec=codec,
        tags=tags,
        cover_bytes=cover,
        file_format=fmt,
        media_type="video",
        publication_date=info.get("publication_date"),
    )
