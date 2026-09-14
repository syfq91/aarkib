"""Tests for Gamepad & Controller navigation support."""

from aarkib.extensions import db
from aarkib.models import MediaItem


def test_gamepad_static_asset_served(client):
    """Verify that /static/js/gamepad.js is served properly with correct JS headers and exports."""
    res = client.get("/static/js/gamepad.js")
    assert res.status_code == 200
    assert "javascript" in res.content_type.lower()
    text = res.get_data(as_text=True)
    assert "AarkibGamepad" in text
    assert "spatialNavigate" in text
    assert "setContext" in text
    assert "updateHUD" in text
    assert "BTN" in text
    assert "AXIS" in text


def test_service_worker_precaches_gamepad(client):
    """Verify that service worker sw.js precaches gamepad.js and uses cache version v2."""
    res = client.get("/sw.js")
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    assert "/static/js/gamepad.js" in text
    assert "aarkib-v2" in text


def test_base_template_includes_gamepad(client):
    """Verify that standard HTML pages rendered via base.html include gamepad.js."""
    res = client.get("/")
    assert res.status_code in (200, 302)
    if res.status_code == 200:
        html = res.get_data(as_text=True)
        assert "/static/js/gamepad.js" in html
        assert "app.css?v=7" in html


def test_css_contains_gamepad_styles(client):
    """Verify that app.css includes gamepad spatial focus rules and HUD styling."""
    res = client.get("/static/css/app.css")
    assert res.status_code == 200
    css = res.get_data(as_text=True)
    assert ".gamepad-active" in css
    assert ".gamepad-hud" in css
    assert ".gamepad-prompt" in css
    assert ".gamepad-keycap" in css


def test_reader_templates_include_gamepad(client, app, tmp_path):
    """Verify that comic, epub, pdf, video, and audio player templates include gamepad.js."""
    with app.app_context():
        # Ensure media directory exists
        (tmp_path / "media").mkdir(parents=True, exist_ok=True)

        # Create dummy files
        cbz_file = tmp_path / "media" / "comic.cbz"
        epub_file = tmp_path / "media" / "book.epub"
        pdf_file = tmp_path / "media" / "doc.pdf"
        video_file = tmp_path / "media" / "video.mp4"
        audio_file = tmp_path / "media" / "track.mp3"

        for p in [cbz_file, epub_file, pdf_file, video_file, audio_file]:
            p.write_bytes(b"dummy media content")

        cbz_item = MediaItem(
            title="Test Comic",
            original_file_path=str(cbz_file),
            file_format="cbz",
            media_type="comic",
            file_size=1024,
            file_hash="hash_cbz_1",
        )
        epub_item = MediaItem(
            title="Test Book",
            original_file_path=str(epub_file),
            file_format="epub",
            media_type="book",
            file_size=1024,
            file_hash="hash_epub_1",
        )
        pdf_item = MediaItem(
            title="Test Document",
            original_file_path=str(pdf_file),
            file_format="pdf",
            media_type="book",
            file_size=1024,
            file_hash="hash_pdf_1",
            page_count=10,
        )
        video_item = MediaItem(
            title="Test Video",
            original_file_path=str(video_file),
            file_format="mp4",
            media_type="video",
            file_size=1024,
            file_hash="hash_video_1",
        )
        audio_item = MediaItem(
            title="Test Audio",
            original_file_path=str(audio_file),
            file_format="mp3",
            media_type="audio",
            file_size=1024,
            file_hash="hash_audio_1",
        )
        db.session.add_all([cbz_item, epub_item, pdf_item, video_item, audio_item])
        db.session.commit()

        cbz_id = cbz_item.id
        epub_id = epub_item.id
        pdf_id = pdf_item.id
        video_id = video_item.id
        audio_id = audio_item.id

    # CBZ Reader
    res = client.get(f"/reader/cbz/{cbz_id}")
    assert res.status_code == 200
    assert "/static/js/gamepad.js" in res.get_data(as_text=True)

    # EPUB Reader
    res = client.get(f"/reader/epub/{epub_id}")
    assert res.status_code == 200
    assert "/static/js/gamepad.js" in res.get_data(as_text=True)

    # PDF Reader
    res = client.get(f"/reader/pdf/{pdf_id}")
    assert res.status_code == 200
    assert "/static/js/gamepad.js" in res.get_data(as_text=True)

    # Video Player
    res = client.get(f"/reader/video/{video_id}")
    assert res.status_code == 200
    assert "/static/js/gamepad.js" in res.get_data(as_text=True)

    # Audio Player
    res = client.get(f"/reader/audio/{audio_id}")
    assert res.status_code == 200
    assert "/static/js/gamepad.js" in res.get_data(as_text=True)


def test_settings_system_has_gamepad_card(client):
    """Verify that settings/system page renders the gamepad navigation preferences card for admin users."""
    res = client.get("/settings/system")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "Gamepad &amp; 10-Foot Navigation" in html
    assert "setting-GAMEPAD_HUD_MODE" in html
    assert "setting-GAMEPAD_RUMBLE" in html
