"""Pure-Python parser for audio metadata (MP3/ID3, FLAC, WAV, and audio filename conventions)."""

from __future__ import annotations

import io
import logging
import re
import struct
from pathlib import Path
from typing import Any

from aarkib.services.parsers.base import ParsedAudioMetadata

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


def parse_audio(file_path: Path) -> ParsedAudioMetadata:
    """Parses audio files into a unified ParsedAudioMetadata representation."""
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

    return ParsedAudioMetadata(
        title=title,
        creators=creators,
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
        media_type="audio",
        publication_date=year,
    )
