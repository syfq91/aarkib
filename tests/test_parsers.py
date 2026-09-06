from aarkib.services.parsers.cbz import parse_cbz
from aarkib.services.parsers.epub import parse_epub


def test_parse_epub(sample_epub):
    meta = parse_epub(sample_epub)
    assert meta is not None
    assert meta.title == "Sample Test Book"
    assert "Jane Doe" in meta.authors
    assert meta.isbn == "978-3-16-148410-0"
    assert "Fiction" in meta.tags
    assert meta.file_format == "epub"


def test_parse_cbz(sample_cbz):
    meta = parse_cbz(sample_cbz)
    assert meta is not None
    assert meta.title == "Episode 1: The Beginning"
    assert meta.series == "Epic Adventure"
    assert meta.series_index == 1.0
    assert "John Comics" in meta.authors
    assert meta.page_count == 2
    assert meta.file_format == "cbz"
