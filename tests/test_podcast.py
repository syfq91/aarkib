from __future__ import annotations

from pathlib import Path

from aarkib.extensions import db
from aarkib.models import Collection, MediaItem, MediaType
from aarkib.plugins import init_plugins, plugin_registry
from aarkib.services.parsers.podcast import parse_podcast_filename
from aarkib.services.search import MEDIA_GROUPS, search_grouped


def test_parse_podcast_filename():
    # Season & Episode
    r1 = parse_podcast_filename(Path("/podcasts/Show Name - S02E14 - Grand Finale.mp3"))
    assert r1["show"] == "Show Name"
    assert r1["season"] == 2
    assert r1["episode"] == 14
    assert r1["title"] == "Grand Finale"

    # Ep Number
    r2 = parse_podcast_filename(
        Path("/podcasts/The Daily - Ep 105 - Breaking News.mp3")
    )
    assert r2["show"] == "The Daily"
    assert r2["episode"] == 105
    assert r2["title"] == "Breaking News"

    # Date-based podcast
    r3 = parse_podcast_filename(
        Path("/podcasts/Up First - 2026-09-12 - Morning Briefing.mp3")
    )
    assert r3["show"] == "Up First"
    assert r3["publication_date"] == "2026-09-12"
    assert r3["title"] == "Morning Briefing"

    # Parent folder fallback
    r4 = parse_podcast_filename(Path("/media/podcasts/Hardcore History/Blueprint.mp3"))
    assert r4["show"] == "Hardcore History"
    assert r4["title"] == "Blueprint"


def test_podcast_media_plugin_registration(app):
    init_plugins(app)
    plugin = plugin_registry.get_plugin("podcast")
    assert plugin is not None
    assert plugin.media_type == "podcast"
    assert ".mp3" in plugin.supported_extensions

    by_type = plugin_registry.get_plugin_for_media_type("podcast")
    assert by_type is plugin

    assert plugin.get_player_url(42, "mp3") == "/reader/podcast/42"


def test_media_item_podcast_properties(app):
    with app.app_context():
        item = MediaItem(
            title="Episode 1: The Beginning",
            original_file_path="/media/podcasts/Show/Ep1.mp3",
            file_format="mp3",
            file_hash="hash_pod_1",
            media_type=MediaType.PODCAST.value,
            episode=1,
            season=1,
            podcast_feed_url="https://example.com/feed.xml",
            podcast_guid="guid-12345",
        )
        assert item.is_podcast is True
        assert item.is_audio is True
        assert item.is_book is False
        assert item.creators_display == "Unknown Host"
        assert item.player_url == f"/reader/podcast/{item.id}"
        assert item.episode_code == "S01E01"


def test_podcast_player_view(app, client):
    with app.app_context():
        show = Collection(name="Tech Talk Daily")
        db.session.add(show)
        db.session.flush()

        ep1 = MediaItem(
            title="Ep 1: Intro",
            original_file_path="/media/podcasts/Tech Talk Daily/ep1.mp3",
            file_format="mp3",
            file_hash="hash_ttd_1",
            media_type="podcast",
            collection_id=show.id,
            series_index=1.0,
            episode=1,
        )
        ep2 = MediaItem(
            title="Ep 2: Deep Dive",
            original_file_path="/media/podcasts/Tech Talk Daily/ep2.mp3",
            file_format="mp3",
            file_hash="hash_ttd_2",
            media_type="podcast",
            collection_id=show.id,
            series_index=2.0,
            episode=2,
        )
        db.session.add_all([ep1, ep2])
        db.session.commit()
        ep1_id = ep1.id
        ep2_id = ep2.id

    resp = client.get(f"/reader/podcast/{ep1_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Tech Talk Daily" in html
    assert "Ep 1: Intro" in html
    assert f"/reader/podcast/{ep2_id}" in html


def test_search_grouped_includes_podcast(app):
    # Verify MEDIA_GROUPS has podcast
    podcast_group = next((g for g in MEDIA_GROUPS if g["key"] == "podcast"), None)
    assert podcast_group is not None
    assert podcast_group["label"] == "Podcasts & Shows"

    with app.app_context():
        ep = MediaItem(
            title="Radiolab: Dark Forest Phenomenon",
            original_file_path="/media/podcasts/Radiolab/DarkForest.mp3",
            file_format="mp3",
            file_hash="hash_rl_dark",
            media_type="podcast",
        )
        db.session.add(ep)
        db.session.commit()

        from aarkib.services.search import sync_media_item_fts

        sync_media_item_fts(ep.id, session=db.session)

        grouped = search_grouped("Radiolab", session=db.session)
        assert "podcast" in grouped["groups"]
        assert grouped["groups"]["podcast"]["count"] >= 1
        assert any(
            item["title"] == ep.title for item in grouped["groups"]["podcast"]["items"]
        )
