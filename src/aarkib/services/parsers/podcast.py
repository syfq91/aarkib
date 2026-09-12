"""Pure-Python parser for podcast episode metadata, tags, and filename heuristics."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from aarkib.services.parsers.audio import (
    extract_audio_cover,
    parse_audio,
    parse_id3v2,
)
from aarkib.services.parsers.base import ParsedPodcastMetadata

logger = logging.getLogger(__name__)

PODCAST_FILENAME_PATTERNS = [
    # Show Name - S01E02 - Episode Title
    re.compile(
        r"^(?P<show>.+?)[ ._-]+[sS](?P<season>\d{1,2})[eE](?P<episode>\d{1,4})[ ._-]+(?P<title>.+?)$"
    ),
    # Show Name - Ep 02 - Episode Title or Show Name - Episode 02 - Episode Title
    re.compile(
        r"^(?P<show>.+?)[ ._-]+(?:[eE]p(?:isode)?\.?|[#№])[ ._-]*(?P<episode>\d{1,4})[ ._-]+(?P<title>.+?)$",
        re.IGNORECASE,
    ),
    # Show Name - 2026-09-12 - Episode Title
    re.compile(
        r"^(?P<show>.+?)[ ._-]+(?P<date>\d{4}[-_.]\d{2}[-_.]\d{2})[ ._-]+(?P<title>.+?)$"
    ),
    # 2026-09-12 - Episode Title
    re.compile(r"^(?P<date>\d{4}[-_.]\d{2}[-_.]\d{2})[ ._-]+(?P<title>.+?)$"),
    # Show Name - 042 - Episode Title
    re.compile(
        r"^(?P<show>[a-zA-Z].+?)[ ._-]+(?P<episode>\d{1,4})[ ._-]+(?P<title>.+?)$"
    ),
    # 042 - Episode Title
    re.compile(r"^(?P<episode>\d{1,4})[ ._-]+(?P<title>.+?)$"),
    # Show Name - Episode Title
    re.compile(r"^(?P<show>[a-zA-Z].+?)[ ._-]+-[ ._-]+(?P<title>.+?)$"),
]


def parse_podcast_filename(file_path: Path) -> dict[str, Any]:
    """Extracts show name, episode, season, and title from podcast filename heuristics."""
    stem = file_path.stem
    result: dict[str, Any] = {}

    for pat in PODCAST_FILENAME_PATTERNS:
        m = pat.match(stem)
        if m:
            groups = m.groupdict()
            if "show" in groups and groups["show"]:
                result["show"] = groups["show"].strip()
            if "title" in groups and groups["title"]:
                result["title"] = groups["title"].strip()
            if "season" in groups and groups["season"]:
                try:
                    result["season"] = int(groups["season"])
                except ValueError:
                    pass
            if "episode" in groups and groups["episode"]:
                try:
                    result["episode"] = int(groups["episode"])
                except ValueError:
                    pass
            if "date" in groups and groups["date"]:
                result["publication_date"] = (
                    groups["date"].replace("_", "-").replace(".", "-")
                )
            break

    if "show" not in result:
        # Fall back to parent folder name if not root
        parent_name = file_path.parent.name
        if parent_name and parent_name not in (
            ".",
            "/",
            "data",
            "media",
            "podcasts",
            "audio",
        ):
            result["show"] = parent_name.replace("_", " ").title()

    if "title" not in result:
        result["title"] = stem.replace("_", " ").strip()

    return result


def parse_podcast(file_path: Path) -> ParsedPodcastMetadata:
    """Parses podcast episode audio files into a ParsedPodcastMetadata object."""
    suffix = file_path.suffix.lower()
    fn_meta = parse_podcast_filename(file_path)

    raw_tags: dict[str, Any] = {}
    if suffix in (".mp3", ".aac"):
        raw_tags = parse_id3v2(file_path)
    else:
        # Use general audio parser for FLAC, OGG, M4A, etc.
        parsed_audio = parse_audio(file_path)
        if parsed_audio:
            raw_tags = {
                "title": parsed_audio.title,
                "artist": parsed_audio.authors[0] if parsed_audio.authors else None,
                "album": parsed_audio.album,
                "track_number": parsed_audio.track_number,
                "disc_number": parsed_audio.disc_number,
                "duration": parsed_audio.duration,
                "bitrate": parsed_audio.bitrate,
                "year": parsed_audio.publication_date,
                "cover_bytes": parsed_audio.cover_bytes,
                "description": parsed_audio.description,
            }

    # Episode title
    title = raw_tags.get("title") or fn_meta.get("title") or file_path.stem

    # Show title (series/collection)
    show_title = raw_tags.get("album") or fn_meta.get("show")

    # Host / Creators
    creators = []
    if raw_tags.get("artist"):
        creators.append(raw_tags["artist"])
    elif raw_tags.get("album_artist"):
        creators.append(raw_tags["album_artist"])
    elif raw_tags.get("author"):
        creators.append(raw_tags["author"])
    elif show_title:
        creators.append(show_title)

    # Episode & Season numbers
    episode = raw_tags.get("track_number")
    if episode is None:
        episode = fn_meta.get("episode")

    season = raw_tags.get("disc_number")
    if season is None:
        season = fn_meta.get("season")

    # Duration & Bitrate
    duration = raw_tags.get("duration")
    bitrate = raw_tags.get("bitrate")

    # Description / Notes
    description = raw_tags.get("description")

    # Feed URL & GUID
    feed_url = raw_tags.get("podcast_feed_url")
    guid = raw_tags.get("podcast_guid")

    # Cover bytes
    cover_bytes = raw_tags.get("cover_bytes")
    if not cover_bytes:
        cover_bytes = extract_audio_cover(file_path)

    # Publication date
    pub_date = raw_tags.get("year") or fn_meta.get("publication_date")

    tags = ["Podcast"]
    if raw_tags.get("genre") and raw_tags["genre"] not in tags:
        tags.append(raw_tags["genre"])

    return ParsedPodcastMetadata(
        title=title,
        creators=creators,
        show_title=show_title,
        episode=episode,
        season=season,
        series=show_title,
        series_index=float(episode) if episode is not None else None,
        duration=duration,
        bitrate=bitrate,
        description=description,
        publication_date=str(pub_date) if pub_date else None,
        feed_url=feed_url,
        guid=guid,
        cover_bytes=cover_bytes,
        file_format=suffix.lstrip("."),
        tags=tags,
    )
