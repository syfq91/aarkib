from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from aarkib.extensions import db
from aarkib.models import Library, MediaItem
from aarkib.services.media_service import (
    count_media_in_library,
    edit_media_metadata,
    generate_slug,
    library_path_conditions,
    path_match_filter,
    path_prefixes,
    resolve_library,
    resolve_or_create_authors,
    resolve_or_create_collections,
    resolve_or_create_creators,
    resolve_or_create_series,
    resolve_or_create_tags,
)

if TYPE_CHECKING:
    from flask import Flask


def test_resolve_entities(app: Flask) -> None:
    """Verify author, series, and tag entity resolvers handle creation and deduplication."""
    with app.app_context():
        # Authors / Creators
        authors = resolve_or_create_authors(["Arthur Conan Doyle", "Agatha Christie"])
        db.session.flush()
        assert len(authors) == 2
        names = {a.name for a in authors}
        assert names == {"Arthur Conan Doyle", "Agatha Christie"}

        # Alias resolve_or_create_creators with existing and new author
        creators = resolve_or_create_creators(["Arthur Conan Doyle", "J.K. Rowling"])
        db.session.flush()
        assert len(creators) == 2
        assert creators[0].id == authors[0].id

        # Series / Collections
        series1 = resolve_or_create_series("Sherlock Holmes")
        db.session.flush()
        series2 = resolve_or_create_series("  Sherlock Holmes  ")
        assert series1.id == series2.id

        # Alias resolve_or_create_collections
        col = resolve_or_create_collections("Sherlock Holmes")
        assert col.id == series1.id

        # Tags
        tags = resolve_or_create_tags(["mystery", "DETECTIVE", "fiction"])
        db.session.flush()
        assert len(tags) == 3
        tag_names = {t.name for t in tags}
        assert tag_names == {"Mystery", "Detective", "Fiction"}


def test_edit_media_metadata(app: Flask) -> None:
    """Verify edit_media_metadata updates all fields including video, audio, and field locks."""
    with app.app_context():
        item = MediaItem(
            title="Initial Title",
            original_file_path="/media/books/sample.epub",
            file_format="epub",
            file_hash="hash_init_edit",
            media_type="book",
        )
        db.session.add(item)
        db.session.flush()

        # Update core and descriptive metadata
        data = {
            "title": "New Book Title",
            "authors": "Author One, Author Two",
            "series": "Great Adventures",
            "series_index": "2.5",
            "tags": "Adventure, Thriller",
            "description": "An exciting story.",
            "publisher": "Acme Publishing",
            "publication_date": "2024-05-01",
            "isbn": "9781234567890",
            "language": "en",
            "season": "1",
            "episode": "4",
            "album": "Original Soundtrack",
            "track_number": "7",
            "disc_number": "1",
            "locked_fields": ["title", "description"],
        }
        updated = edit_media_metadata(item, data)
        db.session.flush()

        assert updated.title == "New Book Title"
        assert len(updated.authors) == 2
        assert updated.series is not None
        assert updated.series.name == "Great Adventures"
        assert updated.series_index == 2.5
        assert len(updated.tags) == 2
        assert updated.description == "An exciting story."
        assert updated.publisher == "Acme Publishing"
        assert updated.publication_date == "2024-05-01"
        assert updated.isbn == "9781234567890"
        assert updated.season == 1
        assert updated.episode == 4
        assert updated.album == "Original Soundtrack"
        assert updated.track_number == 7
        assert updated.disc_number == 1
        assert "title" in updated.get_locked_fields()
        assert "description" in updated.get_locked_fields()

        # Test clearing series
        clear_data = {"series": ""}
        cleared = edit_media_metadata(item, clear_data)
        db.session.flush()
        assert cleared.series is None
        assert cleared.series_index is None


def test_library_utilities(app: Flask) -> None:
    """Verify generate_slug, resolve_library, path prefixes, and media counting."""
    with app.app_context():
        lib = Library(
            name="Sci-Fi Books",
            slug="sci-fi-books",
            path="/media/scifi",
            media_type="book",
        )
        db.session.add(lib)
        db.session.flush()

        # generate_slug collision handling
        slug1 = generate_slug("Sci-Fi Books")
        assert slug1 == "sci-fi-books-2"

        # resolve_library by id and slug
        resolved_by_id = resolve_library(lib.id)
        assert resolved_by_id.id == lib.id
        resolved_by_slug = resolve_library("sci-fi-books")
        assert resolved_by_slug.id == lib.id

        with pytest.raises(KeyError):
            resolve_library("non-existent-lib")

        # path_prefixes and conditions
        p_res, p_raw = path_prefixes("/media/scifi")
        assert p_res.endswith("/")
        assert p_raw.endswith("/")

        cond_res, cond_raw = library_path_conditions(lib)
        assert cond_res == p_res

        filt = path_match_filter("/media/scifi", lib)
        assert filt is not None

        # count_media_in_library
        item1 = MediaItem(
            title="Dune",
            original_file_path="/media/scifi/dune.epub",
            file_format="epub",
            file_hash="hash_dune",
            media_type="book",
            library_id=lib.id,
        )
        item2 = MediaItem(
            title="Foundation",
            original_file_path="/media/scifi/foundation.epub",
            file_format="epub",
            file_hash="hash_foundation",
            media_type="book",
            library_id=lib.id,
        )
        db.session.add_all([item1, item2])
        db.session.commit()

        count = count_media_in_library(lib)
        assert count == 2
