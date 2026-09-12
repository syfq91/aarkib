"""SQLite FTS5 Full-Text Search Service for Aarkib.

Provides sub-millisecond catalog search across titles, creators, collections,
descriptions, and tags, powered by a standalone SQLite FTS5 virtual table with
BM25 relevance ranking and defensive query tokenization/sanitization.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session, selectinload

from aarkib.extensions import db

logger = logging.getLogger(__name__)

# BM25 Column Weights: (title, creators, collection, description, tags)
# Higher weight boosts match relevance: Title (10x) > Creators (5x) > Collection (3x) > Tags (2x) > Description (1x)
BM25_WEIGHTS_SQL = "10.0, 5.0, 3.0, 1.0, 2.0"

MEDIA_GROUPS = [
    {"key": "video", "label": "Movies & TV Shows", "icon": "🎬"},
    {"key": "book", "label": "Books", "icon": "📚"},
    {"key": "audiobook", "label": "Audiobooks", "icon": "🎧"},
    {"key": "podcast", "label": "Podcasts & Shows", "icon": "🎙️"},
    {"key": "comic", "label": "Comics & Manga", "icon": "🎨"},
    {"key": "music", "label": "Music", "icon": "🎵"},
]

CREATE_FTS_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS media_items_fts USING fts5(
    title,
    creators,
    collection,
    description,
    tags,
    tokenize='porter unicode61'
);
"""

FTS_INSERT_REPLACE_SQL = """
INSERT OR REPLACE INTO media_items_fts(rowid, title, creators, collection, description, tags)
SELECT
    m.id,
    COALESCE(m.title, ''),
    COALESCE((
        SELECT GROUP_CONCAT(c.name, ' ')
        FROM creators c
        JOIN media_creators mc ON mc.creator_id = c.id
        WHERE mc.media_item_id = m.id
    ), ''),
    COALESCE((
        SELECT col.name
        FROM collections col
        WHERE col.id = m.collection_id
    ), ''),
    COALESCE(m.description, ''),
    COALESCE((
        SELECT GROUP_CONCAT(t.name, ' ')
        FROM tags t
        JOIN media_tags mt ON mt.tag_id = t.id
        WHERE mt.media_item_id = m.id
    ), '')
FROM media_items m
"""


def init_search_fts(conn_or_session: Any = None) -> bool:
    """Initialize the media_items_fts virtual table if it does not exist.

    Returns True if FTS5 is available and initialized, False otherwise.
    """
    try:
        if conn_or_session is not None and hasattr(conn_or_session, "execute"):
            conn_or_session.execute(text(CREATE_FTS_TABLE_SQL))
        else:
            with db.engine.begin() as conn:
                conn.execute(text(CREATE_FTS_TABLE_SQL))
        return True
    except Exception as exc:
        logger.warning(
            "FTS5 table initialization failed (FTS5 may be unavailable): %s", exc
        )
        return False


def parse_fts_query(raw_query: str) -> str:
    """Defensively parse, sanitize, and format user input for SQLite FTS5.

    - Handles unbalanced quotes, special syntax chars (: ^ * ( ) { } [ ] - +).
    - Preserves exact double-quoted phrases when balanced.
    - Adds prefix matching wildcard (*) to word tokens for instant typeahead search.
    - Double-quotes each token to treat boolean operators (AND, OR, NOT) as literal words.
    """
    if not raw_query:
        return ""

    q = raw_query.strip()
    if not q:
        return ""

    # Check for balanced explicit phrase search: e.g. "The Matrix"
    if q.startswith('"') and q.endswith('"') and len(q) > 1 and q.count('"') % 2 == 0:
        phrase = q.strip('"').replace('"', " ").strip()
        if phrase:
            return f'"{phrase}"'

    # Extract alphanumeric tokens (including unicode / non-ASCII words)
    tokens = re.findall(r"[\w]+", q, re.UNICODE)
    if not tokens:
        return ""

    # Quote each token and append wildcard suffix for prefix typeahead matching
    cleaned = [f'"{tok}"*' for tok in tokens]
    return " ".join(cleaned)


def sync_media_item_fts(item_id: int, session: Session | None = None) -> None:
    """Synchronously insert or update a single media item in media_items_fts."""
    sess = session or db.session
    try:
        sql = f"{FTS_INSERT_REPLACE_SQL} WHERE m.id = :item_id;"
        sess.execute(text(sql), {"item_id": item_id})
        sess.commit()
    except Exception as exc:
        logger.warning("Failed to sync item %d to FTS index: %s", item_id, exc)


def remove_media_item_fts(item_id: int, session: Session | None = None) -> None:
    """Remove a media item from media_items_fts by rowid."""
    sess = session or db.session
    try:
        sess.execute(
            text("DELETE FROM media_items_fts WHERE rowid = :item_id;"),
            {"item_id": item_id},
        )
        sess.commit()
    except Exception as exc:
        logger.warning("Failed to delete item %d from FTS index: %s", item_id, exc)


def rebuild_search_index(session: Session | None = None) -> int:
    """Completely rebuild the media_items_fts table from the media_items catalog.

    Returns the count of indexed items.
    """
    sess = session or db.session
    try:
        init_search_fts(sess)
        sess.execute(text("DELETE FROM media_items_fts;"))
        sess.execute(text(FTS_INSERT_REPLACE_SQL + ";"))
        sess.commit()
        count = sess.scalar(text("SELECT count(*) FROM media_items_fts;")) or 0
        logger.info("Rebuilt media_items_fts index with %d items.", count)
        return int(count)
    except Exception as exc:
        logger.warning("Failed to rebuild FTS index: %s", exc)
        return 0


def sync_batch_fts(item_ids: list[int], session: Session | None = None) -> None:
    """Batch-synchronize a specific collection of mutated item IDs."""
    if not item_ids:
        return
    sess = session or db.session
    try:
        # Chunk into batches of 500 to respect SQLite expression limits
        chunk_size = 500
        for i in range(0, len(item_ids), chunk_size):
            chunk = item_ids[i : i + chunk_size]
            placeholders = ", ".join(f":id_{j}" for j in range(len(chunk)))
            params = {f"id_{j}": val for j, val in enumerate(chunk)}
            sql = f"{FTS_INSERT_REPLACE_SQL} WHERE m.id IN ({placeholders});"
            sess.execute(text(sql), params)
        sess.commit()
    except Exception as exc:
        logger.warning("Failed to batch sync FTS items: %s", exc)


def _search_media_ids_fallback_ilike(
    q: str,
    media_type: str | None = None,
    library_id: int | None = None,
    limit: int = 1000,
    session: Session | None = None,
) -> list[int]:
    """Fallback search using standard SQLAlchemy ILIKE queries if FTS is unavailable or fails."""
    from aarkib.models import Author, Book, Series, Tag

    sess = session or db.session
    query = select(Book.id)

    if media_type:
        query = query.where(Book.media_type == media_type)
    if library_id:
        query = query.where(Book.library_id == library_id)

    search_filter = or_(
        Book.title.ilike(f"%{q}%"),
        Book.description.ilike(f"%{q}%"),
        Book.authors.any(Author.name.ilike(f"%{q}%")),
        Book.tags.any(Tag.name.ilike(f"%{q}%")),
        Book.series.has(Series.name.ilike(f"%{q}%")),
    )
    query = query.where(search_filter).limit(limit)
    return list(sess.scalars(query).all())


def search_media_ids(
    q: str,
    media_type: str | None = None,
    library_id: int | None = None,
    limit: int = 1000,
    session: Session | None = None,
) -> list[int]:
    """Execute FTS5 search and return matching media item IDs ordered by BM25 relevance.

    Falls back to SQL ILIKE search on any FTS5 syntax or database exception.
    """
    clean_query = parse_fts_query(q)
    if not clean_query:
        return []

    sess = session or db.session
    try:
        sql = f"""
        SELECT fts.rowid, bm25(media_items_fts, {BM25_WEIGHTS_SQL}) AS rank
        FROM media_items_fts fts
        JOIN media_items m ON m.id = fts.rowid
        WHERE media_items_fts MATCH :match_query
          AND (:media_type IS NULL OR m.media_type = :media_type)
          AND (:library_id IS NULL OR m.library_id = :library_id)
        ORDER BY rank ASC
        LIMIT :limit;
        """
        rows = sess.execute(
            text(sql),
            {
                "match_query": clean_query,
                "media_type": media_type if media_type else None,
                "library_id": library_id if library_id else None,
                "limit": limit,
            },
        ).fetchall()
        return [int(row[0]) for row in rows]
    except Exception as exc:
        logger.debug(
            "FTS5 query '%s' failed, falling back to ILIKE: %s", clean_query, exc
        )
        return _search_media_ids_fallback_ilike(
            q, media_type=media_type, library_id=library_id, limit=limit, session=sess
        )


def serialize_media_item_summary(b: Any) -> dict[str, Any]:
    """Serialize a MediaItem/Book model into a uniform dict for search results."""
    return {
        "id": b.id,
        "title": b.title,
        "media_type": b.media_type,
        "authors": [a.name for a in b.authors],
        "authors_display": b.authors_display,
        "file_format": b.file_format,
        "file_size": b.file_size,
        "cover_url": f"/api/media/{b.id}/cover",
        "player_url": b.player_url,
        "series": b.series.name if b.series else None,
        "series_index": b.series_index,
        "tags": [t.name for t in b.tags],
        "duration": getattr(b, "duration", None),
        "season": getattr(b, "season", None),
        "episode": getattr(b, "episode", None),
        "narrator": getattr(b, "narrator", None),
        "album": getattr(b, "album", None),
        "album_artist": getattr(b, "album_artist", None),
        "release_year": getattr(b, "release_year", None),
    }


def search_grouped(
    q: str,
    library_id: int | None = None,
    limit_per_group: int = 8,
    session: Session | None = None,
) -> dict[str, Any]:
    """Search the catalog and return results grouped by media category with counts and top items."""
    clean_query = parse_fts_query(q)
    if not clean_query:
        return {
            "query": q,
            "total_results": 0,
            "groups": {
                grp["key"]: {
                    "label": grp["label"],
                    "icon": grp["icon"],
                    "count": 0,
                    "items": [],
                }
                for grp in MEDIA_GROUPS
            },
        }

    sess = session or db.session
    from aarkib.models import Book

    matching_entries: list[tuple[int, str, str]] = []  # (id, media_type, file_format)

    try:
        sql = f"""
        SELECT fts.rowid AS id, m.media_type, m.file_format,
               bm25(media_items_fts, {BM25_WEIGHTS_SQL}) AS rank
        FROM media_items_fts fts
        JOIN media_items m ON m.id = fts.rowid
        WHERE media_items_fts MATCH :match_query
          AND (:library_id IS NULL OR m.library_id = :library_id)
        ORDER BY rank ASC
        LIMIT 1000;
        """
        rows = sess.execute(
            text(sql),
            {
                "match_query": clean_query,
                "library_id": library_id if library_id else None,
            },
        ).fetchall()
        matching_entries = [
            (int(r[0]), str(r[1] or "book").lower(), str(r[2] or "").lower())
            for r in rows
        ]
    except Exception as exc:
        logger.debug(
            "FTS5 grouped query '%s' failed, falling back: %s", clean_query, exc
        )
        fallback_ids = _search_media_ids_fallback_ilike(
            q, library_id=library_id, session=sess
        )
        if fallback_ids:
            items = sess.scalars(select(Book).where(Book.id.in_(fallback_ids))).all()
            matching_entries = [
                (
                    b.id,
                    str(b.media_type or "book").lower(),
                    str(b.file_format or "").lower(),
                )
                for b in items
            ]

    # Map raw media_type/format into standard display categories
    # Groups: video, book, audiobook, comic, music
    grouped_ids: dict[str, list[int]] = {grp["key"]: [] for grp in MEDIA_GROUPS}

    for item_id, m_type, fmt in matching_entries:
        if m_type == "video":
            target_key = "video"
        elif m_type == "comic" or fmt in ("cbz", "cbr"):
            target_key = "comic"
        elif m_type == "audiobook" or fmt == "m4b":
            target_key = "audiobook"
        elif m_type == "podcast":
            target_key = "podcast"
        elif m_type == "music" or (m_type == "audio" and fmt != "m4b"):
            target_key = "music"
        else:
            target_key = "book"

        if target_key in grouped_ids:
            grouped_ids[target_key].append(item_id)

    # Collect top IDs across all categories to batch load in single DB query
    needed_ids: list[int] = []
    for grp in MEDIA_GROUPS:
        key = grp["key"]
        needed_ids.extend(grouped_ids[key][:limit_per_group])

    items_map: dict[int, Any] = {}
    if needed_ids:
        loaded_books = sess.scalars(
            select(Book)
            .options(
                selectinload(Book.authors),
                selectinload(Book.series),
                selectinload(Book.tags),
            )
            .where(Book.id.in_(needed_ids))
        ).all()
        items_map = {b.id: b for b in loaded_books}

    result_groups: dict[str, dict[str, Any]] = {}
    total_matches = 0

    for grp in MEDIA_GROUPS:
        key = grp["key"]
        ids = grouped_ids[key]
        count = len(ids)
        total_matches += count

        top_items = []
        for mid in ids[:limit_per_group]:
            if mid in items_map:
                top_items.append(serialize_media_item_summary(items_map[mid]))

        result_groups[key] = {
            "label": grp["label"],
            "icon": grp["icon"],
            "count": count,
            "items": top_items,
        }

    return {
        "query": q,
        "total_results": total_matches,
        "groups": result_groups,
    }
