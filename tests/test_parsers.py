from aarkib.services.parsers.cbz import parse_cbz
from aarkib.services.parsers.epub import extract_series_from_title, parse_epub


def test_extract_series_from_title():
    s, idx, title = extract_series_from_title("[One Piece 01] Romance Dawn")
    assert s == "One Piece"
    assert idx == 1.0
    assert title == "Romance Dawn"

    s, idx, title = extract_series_from_title(
        "Harry Potter - Book 2 - Chamber of Secrets"
    )
    assert s == "Harry Potter"
    assert idx == 2.0
    assert title == "Chamber of Secrets"

    s, idx, title = extract_series_from_title("Dune #1 - Dune")
    assert s == "Dune"
    assert idx == 1.0
    assert title == "Dune"


def test_parse_epub(sample_epub):
    meta = parse_epub(sample_epub)
    assert meta is not None
    assert meta.title == "Sample Test Book"
    assert "Jane Doe" in meta.creators
    assert meta.isbn == "978-3-16-148410-0"
    assert "Fiction" in meta.tags
    assert meta.file_format == "epub"


def test_parse_cbz(sample_cbz):
    meta = parse_cbz(sample_cbz)
    assert meta is not None
    assert meta.title == "Episode 1: The Beginning"
    assert meta.series == "Epic Adventure"
    assert meta.series_index == 1.0
    assert "John Comics" in meta.creators
    assert meta.page_count == 2
    assert meta.file_format == "cbz"
