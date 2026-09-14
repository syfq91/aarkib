from __future__ import annotations

from types import SimpleNamespace

from aarkib.models import Book
from aarkib.services.indexer import (
    _assign_authors_tags_series,
    _resolve_media_type,
    compute_sha256,
    compute_sort_title,
    get_supported_extensions,
)


def test_compute_sort_title():
    assert compute_sort_title("The Hobbit") == "Hobbit"
    assert compute_sort_title("A Tale of Two Cities") == "Tale of Two Cities"
    assert compute_sort_title("An American Tragedy") == "American Tragedy"
    assert compute_sort_title("Dune") == "Dune"
    assert compute_sort_title("") == ""


def test_compute_sha256(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    h = compute_sha256(f)
    assert len(h) == 64
    assert h == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_get_supported_extensions():
    exts = get_supported_extensions()
    assert ".epub" in exts
    assert ".cbz" in exts
    assert ".mp4" in exts
    assert ".mp3" in exts


def test_resolve_media_type(app):
    with app.app_context():
        # Explicit library override
        assert (
            _resolve_media_type(SimpleNamespace(), "/path/book.epub", "comic")
            == "comic"
        )

        # By extension/format
        meta_comic = SimpleNamespace(file_format="cbz")
        assert _resolve_media_type(meta_comic, "/path/comic.cbz", None) == "comic"

        meta_video = SimpleNamespace(file_format="mp4", season=1, episode=2)
        assert _resolve_media_type(meta_video, "/path/tv.mp4", None) == "tv"

        meta_movie = SimpleNamespace(file_format="mkv")
        assert _resolve_media_type(meta_movie, "/path/movie.mkv", None) == "movie"

        meta_audiobook = SimpleNamespace(file_format="m4b")
        assert (
            _resolve_media_type(meta_audiobook, "/path/book.m4b", None) == "audiobook"
        )

        meta_music = SimpleNamespace(file_format="flac")
        assert _resolve_media_type(meta_music, "/path/song.flac", None) == "music"


def test_assign_authors_tags_series(app):
    from aarkib.extensions import db

    with app.app_context():
        book = Book(
            original_file_path="/path/test.epub",
            title="Test",
            file_hash="12345",
            file_format="epub",
        )
        db.session.add(book)
        meta = SimpleNamespace(
            authors=["Arthur Conan Doyle", " "],
            series="Sherlock Holmes",
            series_index=1.0,
            tags=["Mystery", "Classic"],
        )
        _assign_authors_tags_series(book, meta)

        assert len(book.authors) == 1
        assert book.authors[0].name == "Arthur Conan Doyle"
        assert book.series is not None
        assert book.series.name == "Sherlock Holmes"
        assert book.series_index == 1.0
        assert len(book.tags) == 2
        assert {t.name for t in book.tags} == {"Mystery", "Classic"}
