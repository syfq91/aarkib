from pathlib import Path
from unittest.mock import MagicMock, patch

from aarkib.extensions import db
from aarkib.models import MediaItem, MetadataSource, User, resolve_metadata_source_type
from aarkib.services.enricher import enrich_media_item
from aarkib.services.indexer import index_media_file
from aarkib.services.media_service import edit_media_metadata
from aarkib.services.metadata.base import MediaMetadataDetails, MetadataSearchResult
from aarkib.services.metadata.matcher import (
    CandidateMatch,
    MetadataMatcher,
    calculate_match_confidence,
)


def test_confidence_scoring_exact_and_fuzzy_matches():
    # Exact title and year
    score_exact = calculate_match_confidence(
        query_title="Dune",
        candidate_title="Dune",
        query_year="1965",
        candidate_year="1965",
    )
    assert score_exact >= 0.95

    # Title with subtitle containment bonus
    score_sub = calculate_match_confidence(
        query_title="Dune",
        candidate_title="Dune: Part One",
        query_year="2021",
        candidate_year="2021",
    )
    assert score_sub >= 0.75

    # Word reordering token Jaccard
    score_reordered = calculate_match_confidence(
        query_title="The Lord of the Rings",
        candidate_title="Lord of the Rings, The",
        query_year="1954",
        candidate_year="1954",
    )
    assert score_reordered >= 0.85

    # Completely unrelated titles
    score_unrelated = calculate_match_confidence(
        query_title="The Hobbit",
        candidate_title="Foundation and Earth",
        query_year="1937",
        candidate_year="1986",
    )
    assert score_unrelated <= 0.25


def test_confidence_scoring_year_proximity_and_creators():
    # Same title, exact year vs distant year
    score_same_year = calculate_match_confidence(
        query_title="Neuromancer",
        candidate_title="Neuromancer",
        query_year="1984",
        candidate_year="1984",
    )
    score_diff_year = calculate_match_confidence(
        query_title="Neuromancer",
        candidate_title="Neuromancer",
        query_year="1984",
        candidate_year="2015",
    )
    assert score_same_year > score_diff_year

    # Creator match bonus
    score_with_author = calculate_match_confidence(
        query_title="Foundation",
        candidate_title="Foundation",
        query_year="1951",
        candidate_year="1951",
        query_creators=["Isaac Asimov"],
        candidate_creators=["Asimov"],
    )
    score_no_author = calculate_match_confidence(
        query_title="Foundation",
        candidate_title="Foundation",
        query_year="1951",
        candidate_year="1951",
    )
    assert score_with_author >= score_no_author


def test_confidence_scoring_identifier_equality():
    # Exact ISBN match yields 1.0
    score_isbn = calculate_match_confidence(
        query_title="Some Mismatched Title Query",
        candidate_title="Another Title Candidate",
        query_identifiers={"isbn": "978-0-345-39180-3"},
        candidate_identifiers={"isbn": "9780345391803"},
    )
    assert score_isbn == 1.0

    # TMDB ID equality yields 1.0
    score_tmdb = calculate_match_confidence(
        query_title="Sample Movie",
        candidate_title="Sample Movie Alt",
        query_identifiers={"tmdb_id": "550"},
        candidate_identifiers={"tmdb_id": "550"},
    )
    assert score_tmdb == 1.0


def test_candidate_matcher_multi_provider_search():
    mock_registry = MagicMock()
    mock_registry.search.return_value = [
        MetadataSearchResult(
            id="low-1",
            provider="openlibrary",
            title="Random Unrelated",
            year="1999",
            score=0.2,
        ),
        MetadataSearchResult(
            id="high-1",
            provider="googlebooks",
            title="The Hobbit",
            year="1937",
            creators=["J.R.R. Tolkien"],
            score=0.9,
        ),
        MetadataSearchResult(
            id="mid-1",
            provider="openlibrary",
            title="The Hobbit: Pocket Edition",
            year="1980",
            score=0.6,
        ),
    ]

    matcher = MetadataMatcher(registry=mock_registry)
    candidates = matcher.find_candidates(
        item_or_query="The Hobbit",
        media_type="book",
        year="1937",
        creators=["J.R.R. Tolkien"],
    )

    assert len(candidates) == 3
    # Verify ranking: high-1 must be ranked first
    assert isinstance(candidates[0], CandidateMatch)
    assert candidates[0].id == "high-1"
    assert candidates[0].confidence_score >= candidates[1].confidence_score
    assert candidates[1].confidence_score >= candidates[2].confidence_score

    # Test CandidateMatch to_dict serialization
    cand_dict = candidates[0].to_dict()
    assert cand_dict["id"] == "high-1"
    assert "confidence_score" in cand_dict
    assert "score" in cand_dict
    assert cand_dict["confidence_score"] == cand_dict["score"]

    # Test select_best_match
    best = matcher.select_best_match(candidates, min_confidence=0.5)
    assert best is not None
    assert best.id == "high-1"

    # With impossible threshold (> 1.0), no match selected
    assert matcher.select_best_match(candidates, min_confidence=1.01) is None


def test_provenance_distinction_sources(app):
    with app.app_context():
        # Source resolution helper
        assert resolve_metadata_source_type("user") == MetadataSource.MANUAL
        assert resolve_metadata_source_type("manual_user") == MetadataSource.MANUAL
        assert resolve_metadata_source_type("api") == MetadataSource.MANUAL
        assert resolve_metadata_source_type("file_metadata") == MetadataSource.DERIVED
        assert resolve_metadata_source_type("id3") == MetadataSource.DERIVED
        assert resolve_metadata_source_type("openlibrary") == MetadataSource.AUTOMATIC
        assert resolve_metadata_source_type("tmdb") == MetadataSource.AUTOMATIC

        item = MediaItem(
            title="Provenance Test Item",
            original_file_path="/tmp/test_item.epub",
            file_format="epub",
            file_hash="testprovhash",
            media_type="book",
        )

        # 1. Set derived provenance from file metadata
        item.set_field_provenance(
            "title",
            "file_metadata",
            source_type=MetadataSource.DERIVED,
            confidence=1.0,
            value="Provenance Test Item",
        )
        assert item.get_provenance_for_field("title") == "file_metadata"
        assert item.get_provenance_type_for_field("title") == MetadataSource.DERIVED

        # Backward compatibility: get_field_provenance returns string mapping
        prov_map = item.get_field_provenance()
        assert prov_map.get("title") == "file_metadata"

        # Rich detailed provenance
        detailed = item.get_detailed_field_provenance("title")
        assert detailed is not None
        assert detailed["source"] == "file_metadata"
        assert detailed["source_type"] == "derived"
        assert detailed["confidence"] == 1.0
        assert detailed["value"] == "Provenance Test Item"
        assert "updated_at" in detailed

        # 2. Update to manual user edit
        item.set_field_provenance(
            "title",
            "user",
            source_type=MetadataSource.MANUAL,
            confidence=1.0,
            value="User Edited Title",
        )
        assert item.get_provenance_for_field("title") == "user"
        assert item.get_provenance_type_for_field("title") == MetadataSource.MANUAL

        all_detailed = item.get_all_detailed_provenance()
        assert "title" in all_detailed
        assert all_detailed["title"]["source_type"] == "manual"


def test_manual_edit_lock_protection_on_rescan(app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

        # User edits title and description
        edit_data = {
            "title": "My Immutable Custom Title",
            "description": "My curated synopsis that must never be lost.",
        }
        updated = edit_media_metadata(book, edit_data)
        db.session.commit()

        # Check invariant: modified fields are marked MANUAL and locked
        assert updated.is_field_locked("title") is True
        assert updated.is_field_locked("description") is True
        assert updated.get_provenance_type_for_field("title") == MetadataSource.MANUAL
        assert (
            updated.get_provenance_type_for_field("description")
            == MetadataSource.MANUAL
        )

        # Simulate rescan / re-indexing of the same physical file
        re_indexed = index_media_file(sample_epub, covers_dir)
        assert re_indexed is not None
        assert re_indexed.id == book_id

        # Verification: User edits MUST be preserved
        assert re_indexed.title == "My Immutable Custom Title"
        assert re_indexed.description == "My curated synopsis that must never be lost."


def test_manual_edit_lock_protection_on_enrichment(app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None

        # User edits title and summary
        edit_media_metadata(
            book,
            {
                "title": "Protected User Title",
                "description": "Protected User Description",
            },
        )
        db.session.commit()

        mock_candidate = [
            MetadataSearchResult(
                id="online-456",
                provider="openlibrary",
                title="Protected User Title",
                overview="Online Auto Description",
                score=0.9,
            )
        ]
        mock_details = MediaMetadataDetails(
            id="online-456",
            provider="openlibrary",
            title="Online Overwrite Title",
            overview="Online Overwrite Description",
            publisher="Online Publisher",
        )

        with (
            patch(
                "aarkib.services.metadata.registry.MetadataProviderRegistry.search",
                return_value=mock_candidate,
            ),
            patch(
                "aarkib.services.metadata.registry.MetadataProviderRegistry.fetch_details",
                return_value=mock_details,
            ),
        ):
            # 1. Background / automatic enrichment with overwrite=False
            res_safe = enrich_media_item(book, covers_dir, overwrite=False)
            assert res_safe["status"] in ("success", "no_changes_needed")
            db.session.commit()

            item_after_safe = db.session.get(MediaItem, book.id)
            # Invariant: MANUAL locked fields must NOT be overwritten
            assert item_after_safe.title == "Protected User Title"
            assert item_after_safe.description == "Protected User Description"
            # Unlocked empty fields (publisher) can be populated
            assert item_after_safe.publisher == "Online Publisher"

            # 2. User explicitly checks overwrite=True
            res_overwrite = enrich_media_item(book, covers_dir, overwrite=True)
            assert res_overwrite["status"] == "success"
            db.session.commit()

            item_after_force = db.session.get(MediaItem, book.id)
            assert item_after_force.title == "Online Overwrite Title"
            assert item_after_force.description == "Online Overwrite Description"
            assert (
                item_after_force.get_provenance_type_for_field("title")
                == MetadataSource.AUTOMATIC
            )


def test_api_metadata_search_with_matcher(client, app, sample_epub):
    with app.app_context():
        covers_dir = Path(app.config["COVERS_DIR"])
        book = index_media_file(sample_epub, covers_dir)
        assert book is not None
        book_id = book.id

        admin = User(username="matcher_admin", is_admin=True)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "matcher_admin", "password": "adminpass"},
        follow_redirects=True,
    )

    mock_search_results = [
        MetadataSearchResult(
            id="cand-1",
            provider="googlebooks",
            title="The Hobbit",
            year="1937",
            creators=["J.R.R. Tolkien"],
            score=0.95,
        )
    ]

    with patch(
        "aarkib.services.metadata.registry.MetadataProviderRegistry.search",
        return_value=mock_search_results,
    ):
        res = client.get(f"/api/media/{book_id}/metadata/search?q=The+Hobbit")
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "success"
        assert len(data["candidates"]) == 1
        cand = data["candidates"][0]
        assert cand["id"] == "cand-1"
        assert "confidence_score" in cand
        assert "score" in cand
        assert cand["confidence_score"] >= 0.70
