from __future__ import annotations

import shutil
from pathlib import Path

from pypdf import PdfWriter

from aarkib.extensions import db
from aarkib.models import MediaItem
from aarkib.plugins.book import BookMediaPlugin
from aarkib.services.parsers.pdf import parse_pdf
from aarkib.services.scanner import scan_library


def test_parse_pdf(sample_pdf: Path):
    """Test extracting metadata from a PDF file."""
    meta = parse_pdf(sample_pdf)
    assert meta is not None
    assert meta.title == "Sample PDF Document"
    assert "Dr. Alice Smith" in meta.creators
    assert meta.description == "A sample PDF document for automated testing"
    assert meta.page_count == 2
    assert meta.file_format == "pdf"
    assert meta.media_type == "book"
    assert meta.publication_date == "2024-01-01"


def test_parse_pdf_series_extraction(tmp_path: Path):
    """Test extracting series name and volume index from filename heuristic."""
    pdf_path = tmp_path / "Foundation - Vol. 02 - Foundation and Empire.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=450)
    with open(pdf_path, "wb") as f:
        writer.write(f)

    meta = parse_pdf(pdf_path)
    assert meta is not None
    assert meta.series == "Foundation"
    assert meta.series_index == 2.0
    assert meta.title == "Foundation and Empire"


def test_book_plugin_pdf_integration(sample_pdf: Path):
    """Test BookMediaPlugin registers and handles PDF format."""
    plugin = BookMediaPlugin()
    assert ".pdf" in plugin.supported_extensions

    url = plugin.get_player_url(99, "pdf")
    assert url == "/reader/pdf/99"

    meta = plugin.parse_metadata(sample_pdf)
    assert meta is not None
    assert meta.file_format == "pdf"


def test_pdf_scanner_and_web_reader(app, client, sample_pdf: Path):
    """Test full flow: scanner indexes PDF, and reader route serves reader_pdf.html."""
    media_dir = Path(app.config["MEDIA_DIR"])
    media_dir.mkdir(parents=True, exist_ok=True)
    target_pdf = media_dir / "test_book.pdf"
    shutil.copy(sample_pdf, target_pdf)

    # Scan library
    scan_library(app)

    with app.app_context():
        item = db.session.query(MediaItem).filter_by(file_format="pdf").first()
        assert item is not None
        assert item.title == "Sample PDF Document"
        assert item.page_count == 2
        item_id = item.id

    # Test Web reader route
    resp = client.get(f"/reader/pdf/{item_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Sample PDF Document" in html
    assert f"/api/media/{item_id}/file" in html
    assert "Page" in html

    # Test non-existent PDF returns 404
    resp_404 = client.get("/reader/pdf/999999")
    assert resp_404.status_code == 404


def test_pdf_progress_reporting(app, client, sample_pdf: Path):
    """Test saving and retrieving progress on a PDF."""
    media_dir = Path(app.config["MEDIA_DIR"])
    media_dir.mkdir(parents=True, exist_ok=True)
    target_pdf = media_dir / "progress_book.pdf"
    shutil.copy(sample_pdf, target_pdf)

    scan_library(app)

    with app.app_context():
        item = db.session.query(MediaItem).filter_by(file_format="pdf").first()
        item_id = item.id

    # Post page progress
    post_resp = client.post(
        f"/api/media/{item_id}/progress",
        json={"location": "2", "percentage": 100.0, "is_completed": True},
    )
    assert post_resp.status_code == 200

    # Retrieve progress
    get_resp = client.get(f"/api/media/{item_id}/progress")
    assert get_resp.status_code == 200
    data = get_resp.get_json()
    assert data["location"] == "2"
    assert data["percentage"] == 100.0
    assert data["is_completed"] is True


def test_pdf_opds_acquisition(app, client, sample_pdf: Path):
    """Test that OPDS 2.0 feed returns application/pdf acquisition link for PDF items."""
    media_dir = Path(app.config["MEDIA_DIR"])
    media_dir.mkdir(parents=True, exist_ok=True)
    target_pdf = media_dir / "opds_book.pdf"
    shutil.copy(sample_pdf, target_pdf)

    scan_library(app)

    resp = client.get("/opds/v2/recent.json")
    assert resp.status_code == 200
    data = resp.get_json()
    publications = data.get("publications", [])
    pdf_pubs = [
        p
        for p in publications
        if any(link.get("type") == "application/pdf" for link in p.get("links", []))
    ]
    assert len(pdf_pubs) >= 1
