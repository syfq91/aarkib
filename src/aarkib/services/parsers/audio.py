"""Pure-Python parser for audio metadata (MP3/ID3, FLAC, WAV, and audio filename conventions)."""

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

from aarkib.services.parsers.base import (
    ParsedAudiobookMetadata,
    ParsedAudioMetadata,
    ParsedMusicMetadata,
)

logger = logging.getLogger(__name__)

AUDIO_FILENAME_PATTERNS = [
    # 01 - Artist - Title
    re.compile(
        r"^(?P<track>\d{1,3})[ ._-]+(?P<artist>[a-zA-Z].+?)[ ._-]+-[ ._-]+(?P<title>.+?)$",
        re.I,
    ),
    # Artist - Album - 01 - Title
    re.compile(
        r"^(?P<artist>.+?)[ ._-]+-[ ._-]+(?P<album>.+?)[ ._-]+-[ ._-]+(?P<track>\d{1,3})[ ._-]+-[ ._-]+(?P<title>.+?)$",
        re.I,
    ),
    # 01 - Title or 01. Title or 01 - Hotel California
    re.compile(r"^(?P<track>\d{1,3})[ ._-]+(?P<title>.+?)$", re.I),
    # Artist - Title
    re.compile(r"^(?P<artist>[a-zA-Z].+?)[ ._-]+-[ ._-]+(?P<title>.+?)$", re.I),
]


def _decode_id3_text(data: bytes, encoding: int) -> str:
    """Decodes ID3 frame text based on encoding byte."""
    try:
        if encoding == 0:
            return data.decode("iso-8859-1", errors="ignore").rstrip("\x00")
        elif encoding == 1:
            return data.decode("utf-16", errors="ignore").rstrip("\x00")
        elif encoding == 2:
            return data.decode("utf-16-be", errors="ignore").rstrip("\x00")
        elif encoding == 3:
            return data.decode("utf-8", errors="ignore").rstrip("\x00")
    except Exception:
        pass
    return data.decode("utf-8", errors="ignore").rstrip("\x00")


def parse_id3v2(file_path: Path) -> dict[str, Any]:
    """Pure-Python ID3v2.3 / ID3v2.4 tag and APIC cover art extractor."""
    meta: dict[str, Any] = {}
    try:
        with open(file_path, "rb") as f:
            header = f.read(10)
            if len(header) < 10 or header[:3] != b"ID3":
                return meta

            version_major = header[3]
            # 4 syncsafe bytes
            tag_size = (
                (header[6] << 21) | (header[7] << 14) | (header[8] << 7) | header[9]
            )
            tag_data = f.read(tag_size)

        stream = io.BytesIO(tag_data)
        while stream.tell() < tag_size:
            frame_hdr = stream.read(10)
            if len(frame_hdr) < 10 or frame_hdr[0] == 0:
                break

            frame_id = frame_hdr[:4].decode("latin-1", errors="ignore")
            if version_major == 4:
                # syncsafe size in v2.4
                frame_size = (
                    (frame_hdr[4] << 21)
                    | (frame_hdr[5] << 14)
                    | (frame_hdr[6] << 7)
                    | frame_hdr[7]
                )
            else:
                frame_size = struct.unpack(">I", frame_hdr[4:8])[0]

            if frame_size <= 0 or stream.tell() + frame_size > len(tag_data) + 10:
                break

            payload = stream.read(frame_size)
            if not payload:
                continue

            enc = payload[0]
            text_data = payload[1:]

            if frame_id == "TIT2":
                meta["title"] = _decode_id3_text(text_data, enc)
            elif frame_id in ("TPE1", "TPE2"):
                artist = _decode_id3_text(text_data, enc)
                if artist and "artist" not in meta:
                    meta["artist"] = artist
                if frame_id == "TPE2" and artist and "album_artist" not in meta:
                    meta["album_artist"] = artist
            elif frame_id == "TPE3":
                narrator = _decode_id3_text(text_data, enc)
                if narrator:
                    meta["narrator"] = narrator
            elif frame_id == "TCOM":
                author = _decode_id3_text(text_data, enc)
                if author and "author" not in meta:
                    meta["author"] = author
            elif frame_id == "TALB":
                meta["album"] = _decode_id3_text(text_data, enc)
            elif frame_id == "TRCK":
                raw_trck = _decode_id3_text(text_data, enc)
                m = re.match(r"^(\d+)", raw_trck.strip())
                if m:
                    meta["track_number"] = int(m.group(1))
            elif frame_id == "TPOS":
                raw_pos = _decode_id3_text(text_data, enc)
                m = re.match(r"^(\d+)", raw_pos.strip())
                if m:
                    meta["disc_number"] = int(m.group(1))
            elif frame_id in ("TDRC", "TYER"):
                meta["year"] = _decode_id3_text(text_data, enc)[:4]
            elif frame_id == "TCON":
                meta["genre"] = _decode_id3_text(text_data, enc)
            elif frame_id == "TLEN":
                try:
                    meta["duration"] = round(
                        float(_decode_id3_text(text_data, enc)) / 1000.0, 2
                    )
                except Exception:
                    pass
            elif frame_id == "CHAP":
                try:
                    cstream = io.BytesIO(payload)
                    elem_bytes = bytearray()
                    while True:
                        b = cstream.read(1)
                        if not b or b == b"\x00":
                            break
                        elem_bytes.extend(b)
                    elem_id = elem_bytes.decode("latin-1", errors="ignore").strip()

                    ch_hdr = cstream.read(16)
                    if len(ch_hdr) >= 16:
                        s_time, e_time, _, _ = struct.unpack(">IIII", ch_hdr)
                        start_time = round(s_time / 1000.0, 3)
                        end_time = round(e_time / 1000.0, 3)
                        ch_title = (
                            elem_id or f"Chapter {len(meta.get('chapters', [])) + 1}"
                        )

                        while cstream.tell() < len(payload):
                            sub_hdr = cstream.read(10)
                            if len(sub_hdr) < 10 or sub_hdr[0] == 0:
                                break
                            sub_id = sub_hdr[:4].decode("latin-1", errors="ignore")
                            sub_size = struct.unpack(">I", sub_hdr[4:8])[0]
                            if sub_size <= 0 or cstream.tell() + sub_size > len(
                                payload
                            ):
                                break
                            sub_data = cstream.read(sub_size)
                            if sub_id == "TIT2" and len(sub_data) > 1:
                                ch_title = _decode_id3_text(sub_data[1:], sub_data[0])

                        if "chapters" not in meta:
                            meta["chapters"] = []
                        meta["chapters"].append(
                            {
                                "id": len(meta["chapters"]),
                                "title": ch_title,
                                "start_time": start_time,
                                "end_time": end_time,
                            }
                        )
                except Exception as e:
                    logger.debug("ID3 CHAP parse error: %s", e)
            elif frame_id == "APIC" and "cover_bytes" not in meta:
                # Attached picture frame: [enc][mime\x00][pic_type][desc\x00][image_bytes]
                try:
                    pstream = io.BytesIO(payload)
                    p_enc = pstream.read(1)[0]
                    # read mime
                    mime_bytes = bytearray()
                    while True:
                        b = pstream.read(1)
                        if not b or b == b"\x00":
                            break
                        mime_bytes.extend(b)
                    pstream.read(1)  # picture type
                    # read description up to null
                    if p_enc in (1, 2):
                        # 2-byte null terminator for utf-16
                        while True:
                            w = pstream.read(2)
                            if not w or w == b"\x00\x00":
                                break
                    else:
                        while True:
                            b = pstream.read(1)
                            if not b or b == b"\x00":
                                break
                    img_data = pstream.read()
                    if len(img_data) > 100:
                        meta["cover_bytes"] = img_data
                except Exception as e:
                    logger.debug("APIC parse error: %s", e)

    except Exception as e:
        logger.debug("ID3v2 parse error for %s: %s", file_path, e)

    return meta


def parse_flac(file_path: Path) -> dict[str, Any]:
    """Pure-Python FLAC STREAMINFO, VORBIS_COMMENT and PICTURE metadata parser."""
    meta: dict[str, Any] = {}
    try:
        with open(file_path, "rb") as f:
            magic = f.read(4)
            if magic != b"fLaC":
                return meta

            while True:
                header = f.read(4)
                if len(header) < 4:
                    break

                is_last = bool(header[0] & 0x80)
                block_type = header[0] & 0x7F
                block_len = (header[1] << 16) | (header[2] << 8) | header[3]
                block_data = f.read(block_len)

                # STREAMINFO = block 0 (34 bytes)
                if block_type == 0 and len(block_data) >= 34:
                    sample_bytes = block_data[14:22]
                    sample_rate = (
                        (sample_bytes[0] << 12)
                        | (sample_bytes[1] << 4)
                        | (sample_bytes[2] >> 4)
                    )
                    total_samples = (
                        ((sample_bytes[3] & 0x0F) << 32)
                        | (sample_bytes[4] << 24)
                        | (sample_bytes[5] << 16)
                        | (sample_bytes[6] << 8)
                        | sample_bytes[7]
                    )
                    if sample_rate > 0:
                        meta["duration"] = round(total_samples / sample_rate, 2)

                # VORBIS_COMMENT = block 4
                elif block_type == 4 and len(block_data) >= 4:
                    stream = io.BytesIO(block_data)
                    vendor_len = struct.unpack("<I", stream.read(4))[0]
                    stream.read(vendor_len)
                    num_comments = struct.unpack("<I", stream.read(4))[0]
                    for _ in range(num_comments):
                        c_len_bytes = stream.read(4)
                        if len(c_len_bytes) < 4:
                            break
                        c_len = struct.unpack("<I", c_len_bytes)[0]
                        comment = stream.read(c_len).decode("utf-8", errors="ignore")
                        if "=" in comment:
                            k, v = comment.split("=", 1)
                            k_up = k.strip().upper()
                            v_clean = v.strip()
                            if k_up == "TITLE":
                                meta["title"] = v_clean
                            elif k_up in ("ARTIST", "PERFORMER"):
                                if "artist" not in meta:
                                    meta["artist"] = v_clean
                            elif k_up == "ALBUM":
                                meta["album"] = v_clean
                            elif k_up in ("TRACKNUMBER", "TRACK"):
                                m = re.match(r"^(\d+)", v_clean)
                                if m:
                                    meta["track_number"] = int(m.group(1))
                            elif k_up in ("DISCNUMBER", "DISC"):
                                m = re.match(r"^(\d+)", v_clean)
                                if m:
                                    meta["disc_number"] = int(m.group(1))
                            elif k_up in ("DATE", "YEAR"):
                                meta["year"] = v_clean[:4]
                            elif k_up == "GENRE":
                                meta["genre"] = v_clean
                            elif k_up in ("ALBUMARTIST", "ALBUM ARTIST"):
                                meta["album_artist"] = v_clean
                            elif k_up == "NARRATOR":
                                meta["narrator"] = v_clean
                            elif k_up in ("COMPOSER", "AUTHOR"):
                                meta["author"] = v_clean
                            elif k_up == "COMPILATION":
                                meta["is_compilation"] = v_clean in (
                                    "1",
                                    "true",
                                    "True",
                                )

                # PICTURE = block 6
                elif (
                    block_type == 6
                    and len(block_data) >= 32
                    and "cover_bytes" not in meta
                ):
                    try:
                        stream = io.BytesIO(block_data)
                        stream.read(4)  # type
                        mime_len = struct.unpack(">I", stream.read(4))[0]
                        stream.read(mime_len)  # mime
                        desc_len = struct.unpack(">I", stream.read(4))[0]
                        stream.read(desc_len)  # desc
                        stream.read(16)  # width, height, depth, colors
                        img_len = struct.unpack(">I", stream.read(4))[0]
                        img_data = stream.read(img_len)
                        if len(img_data) > 100:
                            meta["cover_bytes"] = img_data
                    except Exception:
                        pass

                if is_last:
                    break

    except Exception as e:
        logger.debug("FLAC parse error for %s: %s", file_path, e)

    return meta


def parse_wav(file_path: Path) -> dict[str, Any]:
    """Pure-Python RIFF/WAVE header parser."""
    meta: dict[str, Any] = {}
    try:
        with open(file_path, "rb") as f:
            riff_hdr = f.read(12)
            if (
                len(riff_hdr) < 12
                or riff_hdr[:4] != b"RIFF"
                or riff_hdr[8:12] != b"WAVE"
            ):
                return meta

            byte_rate = 0
            while True:
                chunk_hdr = f.read(8)
                if len(chunk_hdr) < 8:
                    break
                chunk_id, chunk_size = struct.unpack("<4sI", chunk_hdr)
                if chunk_id == b"fmt " and chunk_size >= 16:
                    fmt_data = f.read(chunk_size)
                    channels, sample_rate, byte_rate = struct.unpack(
                        "<HII", fmt_data[2:12]
                    )
                    if byte_rate > 0:
                        meta["bitrate"] = round((byte_rate * 8) / 1000)
                elif chunk_id == b"data":
                    if byte_rate > 0 and chunk_size > 0:
                        meta["duration"] = round(chunk_size / byte_rate, 2)
                    break
                else:
                    f.seek(chunk_size, io.SEEK_CUR)

    except Exception as e:
        logger.debug("WAV parse error for %s: %s", file_path, e)

    return meta


def parse_audio_filename(file_path: Path) -> dict[str, Any]:
    """Deduces title, artist, album, and track number from filename."""
    stem = file_path.stem
    for pat in AUDIO_FILENAME_PATTERNS:
        m = pat.match(stem)
        if m:
            gd = m.groupdict()
            res: dict[str, Any] = {}
            if "title" in gd and gd["title"]:
                res["title"] = gd["title"].lstrip("- ").strip()
            if "artist" in gd and gd["artist"]:
                res["artist"] = gd["artist"].strip()
            if "album" in gd and gd["album"]:
                res["album"] = gd["album"].strip()
            if "track" in gd and gd["track"]:
                try:
                    res["track_number"] = int(gd["track"])
                except ValueError:
                    pass
            return res

    # Fallback to cleaned stem
    cleaned = re.sub(r"[._-]+", " ", stem).strip()
    return {"title": cleaned or stem}


def extract_audio_cover(file_path: Path) -> bytes | None:
    """Extracts cover artwork from embedded audio tags or neighboring cover images."""
    # 1. Embedded tags
    ext = file_path.suffix.lower()
    if ext == ".mp3":
        id3_meta = parse_id3v2(file_path)
        if id3_meta.get("cover_bytes"):
            return id3_meta["cover_bytes"]
    elif ext == ".flac":
        flac_meta = parse_flac(file_path)
        if flac_meta.get("cover_bytes"):
            return flac_meta["cover_bytes"]

    # 2. Neighboring files in same directory
    parent = file_path.parent
    candidate_names = [
        "cover.jpg",
        "cover.png",
        "cover.webp",
        "folder.jpg",
        "folder.png",
        "album.jpg",
        "album.png",
        "artwork.jpg",
        "artwork.png",
        f"{file_path.stem}.jpg",
        f"{file_path.stem}.png",
        f"{file_path.stem}.webp",
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

    return None


def parse_mp4_chapters(file_path: Path) -> list[dict[str, Any]]:
    """Pure-Python extractor for QuickTime / MP4 chapter markers (moov -> udta -> chpl)."""
    chapters: list[dict[str, Any]] = []
    try:
        with open(file_path, "rb") as f:
            moov_bytes = None
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                box_size, box_type = struct.unpack(">I4s", hdr)
                if box_size == 1:
                    large_hdr = f.read(8)
                    if len(large_hdr) < 8:
                        break
                    box_size = struct.unpack(">Q", large_hdr)[0]
                    payload_len = box_size - 16
                elif box_size == 0:
                    payload_len = None
                else:
                    payload_len = box_size - 8

                if box_type == b"moov":
                    moov_bytes = (
                        f.read(payload_len) if payload_len is not None else f.read()
                    )
                    break
                else:
                    if payload_len is not None and payload_len > 0:
                        f.seek(payload_len, io.SEEK_CUR)
                    else:
                        break

            if not moov_bytes:
                return chapters

            stream = io.BytesIO(moov_bytes)
            chpl_payload = None
            timescale = 1000
            duration = 0.0

            while stream.tell() < len(moov_bytes):
                hdr = stream.read(8)
                if len(hdr) < 8:
                    break
                sub_size, sub_type = struct.unpack(">I4s", hdr)
                if sub_size < 8:
                    break
                sub_payload_len = sub_size - 8
                sub_data = stream.read(sub_payload_len)

                if sub_type == b"mvhd" and len(sub_data) >= 20:
                    ver = sub_data[0]
                    if ver == 0:
                        timescale, raw_dur = struct.unpack(">II", sub_data[12:20])
                    elif ver == 1 and len(sub_data) >= 32:
                        timescale, raw_dur = struct.unpack(">IQ", sub_data[20:32])
                    else:
                        timescale, raw_dur = 1000, 0
                    if timescale > 0:
                        duration = round(raw_dur / timescale, 2)

                elif sub_type == b"udta":
                    u_stream = io.BytesIO(sub_data)
                    while u_stream.tell() < len(sub_data):
                        u_hdr = u_stream.read(8)
                        if len(u_hdr) < 8:
                            break
                        u_size, u_type = struct.unpack(">I4s", u_hdr)
                        if u_size < 8:
                            break
                        u_payload_len = u_size - 8
                        u_payload = u_stream.read(u_payload_len)
                        if u_type == b"chpl":
                            chpl_payload = u_payload
                            break

                elif sub_type == b"chpl":
                    chpl_payload = sub_data

            if chpl_payload and len(chpl_payload) >= 5:
                cp_stream = io.BytesIO(chpl_payload)
                # Skip version (1), flags (3), reserved (1)
                cp_stream.read(5)
                count_bytes = cp_stream.read(4)
                if len(count_bytes) == 4:
                    num_chapters = struct.unpack(">I", count_bytes)[0]
                    raw_chapters: list[dict[str, Any]] = []
                    for i in range(num_chapters):
                        ts_bytes = cp_stream.read(8)
                        if len(ts_bytes) < 8:
                            break
                        timestamp = struct.unpack(">Q", ts_bytes)[0]
                        len_byte = cp_stream.read(1)
                        if not len_byte:
                            break
                        title_len = len_byte[0]
                        title_bytes = cp_stream.read(title_len)
                        title = (
                            title_bytes.decode("utf-8", errors="ignore").strip()
                            or f"Chapter {i + 1}"
                        )
                        # In Nero chpl, timestamp is in 100ns units (10 MHz)
                        start_time = round(timestamp / 10000000.0, 3)
                        raw_chapters.append(
                            {"id": i, "title": title, "start_time": start_time}
                        )

                    for i in range(len(raw_chapters)):
                        if i + 1 < len(raw_chapters):
                            raw_chapters[i]["end_time"] = raw_chapters[i + 1][
                                "start_time"
                            ]
                        else:
                            fallback_end = (
                                duration
                                if duration > raw_chapters[i]["start_time"]
                                else raw_chapters[i]["start_time"] + 60.0
                            )
                            raw_chapters[i]["end_time"] = round(fallback_end, 3)
                    chapters = raw_chapters

    except Exception as e:
        logger.debug("Failed to parse MP4 chapters from %s: %s", file_path, e)

    return chapters


def read_ffprobe_chapters(file_path: Path) -> list[dict[str, Any]]:
    """Uses ffprobe CLI if available to extract container chapters."""
    if not shutil.which("ffprobe"):
        return []

    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_chapters",
            str(file_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return []

        data = json.loads(res.stdout)
        ch_list = data.get("chapters", [])
        parsed = []
        for idx, ch in enumerate(ch_list):
            start = round(float(ch.get("start_time", 0.0)), 3)
            end = round(float(ch.get("end_time", start)), 3)
            tags = ch.get("tags", {})
            title = tags.get("title") or f"Chapter {idx + 1}"
            parsed.append(
                {"id": idx, "title": title, "start_time": start, "end_time": end}
            )
        return parsed
    except Exception as e:
        logger.debug("ffprobe chapter extraction failed for %s: %s", file_path, e)
        return []


def parse_audio(file_path: Path) -> ParsedAudioMetadata:
    """Parses audio files into a ParsedAudioMetadata, ParsedAudiobookMetadata, or ParsedMusicMetadata."""
    ext = file_path.suffix.lower()
    meta: dict[str, Any] = {}

    if ext == ".mp3":
        meta = parse_id3v2(file_path)
    elif ext == ".flac":
        meta = parse_flac(file_path)
    elif ext == ".wav":
        meta = parse_wav(file_path)
    elif ext in (".m4a", ".m4b"):
        from aarkib.services.parsers.video import read_mp4_metadata

        meta = read_mp4_metadata(file_path)

    # Filename fallback for missing fields
    fn_meta = parse_audio_filename(file_path)
    title = meta.get("title") or fn_meta.get("title") or file_path.stem
    artist = meta.get("artist") or fn_meta.get("artist")
    album = meta.get("album") or fn_meta.get("album")
    track_number = meta.get("track_number") or fn_meta.get("track_number")
    disc_number = meta.get("disc_number")
    duration = meta.get("duration")
    bitrate = meta.get("bitrate")
    year = meta.get("year")
    genre = meta.get("genre")

    tags: list[str] = ["Audio"]
    if ext == ".m4b" and "Audiobook" not in tags:
        tags.append("Audiobook")
    if genre and genre not in tags:
        tags.append(genre)

    cover = meta.get("cover_bytes") or extract_audio_cover(file_path)
    creators = [artist] if artist else []

    # Chapter marker extraction
    chapters = (
        meta.get("chapters")
        or parse_mp4_chapters(file_path)
        or read_ffprobe_chapters(file_path)
    )

    is_audiobook = (
        ext == ".m4b"
        or "Audiobook" in tags
        or (genre and "audiobook" in genre.lower())
        or bool(chapters and duration and duration > 1800)
    )

    if is_audiobook:
        author = meta.get("author") or artist
        narrator = meta.get("narrator")
        if not narrator:
            m_narrator = re.search(
                r"narrat(?:ed)?\s+by\s+([^-_,()]+)", file_path.stem, re.I
            )
            if m_narrator:
                narrator = m_narrator.group(1).strip()

        abridged = bool(
            re.search(r"\b(abridged)\b", file_path.stem, re.I)
            and not re.search(r"\b(unabridged)\b", file_path.stem, re.I)
        )

        return ParsedAudiobookMetadata(
            title=title,
            author=author,
            creators=[author] if author else creators,
            narrator=narrator,
            chapters=chapters,
            abridged=abridged,
            series=album,
            series_index=float(track_number) if track_number is not None else None,
            album=album,
            track_number=track_number,
            disc_number=disc_number,
            duration=duration,
            bitrate=bitrate,
            tags=tags,
            cover_bytes=cover,
            file_format=ext.lstrip("."),
            publication_date=year,
        )

    return ParsedMusicMetadata(
        title=title,
        creators=creators,
        series=album,
        series_index=float(track_number) if track_number is not None else None,
        album=album,
        album_artist=meta.get("album_artist") or artist,
        genre=genre,
        release_year=year,
        is_compilation=meta.get("is_compilation", False),
        track_number=track_number,
        disc_number=disc_number,
        duration=duration,
        bitrate=bitrate,
        tags=tags,
        cover_bytes=cover,
        file_format=ext.lstrip("."),
        publication_date=year,
    )


def parse_audiobook(file_path: Path) -> ParsedAudiobookMetadata:
    """Explicit parser for audiobook files."""
    meta = parse_audio(file_path)
    if isinstance(meta, ParsedAudiobookMetadata):
        return meta
    # Wrap in ParsedAudiobookMetadata if parsed as generic music
    return ParsedAudiobookMetadata(
        title=meta.title,
        creators=meta.creators,
        author=meta.creators[0] if meta.creators else None,
        narrator=None,
        chapters=[],
        abridged=False,
        series=meta.series,
        series_index=meta.series_index,
        album=meta.album,
        track_number=meta.track_number,
        disc_number=meta.disc_number,
        duration=meta.duration,
        bitrate=meta.bitrate,
        tags=meta.tags,
        cover_bytes=meta.cover_bytes,
        file_format=meta.file_format,
        publication_date=meta.publication_date,
    )


def parse_music(file_path: Path) -> ParsedMusicMetadata:
    """Explicit parser for music files."""
    meta = parse_audio(file_path)
    if isinstance(meta, ParsedMusicMetadata):
        return meta
    return ParsedMusicMetadata(
        title=meta.title,
        creators=meta.creators,
        series=meta.series,
        series_index=meta.series_index,
        album=meta.album,
        track_number=meta.track_number,
        disc_number=meta.disc_number,
        duration=meta.duration,
        bitrate=meta.bitrate,
        tags=meta.tags,
        cover_bytes=meta.cover_bytes,
        file_format=meta.file_format,
        publication_date=meta.publication_date,
    )
