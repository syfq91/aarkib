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

    resp = client.get(f"/api/media/{track_id}/playback")
    assert resp.status_code == 200
    pb = resp.get_json()["playback"]
    assert pb["media_id"] == track_id
    assert pb["stream_url"] == f"/api/media/{track_id}/stream"
    assert pb["album"] == "The Wall"
    assert pb["track_number"] == 6
    assert pb["duration"] == 382.0


def test_playback_api_not_found(client):
    """Test /api/media/<id>/playback for non-existent item."""
    resp = client.get("/api/media/999999/playback")
    assert resp.status_code == 404
