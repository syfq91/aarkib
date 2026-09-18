from __future__ import annotations

from aarkib.extensions import db
from aarkib.models import MediaItem, UserProgress


def test_playback_api_book(app, client):
    """Test /api/media/<id>/playback for an EPUB book item."""
    with app.app_context():
        book = MediaItem(
            title="Dune",
            original_file_path="/media/dune.epub",
            file_format="epub",
            file_hash="hash123epub",
            media_type="book",
            page_count=600,
        )
        db.session.add(book)
        db.session.commit()
        book_id = book.id

    resp = client.get(f"/api/media/{book_id}/playback")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    pb = data["playback"]
    assert pb["media_id"] == book_id
    assert pb["title"] == "Dune"
    assert pb["media_type"] == "book"
    assert pb["playback_strategy"] == "read"
    assert pb["reader_url"] == f"/reader/epub/{book_id}"
    assert pb["page_count"] == 600
    assert "plan" in pb
    assert pb["plan"]["mode"] == "direct"
    assert pb["plan"]["container"] == "epub"


def test_playback_api_video_with_progress(app, client, default_user):
    """Test /api/media/<id>/playback for a video item with user progress."""
    with app.app_context():
        video = MediaItem(
            title="Dune (2021)",
            original_file_path="/media/dune.mp4",
            file_format="mp4",
            file_hash="hash123mp4",
            media_type="video",
            duration=9300.0,
            codec="h264",
        )
        db.session.add(video)
        db.session.flush()

        progress = UserProgress(
            user_id=default_user,
            media_item_id=video.id,
            progress_location="2540.5",
            percentage=27.3,
        )
        db.session.add(progress)
        db.session.commit()
        video_id = video.id

    resp = client.get(f"/api/media/{video_id}/playback")
    assert resp.status_code == 200
    pb = resp.get_json()["playback"]
    assert pb["media_id"] == video_id
    assert pb["stream_url"] == f"/api/media/{video_id}/stream"
    assert pb["hls_url"] == f"/api/media/{video_id}/hls/master.m3u8"
    assert pb["resume_position"] == 2540.5
    assert pb["progress_percentage"] == 27.3
    assert pb["duration"] == 9300.0
    assert pb["codec"] == "h264"
    assert "plan" in pb
    assert "playback_plan" in pb
    assert pb["plan"]["mode"] in ("direct", "remux", "transcode")


def test_playback_api_audio(app, client):
    """Test /api/media/<id>/playback for a music track item."""
    with app.app_context():
        track = MediaItem(
            title="Comfortably Numb",
            original_file_path="/media/pink_floyd.flac",
            file_format="flac",
            file_hash="hash123flac",
            media_type="music",
            duration=382.0,
            album="The Wall",
            track_number=6,
        )
        db.session.add(track)
        db.session.commit()
        track_id = track.id

    # Baseline unknown client transcode to MP3
    resp = client.get(f"/api/media/{track_id}/playback")
    assert resp.status_code == 200
    pb = resp.get_json()["playback"]
    assert pb["media_id"] == track_id
    assert pb["stream_url"] == f"/api/media/{track_id}/stream"
    assert pb["album"] == "The Wall"
    assert pb["track_number"] == 6
    assert pb["duration"] == 382.0
    assert "plan" in pb
    assert pb["plan"]["mode"] == "transcode"
    assert pb["plan"]["container"] == "mp3"

    # Chromium browser with FLAC support -> direct play
    chrome_ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    resp_chrome = client.get(
        f"/api/media/{track_id}/playback",
        headers={"User-Agent": chrome_ua},
    )
    assert resp_chrome.status_code == 200
    pb_chrome = resp_chrome.get_json()["playback"]
    assert pb_chrome["plan"]["mode"] == "direct"
    assert pb_chrome["plan"]["container"] == "flac"


def test_playback_api_not_found(client):
    """Test /api/media/<id>/playback for non-existent item."""
    resp = client.get("/api/media/999999/playback")
    assert resp.status_code == 404


def test_stream_info_endpoint_with_plan(app, client, tmp_path):
    """Test /api/media/<id>/stream/info includes both legacy evaluation and new plan."""
    from aarkib.models import Library

    media_file = tmp_path / "stream_video.mp4"
    media_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"V" * 512)

    with app.app_context():
        lib = Library(
            name="StreamLib",
            slug="stream-lib",
            path=str(tmp_path),
            media_type="video",
        )
        db.session.add(lib)
        db.session.flush()

        item = MediaItem(
            title="Stream Test",
            original_file_path=str(media_file),
            file_format="mp4",
            file_hash="hash_stream_test",
            file_size=len(media_file.read_bytes()),
            duration=120.0,
            codec="h264",
            library_id=lib.id,
            media_type="video",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    resp = client.get(f"/api/media/{item_id}/stream/info")
    assert resp.status_code == 200
    data = resp.get_json()

    assert data["id"] == item_id
    assert "evaluation" in data
    assert "strategy" in data["evaluation"]
    assert "plan" in data
    assert "mode" in data["plan"]
    assert "container" in data["plan"]
    assert data["direct_url"] == f"/api/media/{item_id}/file"


def test_playback_api_capabilities_header_override(app, client):
    """Test /api/media/<id>/playback dynamically respects X-Aarkib-Capabilities header."""
    with app.app_context():
        book = MediaItem(
            title="E-Ink Test Book",
            original_file_path="/media/eink_book.epub",
            file_format="epub",
            file_hash="hash_eink_epub",
            media_type="book",
            page_count=350,
        )
        db.session.add(book)
        db.session.commit()
        book_id = book.id

    # Request with E-Ink client capability override
    resp = client.get(
        f"/api/media/{book_id}/playback",
        headers={"X-Aarkib-Capabilities": '{"device": {"is_eink": true}}'},
    )
    assert resp.status_code == 200
    pb = resp.get_json()["playback"]
    assert pb["plan"]["mode"] == "optimize"
    assert pb["plan"]["stream_url"] == f"/api/media/{book_id}/optimized"
