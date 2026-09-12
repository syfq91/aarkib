from __future__ import annotations

import io

import pytest

from aarkib.extensions import db
from aarkib.models import Collection, MediaItem, User
from aarkib.services.opml import import_opml_channels, parse_opml

SAMPLE_OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head>
    <title>My Podcast Subscriptions</title>
  </head>
  <body>
    <outline text="Technology">
      <outline text="The Changelog" title="The Changelog" type="rss"
               xmlUrl="https://changelog.com/podcast/feed"
               htmlUrl="https://changelog.com"
               description="Conversations with the hackers, leaders, and innovators." />
      <outline text="Software Engineering Daily" title="Software Engineering Daily" type="rss"
               xmlUrl="https://softwareengineeringdaily.com/feed/podcast"
               htmlUrl="https://softwareengineeringdaily.com"
               description="Technical interviews about software topics." />
    </outline>
    <outline text="Hardcore History" title="Hardcore History" type="rss"
             xmlUrl="https://feeds.feedburner.com/dancarlin/history"
             htmlUrl="https://dancarlin.com"
             description="Dan Carlin explores deep historical themes." />
  </body>
</opml>
"""


def test_parse_opml_valid():
    feeds = parse_opml(SAMPLE_OPML)
    assert len(feeds) == 3

    titles = [f["title"] for f in feeds]
    assert "The Changelog" in titles
    assert "Software Engineering Daily" in titles
    assert "Hardcore History" in titles

    changelog = next(f for f in feeds if f["title"] == "The Changelog")
    assert changelog["xml_url"] == "https://changelog.com/podcast/feed"
    assert changelog["category"] == "Technology"
    assert changelog["html_url"] == "https://changelog.com"
    assert "Conversations" in changelog["description"]

    hh = next(f for f in feeds if f["title"] == "Hardcore History")
    assert hh["category"] is None
    assert hh["xml_url"] == "https://feeds.feedburner.com/dancarlin/history"


def test_parse_opml_empty_and_invalid():
    assert parse_opml("") == []
    assert parse_opml("   ") == []

    with pytest.raises(ValueError, match="Malformed or invalid OPML XML"):
        parse_opml("<opml><head></opml>")


def test_import_opml_channels(app):
    feeds = parse_opml(SAMPLE_OPML)
    with app.app_context():
        # Create an unassigned podcast episode that matches "Hardcore History"
        ep = MediaItem(
            title="Show 68 - Supernova in the East VI",
            original_file_path="/media/podcasts/Hardcore History/Show 68.mp3",
            file_format="mp3",
            file_hash="hash_hh_68",
            media_type="podcast",
            album="Hardcore History",
        )
        db.session.add(ep)
        db.session.commit()

        result = import_opml_channels(feeds, session=db.session)
        assert result["total_feeds"] == 3
        assert result["created_shows"] == 3
        assert result["matched_episodes"] >= 1

        # Check that Hardcore History collection was created and linked to ep
        col = db.session.query(Collection).filter_by(name="Hardcore History").first()
        assert col is not None
        assert ep.collection_id == col.id
        assert ep.podcast_feed_url == "https://feeds.feedburner.com/dancarlin/history"

        # Re-importing should detect existing shows
        result2 = import_opml_channels(feeds, session=db.session)
        assert result2["created_shows"] == 0
        assert result2["existing_shows"] == 3


def test_opml_import_api_endpoint(app, client):
    with app.app_context():
        admin = User(username="admin_opml", is_admin=True)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "admin_opml", "password": "adminpass"},
        follow_redirects=True,
    )

    data = {
        "file": (io.BytesIO(SAMPLE_OPML.encode("utf-8")), "subscriptions.opml"),
    }
    resp = client.post(
        "/api/podcasts/opml/import",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    json_data = resp.get_json()
    assert json_data["status"] == "success"
    assert json_data["data"]["total_feeds"] == 3
