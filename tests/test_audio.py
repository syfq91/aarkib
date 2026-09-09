from __future__ import annotations

import struct
from pathlib import Path

from aarkib.extensions import db
from aarkib.models import Collection, Creator, MediaItem, MediaType
from aarkib.plugins import AudioMediaPlugin
from aarkib.services.parsers.audio import (
    extract_audio_cover,
    parse_audio,
    parse_audio_filename,
    parse_id3v2,
    parse_wav,
)


def test_audio_plugin_metadata():
    plugin = AudioMediaPlugin()
    assert plugin.name == "audio"
    assert plugin.media_type == "audio"
    assert ".mp3" in plugin.supported_extensions
    assert ".flac" in plugin.supported_extensions
    assert ".wav" in plugin.supported_extensions
    assert plugin.get_player_url(55, "mp3") == "/reader/audio/55"

    health = plugin.check_health()
    assert health["status"] == "ok"
    assert health["plugin"] == "audio"
    assert ".mp3" in health["supported_extensions"]


def test_audio_filename_parsing():
    p1 = Path("/music/Queen - Bohemian Rhapsody.mp3")
    m1 = parse_audio_filename(p1)
    assert m1["artist"] == "Queen"
    assert m1["title"] == "Bohemian Rhapsody"

    p2 = Path("/music/03 - Hotel California.flac")
    m2 = parse_audio_filename(p2)
    assert m2["track_number"] == 3
    assert m2["title"] == "Hotel California"


def test_audio_id3v2_pure_python_parser(tmp_path: Path):
    # Construct a synthetic ID3v2.3 tag
    # Header: ID3 (3 bytes), ver 3 (1 byte), rev 0 (1 byte), flags 0 (1 byte), size 4 syncsafe bytes
    payload_title = b"\x00" + b"Song of Freedom"
    frame_title = (
        b"TIT2" + struct.pack(">I", len(payload_title)) + b"\x00\x00" + payload_title
    )
    payload_artist = b"\x00" + b"Artist One"
    frame_artist = (
        b"TPE1" + struct.pack(">I", len(payload_artist)) + b"\x00\x00" + payload_artist
    )

    tag_body = frame_title + frame_artist
    body_len = len(tag_body)
    # syncsafe len
    s0 = (body_len >> 21) & 0x7F
    s1 = (body_len >> 14) & 0x7F
    s2 = (body_len >> 7) & 0x7F
    s3 = body_len & 0x7F
    header = b"ID3\x03\x00\x00" + bytes([s0, s1, s2, s3])

    mp3_file = tmp_path / "test.mp3"
    mp3_file.write_bytes(header + tag_body + b"\xff\xfb\x90\x64" + b"\x00" * 500)

    res = parse_id3v2(mp3_file)
    assert res.get("title") == "Song of Freedom"
    assert res.get("artist") == "Artist One"

    meta = parse_audio(mp3_file)
    assert meta.title == "Song of Freedom"
    assert meta.creators == ["Artist One"]
    assert meta.media_type == "audio"
    assert meta.file_format == "mp3"


def test_audio_wav_pure_python_parser(tmp_path: Path):
    # Construct a synthetic WAV file
    # RIFF header + WAVE + fmt (16 bytes) + data
    channels = 2
    sample_rate = 44100
    byte_rate = sample_rate * channels * 2
    fmt_payload = struct.pack("<HHIIHH", 1, channels, sample_rate, byte_rate, 4, 16)
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload

    data_bytes = b"\x00" * (byte_rate * 2)  # 2 seconds
    data_chunk = b"data" + struct.pack("<I", len(data_bytes)) + data_bytes
    riff_payload = b"WAVE" + fmt_chunk + data_chunk
    wav_bytes = b"RIFF" + struct.pack("<I", len(riff_payload)) + riff_payload

    wav_file = tmp_path / "track.wav"
    wav_file.write_bytes(wav_bytes)

    res = parse_wav(wav_file)
    assert res.get("duration") == 2.0
    assert res.get("bitrate") is not None


def test_audio_cover_extraction(tmp_path: Path):
    audio_file = tmp_path / "song.mp3"
    audio_file.write_bytes(b"dummy")

    # Neighboring cover.jpg
    cover_file = tmp_path / "cover.jpg"
    cover_file.write_bytes(b"FAKE_JPEG_IMAGE_DATA_PADDING_TEST_1234567890" * 5)

    cover = extract_audio_cover(audio_file)
    assert cover is not None
    assert b"FAKE_JPEG_IMAGE_DATA" in cover


def test_audio_web_player_route(client, app):
    with app.app_context():
        artist = Creator(name="Daft Punk")
        album = Collection(name="Discovery")
        item = MediaItem(
            title="One More Time",
            original_file_path="/tmp/onemoretime.mp3",
            file_format="mp3",
            file_hash="hash_mp3_discovery",
            media_type=MediaType.AUDIO.value,
            duration=320.0,
            album="Discovery",
            track_number=1,
        )
        item.creators.append(artist)
        item.collection = album
        db.session.add_all([artist, album, item])
        db.session.commit()
        item_id = item.id

    # Test auto-dispatching /reader/item/<id> route
    res_dispatch = client.get(f"/reader/item/{item_id}")
    assert res_dispatch.status_code == 302
    assert f"/reader/audio/{item_id}" in res_dispatch.headers["Location"]

    # Test dedicated audio player view
    res = client.get(f"/reader/audio/{item_id}")
    assert res.status_code == 200
    assert b"One More Time" in res.data
    assert b"Daft Punk" in res.data
    assert b"Discovery" in res.data
    assert b"audioElement" in res.data
