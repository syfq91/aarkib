from pathlib import Path
from unittest.mock import patch

from aarkib.extensions import db
from aarkib.models import MediaItem
from aarkib.services.enricher import enrich_media_item
from aarkib.services.indexer import index_media_file
from aarkib.services.metadata.base import MediaMetadataDetails, MetadataSearchResult


def test_enrich_book_in_db(app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None

        mock_search_result = [
            MetadataSearchResult(
                id="mock-123",
                provider="mock",
                title="Sample Test Book",
                score=1.0,
            )
        ]

        mock_details = MediaMetadataDetails(
            id="mock-123",
            provider="mock",
            title="Sample Test Book",
            creators=["John Doe"],
            overview="An enriched description from online source.",
            publisher="Enriched Publishing",
            release_date="2026-01-01",
            page_count=250,
            genres=["Fiction", "Technology"],
            isbn="9781234567890",
        )

        with (
            patch(
                "aarkib.services.metadata.registry.MetadataProviderRegistry.search",
                return_value=mock_search_result,
            ),
            patch(
                "aarkib.services.metadata.registry.MetadataProviderRegistry.fetch_details",
                return_value=mock_details,
            ),
        ):
            result = enrich_media_item(book, covers_dir, overwrite=True)
            assert result["status"] == "success"
            assert "description" in result["changes"]
            assert "publisher" in result["changes"]

            updated_book = db.session.get(MediaItem, book.id)
            assert (
                updated_book.description
                == "An enriched description from online source."
            )
            assert updated_book.publisher == "Enriched Publishing"
            assert updated_book.page_count == 250


def test_api_enrich_endpoints(client, app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

    # Enrich endpoints require an authenticated admin user
    from aarkib.models import User

    with app.app_context():
        admin = User(username="enrich_admin", is_admin=True)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()
    client.post(
        "/auth/login",
        data={"username": "enrich_admin", "password": "adminpass"},
        follow_redirects=True,
    )

    mock_search_result = [
        MetadataSearchResult(
            id="mock-123",
            provider="mock",
            title="Sample Test Book",
            score=1.0,
        )
    ]

    mock_details = MediaMetadataDetails(
        id="mock-123",
        provider="mock",
        title="Sample Test Book",
        overview="Enriched via API test",
        publisher="API Books",
    )

    with (
        patch(
            "aarkib.services.metadata.registry.MetadataProviderRegistry.search",
            return_value=mock_search_result,
        ),
        patch(
            "aarkib.services.metadata.registry.MetadataProviderRegistry.fetch_details",
            return_value=mock_details,
        ),
    ):
        # Single book enrich
        res = client.post(f"/api/media/{book_id}/enrich", json={"overwrite": True})
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "success"

        # Library enrich (sync mode)
        res = client.post("/api/library/enrich?sync=true", json={"overwrite": False})
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "success"


def test_enrich_database_session_detached_during_external_io(app, sample_epub):
    """C3 test: verifies that db.session has no open transaction during external network requests."""
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None

        mock_search_result = [
            MetadataSearchResult(
                id="mock-123",
                provider="mock",
                title="Sample Test Book",
                score=1.0,
            )
        ]

        mock_details = MediaMetadataDetails(
            id="mock-123",
            provider="mock",
            title="Sample Test Book",
            creators=["John Doe"],
            overview="Enriched description",
            publisher="Enriched Publishing",
        )

        in_transaction_during_call = None

        def mock_search(*args, **kwargs):
            nonlocal in_transaction_during_call
            in_transaction_during_call = db.session().in_transaction()
            return mock_search_result

        def mock_fetch(*args, **kwargs):
            return mock_details

        with (
            patch(
                "aarkib.services.metadata.registry.MetadataProviderRegistry.search",
                side_effect=mock_search,
            ),
            patch(
                "aarkib.services.metadata.registry.MetadataProviderRegistry.fetch_details",
                side_effect=mock_fetch,
            ),
        ):
            result = enrich_media_item(book, covers_dir, overwrite=True)
            assert result["status"] == "success"
            assert in_transaction_during_call is False


def test_api_metadata_search_session_detached(client, app, sample_epub):
    """C3 test: verifies that db.session is detached before external metadata search."""
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

    from aarkib.models import User

    with app.app_context():
        admin = User(username="search_admin", is_admin=True)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "search_admin", "password": "adminpass"},
        follow_redirects=True,
    )

    in_transaction_during_search = None

    def mock_search(*args, **kwargs):
        nonlocal in_transaction_during_search
        in_transaction_during_search = db.session().in_transaction()
        return []

    with patch(
        "aarkib.services.metadata.metadata_registry.search", side_effect=mock_search
    ):
        res = client.get(f"/api/media/{book_id}/metadata/search?q=test")
        assert res.status_code == 200
        assert in_transaction_during_search is False
