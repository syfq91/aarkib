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
    assert ".m4a" in plugin.supported_extensions
    assert ".m4b" in plugin.supported_extensions
    assert ".flac" in plugin.supported_extensions
    assert ".wav" in plugin.supported_extensions
    assert plugin.get_player_url(55, "m4b") == "/reader/audio/55"

    health = plugin.check_health()
    assert health["status"] == "ok"
    assert health["plugin"] == "audio"
    assert ".m4b" in health["supported_extensions"]


def test_m4b_audiobook_parsing(tmp_path: Path):
    m4b_file = tmp_path / "Brandon Sanderson - Mistborn 01 - The Final Empire.m4b"
    m4b_file.write_bytes(b"\x00" * 100)

    meta = parse_audio(m4b_file)
    assert meta.media_type in ("audio", "audiobook")
    assert meta.file_format == "m4b"
    assert "Audiobook" in meta.tags
    assert "The Final Empire" in meta.title or "Mistborn" in meta.title
    item = MediaItem(
        title=meta.title,
        original_file_path=str(m4b_file),
        file_format=meta.file_format,
        file_hash="fakehashm4b",
    )
    assert item.is_audio is True
    assert item.is_audiobook is True
    assert item.player_url == f"/reader/audio/{item.id}"


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
    assert meta.media_type in ("audio", "music")
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


def test_audiobook_and_music_plugins():
    from aarkib.plugins import AudiobookMediaPlugin, MusicMediaPlugin

    ab_plugin = AudiobookMediaPlugin()
    assert ab_plugin.name == "audiobook"
    assert ab_plugin.media_type == "audiobook"
    assert ".m4b" in ab_plugin.supported_extensions
    assert ab_plugin.get_player_url(77) == "/reader/audiobook/77"

    music_plugin = MusicMediaPlugin()
    assert music_plugin.name == "music"
    assert music_plugin.media_type == "music"
    assert ".flac" in music_plugin.supported_extensions
    assert ".mp3" in music_plugin.supported_extensions
    assert music_plugin.get_player_url(88) == "/reader/music/88"


def test_mp4_chpl_pure_python_parser(tmp_path: Path):
    from aarkib.services.parsers.audio import parse_mp4_chapters

    # Construct synthetic moov -> udta -> chpl
    # mvhd: timescale 1000, duration 120000 (120s)
    mvhd_payload = b"\x00" * 12 + struct.pack(">II", 1000, 120000)
    mvhd_box = struct.pack(">I4s", len(mvhd_payload) + 8, b"mvhd") + mvhd_payload

    # chpl payload: version (1 byte), flags (3 bytes), reserved (1 byte), count (4 bytes = 2)
    # Chapter 0: 0s, "Prologue"
    # Chapter 1: 50s (500,000,000 in 100ns units), "Chapter 1"
    ch0_title = b"Prologue"
    ch0_bytes = struct.pack(">QB", 0, len(ch0_title)) + ch0_title
    ch1_title = b"Chapter 1"
    ch1_bytes = struct.pack(">QB", 500000000, len(ch1_title)) + ch1_title
    chpl_body = b"\x01\x00\x00\x00\x00" + struct.pack(">I", 2) + ch0_bytes + ch1_bytes
    chpl_box = struct.pack(">I4s", len(chpl_body) + 8, b"chpl") + chpl_body

    udta_box = struct.pack(">I4s", len(chpl_box) + 8, b"udta") + chpl_box
    moov_payload = mvhd_box + udta_box
    moov_box = struct.pack(">I4s", len(moov_payload) + 8, b"moov") + moov_payload

    ftyp_payload = b"M4B \x00\x00\x00\x00M4B mp42"
    ftyp_box = struct.pack(">I4s", len(ftyp_payload) + 8, b"ftyp") + ftyp_payload
    mp4_file = tmp_path / "book.m4b"
    mp4_file.write_bytes(ftyp_box + moov_box)

    chapters = parse_mp4_chapters(mp4_file)
    assert len(chapters) == 2
    assert chapters[0]["title"] == "Prologue"
    assert chapters[0]["start_time"] == 0.0
    assert chapters[0]["end_time"] == 50.0
    assert chapters[1]["title"] == "Chapter 1"
    assert chapters[1]["start_time"] == 50.0
    assert chapters[1]["end_time"] == 120.0


def test_id3_chap_frame_parser(tmp_path: Path):
    from aarkib.services.parsers.audio import parse_id3v2

    # Construct synthetic ID3v2.3 with CHAP frame
    # Element ID: ch1\0
    # Start: 0 ms, End: 45000 ms, start_offset: 0, end_offset: 0
    # Subframe: TIT2 with "Beginning"
    sub_payload = b"\x00" + b"Beginning"
    sub_frame = (
        b"TIT2" + struct.pack(">I", len(sub_payload)) + b"\x00\x00" + sub_payload
    )
    chap_payload = b"ch1\x00" + struct.pack(">IIII", 0, 45000, 0, 0) + sub_frame
    chap_frame = (
        b"CHAP" + struct.pack(">I", len(chap_payload)) + b"\x00\x00" + chap_payload
    )

    body_len = len(chap_frame)
    s0 = (body_len >> 21) & 0x7F
    s1 = (body_len >> 14) & 0x7F
    s2 = (body_len >> 7) & 0x7F
    s3 = body_len & 0x7F
    header = b"ID3\x03\x00\x00" + bytes([s0, s1, s2, s3])

    mp3_file = tmp_path / "chapter_test.mp3"
    mp3_file.write_bytes(header + chap_frame + b"\xff\xfb\x90\x64" + b"\x00" * 100)

    res = parse_id3v2(mp3_file)
    assert "chapters" in res
    assert len(res["chapters"]) == 1
    assert res["chapters"][0]["title"] == "Beginning"
    assert res["chapters"][0]["start_time"] == 0.0
    assert res["chapters"][0]["end_time"] == 45.0


def test_audiobook_web_player_route(client, app):
    with app.app_context():
        author = Creator(name="Brandon Sanderson")
        book = MediaItem(
            title="The Way of Kings",
            original_file_path="/tmp/wayofkings.m4b",
            file_format="m4b",
            file_hash="hash_m4b_kings",
            media_type=MediaType.AUDIOBOOK.value,
            duration=3600.0,
            author="Brandon Sanderson",
            narrator="Michael Kramer, Kate Reading",
            chapters_json='[{"id": 0, "title": "Prelude to the Stormlight Archive", "start_time": 0.0, "end_time": 600.0}, {"id": 1, "title": "Prologue: To Kill", "start_time": 600.0, "end_time": 1800.0}]',
        )
        book.creators.append(author)
        db.session.add_all([author, book])
        db.session.commit()
        book_id = book.id

    # Test dedicated /reader/audiobook/<id> route
    res = client.get(f"/reader/audiobook/{book_id}")
    assert res.status_code == 200
    assert b"The Way of Kings" in res.data
    assert b"Brandon Sanderson" in res.data
    assert b"Michael Kramer, Kate Reading" in res.data
    assert b"Prelude to the Stormlight Archive" in res.data
    assert b"drawerOverlay" in res.data
    assert b"speedMenuBtn" in res.data
    assert b"sleepMenuBtn" in res.data

    # Test that /reader/audio/<id> automatically dispatches to audiobook player
    res_audio = client.get(f"/reader/audio/{book_id}")
    assert res_audio.status_code == 200
    assert b"Prelude to the Stormlight Archive" in res_audio.data
