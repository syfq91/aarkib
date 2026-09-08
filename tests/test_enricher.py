from pathlib import Path
from unittest.mock import patch

from aarkib.extensions import db
from aarkib.models import Book
from aarkib.services.enricher import (
    EnrichedMetadata,
    enrich_book,
    fetch_from_google_books,
    fetch_from_open_library,
)
from aarkib.services.scanner import index_single_book


def test_fetch_from_google_books_mock():
    mock_gb_response = {
        "items": [
            {
                "volumeInfo": {
                    "title": "The Way of Kings",
                    "authors": ["Brandon Sanderson"],
                    "description": "Epic fantasy novel.",
                    "publisher": "Tor Books",
                    "publishedDate": "2010-08-31",
                    "pageCount": 1007,
                    "categories": ["Fantasy", "Fiction"],
                    "industryIdentifiers": [
                        {"type": "ISBN_13", "identifier": "9780765326355"}
                    ],
                    "imageLinks": {"thumbnail": "http://example.com/cover.jpg"},
                }
            }
        ]
    }

    with (
        patch("aarkib.services.enricher._http_get_json", return_value=mock_gb_response),
        patch(
            "aarkib.services.enricher._http_get_bytes", return_value=b"fake_image_bytes"
        ),
    ):
        meta = fetch_from_google_books(
            title="The Way of Kings", author="Brandon Sanderson"
        )
        assert meta is not None
        assert meta.title == "The Way of Kings"
        assert meta.authors == ["Brandon Sanderson"]
        assert meta.publisher == "Tor Books"
        assert meta.page_count == 1007
        assert meta.isbn == "9780765326355"
        assert meta.cover_bytes == b"fake_image_bytes"


def test_fetch_from_open_library_mock():
    mock_ol_response = {
        "docs": [
            {
                "title": "Mistborn",
                "author_name": ["Brandon Sanderson"],
                "publisher": ["Tor Books"],
                "first_publish_year": 2006,
                "subject": ["High Fantasy", "Magic"],
                "isbn": ["9780765311788"],
                "cover_i": 12345,
            }
        ]
    }

    with (
        patch("aarkib.services.enricher._http_get_json", return_value=mock_ol_response),
        patch("aarkib.services.enricher._http_get_bytes", return_value=b"fake_cover"),
    ):
        meta = fetch_from_open_library(title="Mistborn", author="Brandon Sanderson")
        assert meta is not None
        assert meta.title == "Mistborn"
        assert meta.authors == ["Brandon Sanderson"]
        assert meta.publication_date == "2006"
        assert meta.isbn == "9780765311788"


def test_enrich_book_in_db(app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        assert book is not None

        mock_meta = EnrichedMetadata(
            title="Sample Test Book",
            authors=["John Doe"],
            description="An enriched description from online source.",
            publisher="Enriched Publishing",
            publication_date="2026-01-01",
            page_count=250,
            tags=["Fiction", "Technology"],
            isbn="9781234567890",
            source="Mock Source",
        )

        with patch(
            "aarkib.services.enricher.fetch_external_metadata", return_value=mock_meta
        ):
            result = enrich_book(book, covers_dir, overwrite=True)
            assert result["status"] == "success"
            assert "description" in result["changes"]
            assert "publisher" in result["changes"]

            updated_book = db.session.get(Book, book.id)
            assert (
                updated_book.description
                == "An enriched description from online source."
            )
            assert updated_book.publisher == "Enriched Publishing"
            assert updated_book.page_count == 250


def test_api_enrich_endpoints(client, app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_single_book(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

    mock_meta = EnrichedMetadata(
        title="Sample Test Book",
        description="Enriched via API test",
        publisher="API Books",
        source="Mock Source",
    )

    with patch(
        "aarkib.services.enricher.fetch_external_metadata", return_value=mock_meta
    ):
        # Single book enrich
        res = client.post(f"/api/books/{book_id}/enrich", json={"overwrite": True})
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "success"

        # Library enrich
        res = client.post("/api/library/enrich", json={"overwrite": False})
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "success"


def test_is_safe_http_url():
    from aarkib.services.enricher import (
        _http_get_bytes,
        _http_get_json,
        _is_safe_http_url,
    )

    assert _is_safe_http_url("https://example.com/api") is True
    assert _is_safe_http_url("http://example.com/image.jpg") is True
    assert _is_safe_http_url("file:///etc/passwd") is False
    assert _is_safe_http_url("ftp://example.com/resource") is False
    assert _is_safe_http_url("javascript:alert(1)") is False
    assert _is_safe_http_url("") is False
    assert _is_safe_http_url(None) is False  # type: ignore

    # Verify functions reject non-http schemes without making network calls
    assert _http_get_json("file:///etc/passwd") is None
    assert _http_get_bytes("file:///etc/passwd") is None
