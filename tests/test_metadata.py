from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from aarkib.extensions import db
from aarkib.models import MediaItem, MetadataCacheEntry, User
from aarkib.services.indexer import index_media_file
from aarkib.services.metadata import (
    MediaMetadataDetails,
    MetadataCacheManager,
    MetadataSearchResult,
    MusicBrainzProvider,
    ResilientHttpClient,
    TMDBProvider,
    TokenBucketRateLimiter,
    compute_confidence_score,
    is_safe_http_url,
)

# --------------------------------------------------------------------------
# 1. Rate Limiter Tests
# --------------------------------------------------------------------------


def test_token_bucket_rate_limiter_burst_and_delay():
    limiter = TokenBucketRateLimiter(rate=5.0, capacity=2.0)
    # 2 tokens available immediately
    w1 = limiter.acquire(1.0)
    assert w1 == 0.0
    w2 = limiter.acquire(1.0)
    assert w2 == 0.0

    # Next token requires waiting ~0.2s (1/5th of a second)
    start = time.monotonic()
    w3 = limiter.acquire(1.0)
    elapsed = time.monotonic() - start
    assert w3 > 0.15
    assert elapsed >= 0.15


# --------------------------------------------------------------------------
# 2. Cache Manager Tests
# --------------------------------------------------------------------------


def test_metadata_cache_manager(app):
    with app.app_context():
        # Clean any prior cache
        db.session.query(MetadataCacheEntry).delete()
        db.session.commit()

        # Cache miss
        assert (
            MetadataCacheManager.get("tmdb", "/search/movie", {"query": "Inception"})
            is None
        )

        # Cache set
        payload = {"results": [{"id": 550, "title": "Inception"}]}
        MetadataCacheManager.set(
            "tmdb", "/search/movie", {"query": "Inception"}, payload, ttl_days=10
        )

        # Cache hit
        cached = MetadataCacheManager.get(
            "tmdb", "/search/movie", {"query": "Inception"}
        )
        assert cached == payload

        # Expire cache entry manually and test prune
        entry = db.session.scalar(db.select(MetadataCacheEntry))
        assert entry is not None
        entry.expires_at = datetime.now(UTC) - timedelta(days=1)
        db.session.commit()

        # Prune
        pruned_count = MetadataCacheManager.prune_expired()
        assert pruned_count >= 1
        assert (
            MetadataCacheManager.get("tmdb", "/search/movie", {"query": "Inception"})
            is None
        )


# --------------------------------------------------------------------------
# 3. Resilient HTTP Client Tests
# --------------------------------------------------------------------------


def test_is_safe_http_url():
    assert is_safe_http_url("https://api.themoviedb.org/3") is True
    assert is_safe_http_url("http://coverartarchive.org/release/123") is True
    assert is_safe_http_url("file:///etc/passwd") is False
    assert is_safe_http_url("ftp://ftp.musicbrainz.org") is False
    assert is_safe_http_url("") is False


def test_resilient_http_client_retries(app):
    with app.app_context():
        client = ResilientHttpClient(
            provider_name="test_provider",
            max_retries=2,
            initial_backoff=0.01,
            jitter=0.01,
        )

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"success": true}'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        # Mock first call as 429 then 200
        mock_429 = MagicMock()
        mock_429.status = 429
        mock_429.headers = {"Retry-After": "0.02"}
        mock_429.__enter__.return_value = mock_429
        mock_429.__exit__.return_value = None

        with patch("urllib.request.urlopen", side_effect=[mock_429, mock_resp]):
            data = client.get_json("https://example.com/api", use_cache=False)
            assert data == {"success": True}


# --------------------------------------------------------------------------
# 4. Confidence Scoring Tests
# --------------------------------------------------------------------------


def test_confidence_scoring():
    # Exact match
    score1 = compute_confidence_score("The Matrix", "The Matrix", "1999", "1999")
    assert score1 >= 0.95

    # Partial match with same year
    score2 = compute_confidence_score(
        "Matrix Reloaded", "The Matrix Reloaded", "2003", "2003"
    )
    assert score2 >= 0.8

    # Distant year penalty
    score3 = compute_confidence_score("Dune", "Dune", "1984", "2021")
    assert score3 < score1


# --------------------------------------------------------------------------
# 5. TMDB Provider Tests
# --------------------------------------------------------------------------


def test_tmdb_provider_search_and_fetch():
    mock_client = MagicMock()
    mock_client.get_json.side_effect = [
        # Movie search response
        {
            "results": [
                {
                    "id": 550,
                    "title": "Fight Club",
                    "release_date": "1999-10-15",
                    "poster_path": "/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg",
                    "overview": "An insomniac office worker...",
                    "vote_average": 8.4,
                }
            ]
        },
        # TV search response
        {"results": []},
        # Movie details response
        {
            "id": 550,
            "title": "Fight Club",
            "release_date": "1999-10-15",
            "overview": "Full synopsis of Fight Club",
            "poster_path": "/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg",
            "genres": [{"name": "Drama"}],
            "runtime": 139,
            "credits": {"crew": [{"name": "David Fincher", "job": "Director"}]},
        },
    ]
    mock_client.get_bytes.return_value = b"mock_poster_bytes"

    provider = TMDBProvider(api_key="mock_tmdb_key", client=mock_client)
    candidates = provider.search("Fight Club", media_type="video", year="1999")
    assert len(candidates) >= 1
    assert candidates[0].title == "Fight Club"
    assert candidates[0].id == "movie:550"
    assert candidates[0].score >= 0.9

    details = provider.fetch_details("movie:550", media_type="video")
    assert details is not None
    assert details.title == "Fight Club"
    assert "David Fincher" in details.creators
    assert details.poster_bytes == b"mock_poster_bytes"
    assert details.duration == 139 * 60.0


# --------------------------------------------------------------------------
# 6. MusicBrainz Provider Tests
# --------------------------------------------------------------------------


def test_musicbrainz_provider_search_and_fetch():
    mock_client = MagicMock()
    mock_client.get_json.side_effect = [
        # MB Release search
        {
            "releases": [
                {
                    "id": "release-mbid-1234",
                    "title": "Random Access Memories",
                    "date": "2013-05-17",
                    "artist-credit": [{"name": "Daft Punk"}],
                    "label-info": [{"label": {"name": "Columbia"}}],
                }
            ]
        },
        # MB Release details
        {
            "id": "release-mbid-1234",
            "title": "Random Access Memories",
            "date": "2013-05-17",
            "artist-credit": [{"name": "Daft Punk"}],
            "genres": [{"name": "Electronic"}, {"name": "Disco"}],
            "label-info": [{"label": {"name": "Columbia"}}],
        },
        # CAA artwork response
        {
            "images": [
                {
                    "front": True,
                    "image": "https://coverartarchive.org/release/1234/front.jpg",
                }
            ]
        },
    ]
    mock_client.get_bytes.return_value = b"mock_album_cover"

    provider = MusicBrainzProvider(client=mock_client)
    candidates = provider.search(
        "Random Access Memories", media_type="music", year="2013"
    )
    assert len(candidates) >= 1
    assert candidates[0].title == "Random Access Memories"
    assert "Daft Punk" in candidates[0].creators
    assert candidates[0].id == "release-mbid-1234"

    details = provider.fetch_details("release-mbid-1234", media_type="music")
    assert details is not None
    assert details.title == "Random Access Memories"
    assert "Daft Punk" in details.creators
    assert details.publisher == "Columbia"
    assert details.poster_bytes == b"mock_album_cover"


# --------------------------------------------------------------------------
# 7. Field-Level Locking and Scanner Integrity Tests
# --------------------------------------------------------------------------


def test_field_level_locking_on_media_item(app):
    with app.app_context():
        item = MediaItem(
            title="Original Title",
            original_file_path="/tmp/fake_media.mp4",
            file_format="mp4",
            file_hash="dummyhash123",
            media_type="video",
        )
        assert item.get_locked_fields() == []
        assert item.is_field_locked("title") is False

        # Lock title and description
        item.lock_field("title")
        item.lock_field("description")
        assert item.is_field_locked("title") is True
        assert item.is_field_locked("description") is True
        assert item.is_field_locked("publisher") is False

        # Unlock description
        item.unlock_field("description")
        assert item.is_field_locked("description") is False
        assert item.is_field_locked("title") is True


def test_scanner_preserves_locked_fields(app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None

        # Lock title and author
        book.title = "User Curated Custom Title"
        book.lock_field("title")
        book.lock_field("authors")
        db.session.commit()

        # Re-index the same file
        re_indexed = index_media_file(sample_epub, covers_dir)
        assert re_indexed is not None
        # Title must NOT have reverted to sample epub title
        assert re_indexed.title == "User Curated Custom Title"


# --------------------------------------------------------------------------
# 8. REST API Fix Match & Metadata Candidate Endpoints
# --------------------------------------------------------------------------


def test_api_metadata_search_and_apply(client, app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

        admin = User(username="meta_admin", is_admin=True)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "meta_admin", "password": "adminpass"},
        follow_redirects=True,
    )

    mock_candidates = [
        MetadataSearchResult(
            id="test_candidate_123",
            provider="mockprovider",
            title="Enriched Title Online",
            year="2026",
            creators=["Online Author"],
            overview="Online synopsis",
            score=0.95,
        )
    ]

    mock_details = MediaMetadataDetails(
        id="test_candidate_123",
        provider="mockprovider",
        title="Enriched Title Online",
        creators=["Online Author"],
        overview="Online synopsis",
        poster_bytes=b"fake_cover_bytes",
    )

    with (
        patch(
            "aarkib.services.metadata.registry.metadata_registry.search",
            return_value=mock_candidates,
        ),
        patch(
            "aarkib.services.metadata.registry.metadata_registry.fetch_details",
            return_value=mock_details,
        ),
    ):
        # 1. Search candidates
        s_res = client.get(f"/api/media/{book_id}/metadata/search?q=Enriched")
        assert s_res.status_code == 200
        s_data = s_res.get_json()
        assert s_data["status"] == "success"
        assert len(s_data["candidates"]) == 1
        assert s_data["candidates"][0]["id"] == "test_candidate_123"

        # 2. Apply candidate with field locking
        a_res = client.post(
            f"/api/media/{book_id}/metadata/apply",
            json={
                "provider": "mockprovider",
                "external_id": "test_candidate_123",
                "lock_fields": ["title", "description"],
            },
        )
        assert a_res.status_code == 200
        a_data = a_res.get_json()
        assert a_data["status"] == "success"
        assert "title" in a_data["locked_fields"]
        assert "description" in a_data["locked_fields"]

        # Verify DB record
        with app.app_context():
            updated = db.session.get(MediaItem, book_id)
            assert updated.title == "Enriched Title Online"
            assert updated.is_field_locked("title") is True
            assert updated.external_id == "mockprovider:test_candidate_123"

        # 3. Inspect and update locked-fields endpoint
        lf_get = client.get(f"/api/media/{book_id}/metadata/locked-fields")
        assert lf_get.status_code == 200
        assert "title" in lf_get.get_json()["locked_fields"]

        lf_put = client.put(
            f"/api/media/{book_id}/metadata/locked-fields",
            json={"locked_fields": ["title", "cover_image"]},
        )
        assert lf_put.status_code == 200
        assert set(lf_put.get_json()["locked_fields"]) == {"title", "cover_image"}


def test_providers_dynamic_api_key_resolution(app):
    """Verifies that all metadata providers dynamically pick up API keys from app.config."""
    from aarkib.services.metadata.providers.comicvine import ComicVineProvider
    from aarkib.services.metadata.providers.google_books import GoogleBooksProvider
    from aarkib.services.metadata.providers.podcastindex import PodcastIndexProvider
    from aarkib.services.metadata.providers.tmdb import TMDBProvider
    from aarkib.services.settings_service import update_settings

    with app.app_context():
        # TMDB
        tmdb = TMDBProvider()
        update_settings(app, {"TMDB_API_KEY": "dynamic_tmdb_key_abc"})
        assert tmdb._get_api_key() == "dynamic_tmdb_key_abc"

        # ComicVine
        cv = ComicVineProvider()
        update_settings(app, {"COMICVINE_API_KEY": "dynamic_cv_key_def"})
        assert cv.get_api_key() == "dynamic_cv_key_def"

        # PodcastIndex
        pi = PodcastIndexProvider()
        update_settings(
            app,
            {
                "PODCASTINDEX_API_KEY": "pi_key_123",
                "PODCASTINDEX_API_SECRET": "pi_secret_456",
            },
        )
        assert pi.api_key == "pi_key_123"
        assert pi.api_secret == "pi_secret_456"
        assert pi.is_configured() is True

        # Google Books
        gb = GoogleBooksProvider()
        update_settings(app, {"GOOGLE_BOOKS_API_KEY": "gb_key_789"})
        assert gb.get_api_key() == "gb_key_789"
