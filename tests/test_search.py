from __future__ import annotations

from sqlalchemy import text

from aarkib.extensions import db
from aarkib.models import Book, Collection, Creator, Tag
from aarkib.services.search import (
    init_search_fts,
    parse_fts_query,
    rebuild_search_index,
    remove_media_item_fts,
    search_media_ids,
    sync_media_item_fts,
)


def test_fts_table_initialization(app):
    """Verify media_items_fts table is created and operational."""
    with app.app_context():
        success = init_search_fts()
        assert success is True
        row = db.session.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='media_items_fts';"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "media_items_fts"


def test_parse_fts_query_sanitization():
    """Verify query parser handles standard words, prefix matching, quotes, and punctuation."""
    # Empty / whitespace
    assert parse_fts_query("") == ""
    assert parse_fts_query("   ") == ""

    # Simple keyword prefix matching
    assert parse_fts_query("dune") == '"dune"*'
    assert parse_fts_query("frank herbert") == '"frank"* "herbert"*'

    # Punctuation & hyphenation
    assert parse_fts_query("Spider-Man") == '"Spider"* "Man"*'
    assert parse_fts_query("C++") == '"C"*'
    assert parse_fts_query("test:123") == '"test"* "123"*'

    # Unbalanced quotes
    res_unclosed = parse_fts_query('unclosed"quote')
    assert '"unclosed"*' in res_unclosed

    # Balanced explicit phrase search
    assert parse_fts_query('"The Matrix"') == '"The Matrix"'

    # Boolean operators treated as quoted tokens (not raw syntax)
    res_bool = parse_fts_query("to be or not to be")
    assert '"or"*' in res_bool
    assert '"not"*' in res_bool


def test_rebuild_and_search_by_dimensions(app):
    """Verify search across title, creators, collections, tags, and descriptions."""
    with app.app_context():
        # Clean existing items
        db.session.execute(text("DELETE FROM media_creators;"))
        db.session.execute(text("DELETE FROM media_tags;"))
        db.session.execute(text("DELETE FROM media_items;"))
        db.session.execute(text("DELETE FROM creators;"))
        db.session.execute(text("DELETE FROM tags;"))
        db.session.execute(text("DELETE FROM collections;"))
        db.session.commit()

        c1 = Creator(name="Frank Herbert")
        c2 = Creator(name="Isaac Asimov")
        t1 = Tag(name="Science Fiction")
        t2 = Tag(name="Cyberpunk")
        col = Collection(name="Dune Chronicles")
        db.session.add_all([c1, c2, t1, t2, col])
        db.session.flush()

        b1 = Book(
            title="Dune",
            original_file_path="/tmp/dune.epub",
            file_format="epub",
            file_size=1000,
            file_hash="hash_dune_1",
            media_type="book",
            description="The desert planet Arrakis.",
            collection_id=col.id,
        )
        b1.creators.append(c1)
        b1.tags.append(t1)

        b2 = Book(
            title="Foundation and Empire",
            original_file_path="/tmp/foundation.epub",
            file_format="epub",
            file_size=1200,
            file_hash="hash_foundation_1",
            media_type="book",
            description="The Galactic Empire declines.",
        )
        b2.creators.append(c2)
        b2.tags.append(t1)

        b3 = Book(
            title="Neuromancer",
            original_file_path="/tmp/neuromancer.epub",
            file_format="epub",
            file_size=900,
            file_hash="hash_neuro_1",
            media_type="book",
            description="A matrix console cowboy.",
        )
        b3.tags.append(t2)

        db.session.add_all([b1, b2, b3])
        db.session.commit()

        # Rebuild FTS
        indexed_count = rebuild_search_index()
        assert indexed_count >= 3

        # Match title
        matches = search_media_ids("Dune")
        assert b1.id in matches
        assert b2.id not in matches

        # Match creator
        matches = search_media_ids("Asimov")
        assert b2.id in matches

        # Match collection/series
        matches = search_media_ids("Chronicles")
        assert b1.id in matches

        # Match tag
        matches = search_media_ids("Cyberpunk")
        assert b3.id in matches

        # Match description
        matches = search_media_ids("Arrakis")
        assert b1.id in matches


def test_bm25_relevance_ranking(app):
    """Verify that title matches rank ahead of description matches via BM25 weights."""
    with app.app_context():
        # Clear items
        db.session.execute(text("DELETE FROM media_creators;"))
        db.session.execute(text("DELETE FROM media_tags;"))
        db.session.execute(text("DELETE FROM media_items;"))
        db.session.commit()

        # Item A: "Matrix" is in description only
        b_desc = Book(
            title="A History of Virtual Worlds",
            original_file_path="/tmp/history.mp4",
            file_format="mp4",
            file_size=1000,
            file_hash="hash_hist_1",
            media_type="video",
            description="Explores simulation hypotheses and the Matrix film.",
        )
        # Item B: "Matrix" is in title
        b_title = Book(
            title="The Matrix",
            original_file_path="/tmp/matrix.mp4",
            file_format="mp4",
            file_size=2000,
            file_hash="hash_matrix_1",
            media_type="video",
            description="A computer hacker learns about true reality.",
        )
        db.session.add_all([b_desc, b_title])
        db.session.commit()

        rebuild_search_index()

        results = search_media_ids("Matrix")
        assert len(results) == 2
        # Title match should rank first
        assert results[0] == b_title.id
        assert results[1] == b_desc.id


def test_reactive_sync_and_removal(app):
    """Verify single-item reactive sync and removal keep FTS index consistent."""
    with app.app_context():
        b = Book(
            title="Old Forgotten Title",
            original_file_path="/tmp/reactive.epub",
            file_format="epub",
            file_size=500,
            file_hash="hash_react_1",
            media_type="book",
            description="Initial description",
        )
        db.session.add(b)
        db.session.commit()
        sync_media_item_fts(b.id)

        assert search_media_ids("Forgotten") == [b.id]

        # Update metadata and reactively sync
        b.title = "Brand New Quantum Computing"
        db.session.commit()
        sync_media_item_fts(b.id)

        # Old title should no longer match
        assert search_media_ids("Forgotten") == []
        # New title matches immediately
        assert search_media_ids("Quantum") == [b.id]

        # Remove from FTS
        remove_media_item_fts(b.id)
        assert search_media_ids("Quantum") == []


def test_search_grouped_api(client, app):
    """Verify /api/search returns categorized groups with counts and items."""
    with app.app_context():
        # Clear items
        db.session.execute(text("DELETE FROM media_creators;"))
        db.session.execute(text("DELETE FROM media_tags;"))
        db.session.execute(text("DELETE FROM media_items;"))
        db.session.commit()

        movie = Book(
            title="Star Wars Episode IV",
            original_file_path="/tmp/sw.mp4",
            file_format="mp4",
            file_size=5000,
            file_hash="hash_sw_mov",
            media_type="video",
        )
        book = Book(
            title="Star Wars: Heir to the Empire",
            original_file_path="/tmp/sw.epub",
            file_format="epub",
            file_size=800,
            file_hash="hash_sw_bk",
            media_type="book",
        )
        audiobook = Book(
            title="Star Wars Audiobook",
            original_file_path="/tmp/sw.m4b",
            file_format="m4b",
            file_size=3000,
            file_hash="hash_sw_ab",
            media_type="audiobook",
        )
        comic = Book(
            title="Star Wars Darth Vader Comic",
            original_file_path="/tmp/sw.cbz",
            file_format="cbz",
            file_size=1500,
            file_hash="hash_sw_cm",
            media_type="comic",
        )
        music = Book(
            title="Star Wars Imperial March",
            original_file_path="/tmp/sw.mp3",
            file_format="mp3",
            file_size=400,
            file_hash="hash_sw_mus",
            media_type="music",
        )

        db.session.add_all([movie, book, audiobook, comic, music])
        db.session.commit()
        rebuild_search_index()

    # Call /api/search
    res = client.get("/api/search?q=Star Wars")
    assert res.status_code == 200
    data = res.get_json()
    assert data["query"] == "Star Wars"
    assert data["total_results"] >= 5

    groups = data["groups"]
    assert "video" in groups and groups["video"]["count"] >= 1
    assert "book" in groups and groups["book"]["count"] >= 1
    assert "audiobook" in groups and groups["audiobook"]["count"] >= 1
    assert "comic" in groups and groups["comic"]["count"] >= 1
    assert "music" in groups and groups["music"]["count"] >= 1


def test_api_media_search_endpoint(client, app):
    """Verify /api/media with ?q= uses FTS5 and BM25 relevance sorting."""
    with app.app_context():
        # Clear items
        db.session.execute(text("DELETE FROM media_creators;"))
        db.session.execute(text("DELETE FROM media_tags;"))
        db.session.execute(text("DELETE FROM media_items;"))
        db.session.commit()

        b1 = Book(
            title="Solaris",
            original_file_path="/tmp/solaris.epub",
            file_format="epub",
            file_size=600,
            file_hash="hash_solaris",
            media_type="book",
            description="Ocean planet sci-fi classic.",
        )
        b2 = Book(
            title="Fiasco",
            original_file_path="/tmp/fiasco.epub",
            file_format="epub",
            file_size=700,
            file_hash="hash_fiasco",
            media_type="book",
            description="Mention of solaris ocean research.",
        )
        db.session.add_all([b1, b2])
        db.session.commit()
        rebuild_search_index()

    res = client.get("/api/media?q=Solaris")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["items"]) == 2
    # Title match ("Solaris") ranks before description match ("Fiasco")
    assert data["items"][0]["title"] == "Solaris"
    assert data["items"][1]["title"] == "Fiasco"


def test_opds_search_integration(client, app):
    """Verify /opds/search?q= returns OPDS Atom XML matching items."""
    with app.app_context():
        b = Book(
            title="OPDS Unique Galaxy Guide",
            original_file_path="/tmp/galaxy.epub",
            file_format="epub",
            file_size=500,
            file_hash="hash_galaxy",
            media_type="book",
        )
        db.session.add(b)
        db.session.commit()
        sync_media_item_fts(b.id)

    res = client.get("/opds/search?q=Galaxy")
    assert res.status_code == 200
    assert "application/atom+xml" in res.headers["Content-Type"]
    assert b"OPDS Unique Galaxy Guide" in res.data


def test_reindex_search_api_admin_required(client, app):
    """Verify POST /api/search/reindex requires admin permissions."""
    from aarkib.models import User

    # Unauthenticated attempt redirects or rejects
    res = client.post("/api/search/reindex")
    assert res.status_code in (302, 401, 403)

    # Standard non-admin user gets 403
    with app.app_context():
        user = User(username="regular_reader", is_admin=False)
        user.set_password("readerpass")
        admin = User(username="search_admin", is_admin=True)
        admin.set_password("adminpass")
        db.session.add_all([user, admin])
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "regular_reader", "password": "readerpass"},
        follow_redirects=True,
    )
    res_user = client.post("/api/search/reindex")
    assert res_user.status_code == 403

    client.get("/auth/logout", follow_redirects=True)

    # Admin user gets 200 OK
    client.post(
        "/auth/login",
        data={"username": "search_admin", "password": "adminpass"},
        follow_redirects=True,
    )

    res_admin = client.post("/api/search/reindex")
    assert res_admin.status_code == 200
    data = res_admin.get_json()
    assert data["status"] == "success"
    assert "indexed_count" in data


def test_cli_reindex_search_command(runner, app):
    """Verify 'reindex-search' Flask CLI command."""
    with app.app_context():
        b = Book(
            title="CLI Reindex Book",
            original_file_path="/tmp/cli_reindex.epub",
            file_format="epub",
            file_size=500,
            file_hash="hash_cli_reindex",
            media_type="book",
        )
        db.session.add(b)
        db.session.commit()

    res = runner.invoke(args=["reindex-search"])
    assert res.exit_code == 0
    assert "Search index rebuild complete" in res.output


def test_search_fallback_on_invalid_fts_syntax(app, monkeypatch):
    """Verify that when FTS5 query encounters an error, fallback to ILIKE works seamlessly."""
    with app.app_context():
        b = Book(
            title="Fallback Quantum Physics",
            original_file_path="/tmp/fallback.epub",
            file_format="epub",
            file_size=500,
            file_hash="hash_fallback",
            media_type="book",
            description="Quantum teleportation research.",
        )
        db.session.add(b)
        db.session.commit()

        # Call search_media_ids with a query, but monkeypatch execute to raise an error
        from aarkib.services import search

        orig_execute = db.session.execute

        def mock_execute(statement, *args, **kwargs):
            if "media_items_fts MATCH" in str(statement):
                raise RuntimeError("Simulated FTS5 syntax error")
            return orig_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db.session, "execute", mock_execute)

        # Should smoothly fallback to ILIKE and find the book
        results = search.search_media_ids("Quantum")
        assert b.id in results
