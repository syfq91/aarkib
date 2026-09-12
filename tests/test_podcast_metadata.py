from __future__ import annotations

from unittest.mock import MagicMock

from aarkib.services.metadata.providers.itunes import iTunesPodcastProvider
from aarkib.services.metadata.providers.podcastindex import PodcastIndexProvider
from aarkib.services.metadata.registry import metadata_registry

MOCK_ITUNES_SEARCH = {
    "resultCount": 1,
    "results": [
        {
            "collectionId": 123456789,
            "collectionName": "Dan Carlin's Hardcore History",
            "artistName": "Dan Carlin",
            "feedUrl": "https://feeds.feedburner.com/dancarlin/history",
            "artworkUrl600": "https://example.com/artwork600.jpg",
            "releaseDate": "2026-08-01T12:00:00Z",
            "genres": ["History", "Society & Culture"],
            "trackCount": 70,
        }
    ],
}

MOCK_ITUNES_LOOKUP = {
    "resultCount": 1,
    "results": [
        {
            "collectionId": 123456789,
            "collectionName": "Dan Carlin's Hardcore History",
            "artistName": "Dan Carlin",
            "feedUrl": "https://feeds.feedburner.com/dancarlin/history",
            "artworkUrl600": "https://example.com/artwork600.jpg",
            "releaseDate": "2026-08-01T12:00:00Z",
            "genres": ["History", "Society & Culture"],
            "trackCount": 70,
        }
    ],
}

MOCK_PODCASTINDEX_SEARCH = {
    "status": "true",
    "feeds": [
        {
            "id": 98765,
            "title": "Software Engineering Daily",
            "author": "SE Daily Team",
            "url": "https://softwareengineeringdaily.com/feed/podcast",
            "artwork": "https://example.com/se_artwork.png",
            "description": "Technical software interviews.",
            "categories": {"1": "Technology", "2": "Podcasting"},
        }
    ],
}


def test_itunes_provider_search_and_lookup():
    mock_client = MagicMock()
    mock_client.get_json.side_effect = [MOCK_ITUNES_SEARCH, MOCK_ITUNES_LOOKUP]

    provider = iTunesPodcastProvider(client=mock_client)
    assert provider.name == "itunes"

    results = provider.search("Hardcore History", media_type="podcast")
    assert len(results) == 1
    res = results[0]
    assert res.id == "123456789"
    assert res.title == "Dan Carlin's Hardcore History"
    assert "Dan Carlin" in res.creators
    assert res.poster_url == "https://example.com/artwork600.jpg"
    assert res.score > 0

    details = provider.fetch_details("123456789", media_type="podcast")
    assert details is not None
    assert details.title == "Dan Carlin's Hardcore History"
    assert details.publisher == "Dan Carlin"
    assert details.poster_url == "https://example.com/artwork600.jpg"
    assert "History" in details.genres


def test_podcastindex_provider_configuration_and_auth():
    # Unconfigured
    unconf = PodcastIndexProvider(api_key="", api_secret="")
    assert unconf.is_configured() is False
    assert unconf.search("Test") == []
    assert unconf.fetch_details("123") is None

    # Configured
    conf = PodcastIndexProvider(api_key="TEST_KEY", api_secret="TEST_SECRET")
    assert conf.is_configured() is True
    headers = conf._auth_headers()
    assert headers["X-Auth-Key"] == "TEST_KEY"
    assert "X-Auth-Date" in headers
    assert "Authorization" in headers
    assert len(headers["Authorization"]) == 40  # SHA1 hex is 40 chars

    # Mock API call
    mock_client = MagicMock()
    mock_client.get_json.return_value = MOCK_PODCASTINDEX_SEARCH
    conf.client = mock_client

    results = conf.search("Software Engineering Daily")
    assert len(results) == 1
    assert results[0].title == "Software Engineering Daily"
    assert results[0].poster_url == "https://example.com/se_artwork.png"


def test_metadata_registry_podcast_waterfall():
    assert metadata_registry.get_provider("itunes") is not None
    assert metadata_registry.get_provider("podcastindex") is not None
    assert "itunes" in metadata_registry.DEFAULT_WATERFALLS["podcast"]
    assert "podcastindex" in metadata_registry.DEFAULT_WATERFALLS["podcast"]
