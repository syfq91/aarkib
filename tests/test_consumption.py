from __future__ import annotations

from aarkib.extensions import db
from aarkib.models import MediaItem


def test_enriched_consumption_progress_lifecycle(app, client, default_user):
    """Test setting and retrieving enriched consumption progress (speed, duration, position)."""
    with app.app_context():
        item = MediaItem(
            title="The Way of Kings",
            original_file_path="/media/wok.m4b",
            file_format="m4b",
            file_hash="hash_wok_123",
            media_type="audiobook",
            duration=162000.0,
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    # POST enriched consumption progress
    post_data = {
        "location": "Chapter 12 - 43:21",
        "position_seconds": 2601.5,
        "duration": 162000.0,
        "playback_speed": 1.25,
        "playback_type": "audiobook",
        "percentage": 1.6,
        "is_completed": False,
    }
    resp = client.post(f"/api/media/{item_id}/progress", json=post_data)
    assert resp.status_code == 200
    res = resp.get_json()
    assert res["status"] == "ok"
    assert res["position_seconds"] == 2601.5
    assert res["duration"] == 162000.0
    assert res["playback_speed"] == 1.25
    assert res["playback_type"] == "audiobook"
    assert res["location"] == "Chapter 12 - 43:21"

    # GET progress
    get_resp = client.get(f"/api/media/{item_id}/progress")
    assert get_resp.status_code == 200
    get_data = get_resp.get_json()
    assert get_data["position_seconds"] == 2601.5
    assert get_data["playback_speed"] == 1.25
    assert get_data["playback_type"] == "audiobook"
    assert get_data["duration"] == 162000.0

    # GET playback descriptor reflects enriched progress
    pb_resp = client.get(f"/api/media/{item_id}/playback")
    assert pb_resp.status_code == 200
    pb = pb_resp.get_json()["playback"]
    assert pb["resume_position"] == 2601.5
    assert pb["playback_speed"] == 1.25
    assert pb["playback_type"] == "audiobook"


def test_consumption_numeric_location_fallback(app, client, default_user):
    """Test that submitting a numeric location string automatically populates position_seconds."""
    with app.app_context():
        item = MediaItem(
            title="Podcast Episode #10",
            original_file_path="/media/ep10.mp3",
            file_format="mp3",
            file_hash="hash_ep10",
            media_type="podcast",
            duration=3600.0,
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    # Post with legacy format (location as seconds string)
    resp = client.post(
        f"/api/media/{item_id}/progress",
        json={"location": "1250.75", "percentage": 34.7},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["position_seconds"] == 1250.75
    assert data["duration"] == 3600.0
