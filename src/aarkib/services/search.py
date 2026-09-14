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

FTS_SEARCH_SQL = text("""
SELECT fts.rowid, bm25(media_items_fts, 10.0, 5.0, 3.0, 1.0, 2.0) AS rank
FROM media_items_fts fts
JOIN media_items m ON m.id = fts.rowid
WHERE media_items_fts MATCH :match_query
  AND (:media_type IS NULL OR m.media_type = :media_type)
  AND (:library_id IS NULL OR m.library_id = :library_id)
ORDER BY rank ASC
LIMIT :limit;
""")

FTS_FACET_SQL = text("""
SELECT fts.rowid AS id, m.media_type, m.file_format,
       bm25(media_items_fts, 10.0, 5.0, 3.0, 1.0, 2.0) AS rank
FROM media_items_fts fts
JOIN media_items m ON m.id = fts.rowid
WHERE media_items_fts MATCH :match_query
  AND (:library_id IS NULL OR m.library_id = :library_id)
ORDER BY rank ASC
LIMIT 1000;
""")


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


QUALIFIER_PATTERN = re.compile(
    r'\b(author|creator|narrator|series|collection|album|tag|genre|title|desc|description|type):(?:"([^"]+)"|(\S+))',
    re.IGNORECASE,
)

FIELD_COLUMN_MAP = {
    "author": "creators",
    "creator": "creators",
    "narrator": "creators",
    "series": "collection",
    "collection": "collection",
    "album": "collection",
    "tag": "tags",
    "genre": "tags",
    "title": "title",
    "desc": "description",
    "description": "description",
}


def parse_fts_query_with_filters(raw_query: str) -> tuple[str, str | None]:
    """Parse raw query for SQLite FTS5, supporting field qualifiers.

    Recognizes qualifiers such as:
    - author:"Frank Herbert" or creator:Herbert -> creators : "Frank Herbert"
    - series:"The Expanse" or collection:Expanse -> collection : "The Expanse"
    - tag:scifi or genre:scifi -> tags : ("scifi"*)
    - title:"Dune" -> title : "Dune"
    - desc:"spice" -> description : ("spice"*)
    - type:book -> extracted media_type filter

    Returns (clean_fts_query, extracted_media_type).
    """
    if not raw_query:
        return "", None

    q = raw_query.strip()
    if not q:
        return "", None

    # Check for balanced explicit phrase search: e.g. "The Matrix" (without field qualifier)
    if q.startswith('"') and q.endswith('"') and len(q) > 1 and q.count('"') % 2 == 0:
        phrase = q.strip('"').replace('"', " ").strip()
        if phrase:
            return f'"{phrase}"', None

    extracted_media_type = None
    column_clauses: list[str] = []

    def _replace_qualifier(match: re.Match[str]) -> str:
        nonlocal extracted_media_type
        field_name = match.group(1).lower()
        quoted_val = match.group(2)
        unquoted_val = match.group(3)
        val = (quoted_val if quoted_val is not None else unquoted_val).strip()

        if field_name == "type":
            extracted_media_type = val.lower()
            return " "

        target_col = FIELD_COLUMN_MAP.get(field_name)
        if target_col and val:
            if quoted_val is not None:
                clean_val = val.replace('"', " ").strip()
                column_clauses.append(f'{target_col} : "{clean_val}"')
            else:
                sub_tokens = re.findall(r"[\w]+", val, re.UNICODE)
                if sub_tokens:
                    tok_str = " ".join(f'"{t}"*' for t in sub_tokens)
                    column_clauses.append(f"{target_col} : ({tok_str})")
        return " "

    remaining_text = QUALIFIER_PATTERN.sub(_replace_qualifier, q).strip()

    # Extract general words from remaining text
    general_tokens = re.findall(r"[\w]+", remaining_text, re.UNICODE)
    general_clauses = [f'"{tok}"*' for tok in general_tokens]

    all_clauses = column_clauses + general_clauses
    if not all_clauses:
        return "", extracted_media_type

    return " ".join(all_clauses), extracted_media_type


def parse_fts_query(raw_query: str) -> str:
    """Defensively parse, sanitize, and format user input for SQLite FTS5."""
    return parse_fts_query_with_filters(raw_query)[0]


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


def remove_batch_fts(item_ids: list[int], session: Session | None = None) -> None:
    """Remove specific item IDs from the FTS index."""
    if not item_ids:
        return
    sess = session or db.session
    try:
        chunk_size = 500
        for i in range(0, len(item_ids), chunk_size):
            chunk = item_ids[i : i + chunk_size]
            placeholders = ", ".join(f":id_{j}" for j in range(len(chunk)))
            params = {f"id_{j}": val for j, val in enumerate(chunk)}
            sess.execute(
                text(f"DELETE FROM media_items_fts WHERE rowid IN ({placeholders})"),
                params,
            )
        sess.commit()
    except Exception as e:
        logger.warning("Failed to remove FTS entries: %s", e)


def _search_media_ids_fallback_ilike(
    q: str,
    media_type: str | None = None,
    library_id: int | None = None,
    limit: int = 1000,
    session: Session | None = None,
) -> list[int]:
    """Fallback search using standard SQLAlchemy ILIKE queries if FTS is unavailable or fails."""
    from aarkib.models import Collection, Creator, MediaItem, Tag

    sess = session or db.session
    query = select(MediaItem.id)

    if media_type:
        query = query.where(MediaItem.media_type == media_type)
    if library_id:
        query = query.where(MediaItem.library_id == library_id)

    search_filter = or_(
        MediaItem.title.ilike(f"%{q}%"),
        MediaItem.description.ilike(f"%{q}%"),
        MediaItem.creators.any(Creator.name.ilike(f"%{q}%")),
        MediaItem.tags.any(Tag.name.ilike(f"%{q}%")),
        MediaItem.collection.has(Collection.name.ilike(f"%{q}%")),
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
    clean_query, extracted_type = parse_fts_query_with_filters(q)
    if not clean_query:
        return []

    effective_media_type = media_type or extracted_type
    sess = session or db.session
    try:
        rows = sess.execute(
            FTS_SEARCH_SQL,
            {
                "match_query": clean_query,
                "media_type": effective_media_type if effective_media_type else None,
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
            q,
            media_type=effective_media_type,
            library_id=library_id,
            limit=limit,
            session=sess,
        )


def serialize_media_item_summary(b: Any) -> dict[str, Any]:
    """Serialize a MediaItem model into a uniform dict for search results."""
    return {
        "id": b.id,
        "title": b.title,
        "media_type": b.media_type,
        "creators": [c.name for c in b.creators],
        "creators_display": b.creators_display,
        "file_format": b.file_format,
        "file_size": b.file_size,
        "cover_url": f"/api/media/{b.id}/cover",
        "player_url": b.player_url,
        "collection": b.collection.name if b.collection else None,
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
    from aarkib.models import MediaItem

    matching_entries: list[tuple[int, str, str]] = []  # (id, media_type, file_format)

    try:
        rows = sess.execute(
            FTS_FACET_SQL,
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
            items = sess.scalars(
                select(MediaItem).where(MediaItem.id.in_(fallback_ids))
            ).all()
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
        loaded_items = sess.scalars(
            select(MediaItem)
            .options(
                selectinload(MediaItem.creators),
                selectinload(MediaItem.collection),
                selectinload(MediaItem.tags),
            )
            .where(MediaItem.id.in_(needed_ids))
        ).all()
        items_map = {b.id: b for b in loaded_items}

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
