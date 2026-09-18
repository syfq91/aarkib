from __future__ import annotations

import struct
from pathlib import Path

from aarkib.plugins import init_plugins, plugin_registry
from aarkib.plugins.video import VideoMediaPlugin
from aarkib.services.indexer import index_media_file
from aarkib.services.parsers.video import (
    extract_video_cover,
    parse_video_filename,
    read_mp4_metadata,
)


def create_synthetic_mp4(
    file_path: Path,
    duration_sec: int = 120,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Creates a minimal synthetic MP4 file with valid ftyp, moov, mvhd, and tkhd boxes."""
    ftyp_payload = b"ftypisom" + struct.pack(">I", 512) + b"isomiso2mp41"
    ftyp_box = struct.pack(">I", len(ftyp_payload) + 4) + ftyp_payload

    # mvhd: timescale 1000, duration = duration_sec * 1000
    timescale = 1000
    duration = duration_sec * timescale
    mvhd_payload = (
        struct.pack(">BBBB", 0, 0, 0, 0)
        + struct.pack(">IIII", 0, 0, timescale, duration)
        + b"\x00" * 80
    )
    mvhd_box = struct.pack(">I", len(mvhd_payload) + 8) + b"mvhd" + mvhd_payload

    # tkhd: width and height in fixed-point 16.16
    tkhd_payload = (
        struct.pack(">BBBB", 0, 0, 0, 0)
        + struct.pack(">IIII", 0, 0, 1, 0)
        + struct.pack(">I", duration)
        + b"\x00" * 8
        + struct.pack(">hhhh", 0, 0, 0, 0)
        + b"\x00" * 36
        + struct.pack(">II", width << 16, height << 16)
    )
    tkhd_box = struct.pack(">I", len(tkhd_payload) + 8) + b"tkhd" + tkhd_payload
    trak_box = struct.pack(">I", len(tkhd_box) + 8) + b"trak" + tkhd_box

    moov_payload = mvhd_box + trak_box
    moov_box = struct.pack(">I", len(moov_payload) + 8) + b"moov" + moov_payload

    file_path.write_bytes(ftyp_box + moov_box)
    return file_path


def test_parse_video_filename_tv_shows():
    # Standard SxxExx
    res = parse_video_filename(
        Path("Breaking.Bad.S01E02.Cats.in.the.Bag.1080p.WEBRip.x264.mp4")
    )
    assert res["series"] == "Breaking Bad"
    assert res["season"] == 1
    assert res["episode"] == 2
    assert res["series_index"] == 1.02
    assert res["title"] == "Cats In The Bag"
    assert "TV Show" in res["tags"]
    assert "1080p" in res["tags"]

    # Short episode without name
    res2 = parse_video_filename(Path("The.Wire.S03E05.mp4"))
    assert res2["series"] == "The Wire"
    assert res2["season"] == 3
    assert res2["episode"] == 5
    assert res2["series_index"] == 3.05
    assert res2["title"] == "Season 3, Episode 5"

    # NxN notation
    res3 = parse_video_filename(
        Path("Game of Thrones - 01x05 - The Wolf and the Lion.mkv")
    )
    assert res3["series"] == "Game Of Thrones"
    assert res3["season"] == 1
    assert res3["episode"] == 5
    assert res3["title"] == "The Wolf And The Lion"

    # Spelled out Season X Episode Y
    res4 = parse_video_filename(
        Path("Rick and Morty - Season 2 Episode 3 - Auto Erotic Assimilation.mp4")
    )
    assert res4["series"] == "Rick And Morty"
    assert res4["season"] == 2
    assert res4["episode"] == 3
    assert res4["title"] == "Auto Erotic Assimilation"


def test_parse_video_filename_movies():
    res = parse_video_filename(Path("Inception (2010) [1080p].mp4"))
    assert res["title"] == "Inception"
    assert res["publication_date"] == "2010"
    assert "Movie" in res["tags"]
    assert "1080p" in res["tags"]

    res2 = parse_video_filename(Path("The.Matrix.1999.2160p.UHD.BluRay.x265.mkv"))
    assert res2["title"] == "The Matrix"
    assert res2["publication_date"] == "1999"
    assert "4K UHD" in res2["tags"]

    # Generic video
    res3 = parse_video_filename(Path("Home_Video_Vacation.mp4"))
    assert res3["title"] == "Home Video Vacation"
    assert "Video" in res3["tags"]


def test_read_mp4_metadata_synthetic(tmp_path):
    mp4_file = tmp_path / "sample.mp4"
    create_synthetic_mp4(mp4_file, duration_sec=180, width=3840, height=2160)

    meta = read_mp4_metadata(mp4_file)
    assert meta.get("duration") == 180.0
    assert meta.get("resolution_width") == 3840
    assert meta.get("resolution_height") == 2160


def test_extract_video_cover_local_poster(tmp_path):
    video_file = tmp_path / "Interstellar (2014).mp4"
    video_file.write_bytes(b"dummy video data")

    poster_file = tmp_path / "Interstellar (2014)-poster.jpg"
    poster_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 200
    poster_file.write_bytes(poster_bytes)

    cover = extract_video_cover(video_file)
    assert cover is not None
    assert cover == poster_bytes


def test_video_plugin_registration():
    init_plugins()
    plugin = plugin_registry.get_plugin("video")
    assert plugin is not None
    assert isinstance(plugin, VideoMediaPlugin)
    assert plugin.media_type == "video"
    assert ".mp4" in plugin.supported_extensions
    assert ".mkv" in plugin.supported_extensions
    assert ".webm" in plugin.supported_extensions
    assert plugin.get_player_url(99) == "/reader/video/99"

    health = plugin.check_health()
    assert health["status"] == "ok"
    assert health["media_type"] == "video"


def test_index_and_api_video_lifecycle(client, app, tmp_path):
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    video_path = tmp_path / "Inception (2010) [1080p].mp4"
    create_synthetic_mp4(video_path, duration_sec=8880, width=1920, height=1080)

    with app.app_context():
        book = index_media_file(video_path, covers_dir)
        assert book is not None
        assert book.is_video is True
        assert book.media_type == "video"
        assert book.title == "Inception"
        assert book.publication_date == "2010"
        assert book.resolution_height == 1080
        assert book.resolution_label == "1080p"
        assert book.formatted_duration == "2h 28m"
        assert book.player_url == f"/reader/video/{book.id}"
        book_id = book.id

    # Test GET /api/media with media_type=video filter
    res = client.get("/api/media?media_type=video")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["items"]) >= 1
    found = next((b for b in data["items"] if b["id"] == book_id), None)
    assert found is not None
    assert found["media_type"] == "video"
    assert found["resolution_height"] == 1080

    # Test GET /api/media/<id>
    res = client.get(f"/api/media/{book_id}")
    assert res.status_code == 200
    detail = res.get_json()
    assert detail["title"] == "Inception"
    assert detail["formatted_duration"] == "2h 28m"
    assert detail["player_url"] == f"/reader/video/{book_id}"

    # Test GET /api/media/<id>/cover (returns fallback SVG for video)
    res = client.get(f"/api/media/{book_id}/cover")
    assert res.status_code == 200
    assert "image/svg+xml" in res.content_type
    assert b"AARKIB VIDEO" in res.data

    # Test GET /api/media/<id>/file with byte range request (HTTP 206 Partial Content)
    res = client.get(
        f"/api/media/{book_id}/file",
        headers={"Range": "bytes=0-100"},
    )
    assert res.status_code == 206
    assert "bytes 0-100/" in res.headers.get("Content-Range", "")
    assert res.content_type == "video/mp4"

    # Test GET /media/<id> detail page rendering (before progress)
    res = client.get(f"/media/{book_id}")
    assert res.status_code == 200
    assert b"Watch Video" in res.data

    # Test saving video playback progress
    res = client.post(
        f"/api/media/{book_id}/progress",
        json={"location": "1250.5", "percentage": 14.1},
    )
    assert res.status_code == 200

    res = client.get(f"/api/media/{book_id}/progress")
    assert res.status_code == 200
    assert res.get_json()["location"] == "1250.5"

    # Test GET /media/<id> detail page rendering (after progress -> Resume Watching)
    res = client.get(f"/media/{book_id}")
    assert res.status_code == 200
    assert b"Resume Watching" in res.data
    assert b"14%" in res.data

    # Test GET /reader/video/<id>
    res = client.get(f"/reader/video/{book_id}")
    assert res.status_code == 200
    assert b"Aarkib Video Player" in res.data
    assert b"video-player" in res.data


def test_tv_show_episode_navigation(client, app, tmp_path):
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    ep1_path = tmp_path / "Stranger Things - S01E01 - Chapter One.mp4"
    ep2_path = tmp_path / "Stranger Things - S01E02 - Chapter Two.mp4"
    create_synthetic_mp4(ep1_path, duration_sec=3000)
    create_synthetic_mp4(ep2_path, duration_sec=3200)

    with app.app_context():
        b1 = index_media_file(ep1_path, covers_dir)
        b2 = index_media_file(ep2_path, covers_dir)
        assert b1 is not None and b2 is not None
        assert b1.collection.name == "Stranger Things"
        assert b2.collection.name == "Stranger Things"
        assert b1.episode_code == "S01E01"
        assert b2.episode_code == "S01E02"
        b1_id, b2_id = b1.id, b2.id

    # Verify next episode on ep 1
    res1 = client.get(f"/reader/video/{b1_id}")
    assert res1.status_code == 200
    assert f"/reader/video/{b2_id}".encode() in res1.data

    # Verify prev episode on ep 2
    res2 = client.get(f"/reader/video/{b2_id}")
    assert res2.status_code == 200
    assert f"/reader/video/{b1_id}".encode() in res2.data
