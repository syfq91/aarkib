from __future__ import annotations

from unittest.mock import MagicMock

from aarkib.services.metadata.providers.comicvine import ComicVineProvider
from aarkib.services.metadata.registry import MetadataProviderRegistry


def test_comicvine_no_api_key():
    """Test ComicVineProvider without an API key gracefully returns empty list."""
    provider = ComicVineProvider(api_key=None)
    results = provider.search("Batman Year One")
    assert results == []


def test_comicvine_mocked_search():
    """Test ComicVineProvider search result parsing with mocked HTTP responses."""
    mock_client = MagicMock()
    mock_client.get_json.return_value = {
        "status_code": 1,
        "results": [
            {
                "id": 12345,
                "name": "Year One",
                "volume": {"id": 100, "name": "Batman"},
                "issue_number": "404",
                "cover_date": "1987-02-01",
                "deck": "The beginning of the Dark Knight's crusade.",
                "image": {"medium_url": "https://example.com/batman404.jpg"},
                "resource_type": "issue",
            }
        ],
    }

    provider = ComicVineProvider(api_key="dummy_key", client=mock_client)
    results = provider.search("Batman Year One")
    assert len(results) == 1
    res = results[0]
    assert res.title == "Batman #404: Year One"
    assert res.year == "1987"
    assert res.poster_url == "https://example.com/batman404.jpg"
    assert res.overview == "The beginning of the Dark Knight's crusade."
    assert res.id == "issue/12345"
    assert res.provider == "comicvine"


def test_comicvine_mocked_details():
    """Test ComicVineProvider fetch_details parsing with mocked HTTP responses."""
    mock_client = MagicMock()
    mock_client.get_json.return_value = {
        "status_code": 1,
        "results": {
            "id": 12345,
            "name": "Year One Part 1",
            "volume": {
                "id": 100,
                "name": "Batman",
                "publisher": {"name": "DC Comics"},
            },
            "issue_number": "404",
            "cover_date": "1987-02-01",
            "description": "<p>James Gordon arrives in Gotham City.</p>",
            "image": {"super_url": "https://example.com/batman404_super.jpg"},
            "person_credits": [
                {"name": "Frank Miller", "role": "writer"},
                {"name": "David Mazzucchelli", "role": "artist"},
            ],
        },
    }

    provider = ComicVineProvider(api_key="dummy_key", client=mock_client)
    details = provider.fetch_details("issue/12345")
    assert details is not None
    assert details.title == "Batman #404: Year One Part 1"
    assert details.series == "Batman"
    assert details.series_index == 404.0
    assert details.publisher == "DC Comics"
    assert details.release_date == "1987-02-01"
    assert "Frank Miller" in details.creators
    assert "David Mazzucchelli" in details.creators
    assert details.overview == "James Gordon arrives in Gotham City."
    assert details.poster_url == "https://example.com/batman404_super.jpg"


def test_registry_comicvine_integration():
    """Test MetadataProviderRegistry includes comicvine in registry and waterfall."""
    registry = MetadataProviderRegistry()
    assert "comicvine" in registry.list_providers()
    comic_providers = registry.DEFAULT_WATERFALLS.get("comic", [])
    assert "comicvine" in comic_providers
