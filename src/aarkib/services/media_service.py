"""Service layer for catalog operations across all media types.

Centralizes reusable business logic so route handlers stay thin and the
creator/series/tag entity resolution is not duplicated across the API and scanner.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import selectinload

from aarkib.extensions import db
from aarkib.models import (
    Collection,
    Creator,
    Library,
    MediaItem,
    MetadataSource,
    Tag,
    UserFavorite,
    UserProgress,
    media_creators,
    media_tags,
)

AUDIOBOOK_EXTENSIONS = frozenset({"m4b"})
MUSIC_EXTENSIONS = frozenset({"mp3", "m4a", "flac", "ogg", "opus", "wav", "aac"})
AUDIO_EXTENSIONS = AUDIOBOOK_EXTENSIONS | MUSIC_EXTENSIONS
VIDEO_EXTENSIONS = frozenset({"mp4", "mkv", "webm", "avi", "mov", "m4v"})
BOOK_EXTENSIONS = frozenset({"epub", "cbz", "cbr", "zip", "pdf", "mobi", "azw3"})
MEDIA_TYPE_CHOICES = frozenset(
    {
        "all",
        "book",
        "comic",
        "video",
        "movie",
        "tv",
        "audio",
        "audiobook",
        "music",
        "podcast",
    }
)

MAX_TITLE_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 50000
MAX_PUBLISHER_LENGTH = 255
MAX_LANGUAGE_LENGTH = 30
MAX_ISBN_LENGTH = 50


def resolve_or_create_creators(names: list[str]) -> list[Creator]:
    """Look up (or create) Creator records for the given names and return them.

    Expected to be called inside an active DB session; callers are responsible
    for flushing/committing.
    """
    creator_objs: list[Creator] = []
    for name in names:
        cleaned_name = str(name).strip()
        if not cleaned_name:
            continue
        creator = db.session.scalar(select(Creator).where(Creator.name == cleaned_name))
        if not creator:
            creator = Creator(name=cleaned_name)
            db.session.add(creator)
        creator_objs.append(creator)
    return creator_objs


def resolve_or_create_collections(name: str) -> Collection:
    """Look up (or create) a Collection record by name and return it."""
    cleaned = name.strip()
    collection_obj = db.session.scalar(
        select(Collection).where(Collection.name == cleaned)
    )
    if not collection_obj:
        collection_obj = Collection(name=cleaned)
        db.session.add(collection_obj)
    return collection_obj


def resolve_or_create_tags(names: list[str]) -> list[Tag]:
    """Look up (or create) Tag records for the given names and return them."""
    tag_objs: list[Tag] = []
    for name in names:
        cleaned = str(name).strip().title()
        if not cleaned:
            continue
        tag_obj = db.session.scalar(select(Tag).where(Tag.name == cleaned))
        if not tag_obj:
            tag_obj = Tag(name=cleaned)
            db.session.add(tag_obj)
        tag_objs.append(tag_obj)
    return tag_objs


def edit_media_metadata(item: MediaItem, data: dict) -> MediaItem:
    """Apply user-supplied metadata edits to a media item.

    Handles title, authors/creators, series/collection, tags, descriptive fields,
    video episode codes, and audio track attributes.
    Returns the updated item; caller is responsible for committing.
    """
    modified_fields: list[str] = []

    title = data.get("title")
    if title:
        item.title = str(title).strip()[:MAX_TITLE_LENGTH]
        modified_fields.append("title")
        if hasattr(item, "set_field_provenance"):
            item.set_field_provenance(
                "title",
                "user",
                source_type=MetadataSource.MANUAL,
                confidence=1.0,
                value=item.title,
            )

    # Creators
    creators_input = data.get("creators") or data.get("authors")
    if creators_input is not None:
        if isinstance(creators_input, str):
            creator_names = [c.strip() for c in creators_input.split(",") if c.strip()]
        else:
            creator_names = [c for c in creators_input if c and str(c).strip()]
        resolved_creators = resolve_or_create_creators(creator_names)
        if resolved_creators:
            item.creators = resolved_creators
            modified_fields.append("creators")
            if hasattr(item, "set_field_provenance"):
                item.set_field_provenance(
                    "creators",
                    "user",
                    source_type=MetadataSource.MANUAL,
                    confidence=1.0,
                    value=creator_names,
                )

    # Collection
    collection_name = data.get("collection") or data.get("series")
    series_index_raw = data.get("series_index")
    if collection_name is not None and str(collection_name).strip():
        collection_obj = resolve_or_create_collections(str(collection_name))
        item.collection = collection_obj
        if series_index_raw not in (None, ""):
            try:
                item.series_index = float(series_index_raw)
            except ValueError:
                pass
        else:
            item.series_index = None
        modified_fields.append("collection")
        if hasattr(item, "set_field_provenance"):
            item.set_field_provenance(
                "collection",
                "user",
                source_type=MetadataSource.MANUAL,
                confidence=1.0,
                value=str(collection_name),
            )
    elif "series" in data or "collection" in data:
        item.collection = None
        item.series_index = None
        modified_fields.append("collection")
        if hasattr(item, "set_field_provenance"):
            item.set_field_provenance(
                "collection",
                "user",
                source_type=MetadataSource.MANUAL,
                confidence=1.0,
                value=None,
            )

    # Tags / Categories
    tags_input = data.get("tags")
    if tags_input is not None:
        if isinstance(tags_input, str):
            tag_names = [t.strip() for t in tags_input.split(",") if t.strip()]
        else:
            tag_names = [t for t in tags_input if t and str(t).strip()]
        resolved_tags = resolve_or_create_tags(tag_names)
        if resolved_tags:
            item.tags = resolved_tags
            modified_fields.append("tags")
            if hasattr(item, "set_field_provenance"):
                item.set_field_provenance(
                    "tags",
                    "user",
                    source_type=MetadataSource.MANUAL,
                    confidence=1.0,
                    value=tag_names,
                )

    # Optional descriptive fields
    if "description" in data:
        description = data.get("description") or None
        if description:
            description = str(description)[:MAX_DESCRIPTION_LENGTH]
        item.description = description
        modified_fields.append("description")
        if hasattr(item, "set_field_provenance"):
            item.set_field_provenance(
                "description",
                "user",
                source_type=MetadataSource.MANUAL,
                confidence=1.0,
                value=description,
            )
    if "publisher" in data:
        publisher = data.get("publisher") or None
        if publisher:
            publisher = str(publisher).strip()[:MAX_PUBLISHER_LENGTH]
        item.publisher = publisher
        modified_fields.append("publisher")
        if hasattr(item, "set_field_provenance"):
            item.set_field_provenance(
                "publisher",
                "user",
                source_type=MetadataSource.MANUAL,
                confidence=1.0,
                value=publisher,
            )
    if "publication_date" in data:
        item.publication_date = data.get("publication_date") or None
        modified_fields.append("publication_date")
        if hasattr(item, "set_field_provenance"):
            item.set_field_provenance(
                "publication_date",
                "user",
                source_type=MetadataSource.MANUAL,
                confidence=1.0,
                value=item.publication_date,
            )
    if "isbn" in data:
        isbn = data.get("isbn") or None
        if isbn:
            isbn = str(isbn).strip()[:MAX_ISBN_LENGTH]
        item.isbn = isbn
        modified_fields.append("isbn")
        if hasattr(item, "set_field_provenance"):
            item.set_field_provenance(
                "isbn",
                "user",
                source_type=MetadataSource.MANUAL,
                confidence=1.0,
                value=isbn,
            )
    if "language" in data:
        language = data.get("language")
        if language:
            language = str(language).strip()[:MAX_LANGUAGE_LENGTH]
        item.language = language or "en"
        modified_fields.append("language")
        if hasattr(item, "set_field_provenance"):
            item.set_field_provenance(
                "language",
                "user",
                source_type=MetadataSource.MANUAL,
                confidence=1.0,
                value=item.language,
            )

    # Video-specific metadata
    if "season" in data:
        try:
            item.season = int(data["season"]) if data["season"] is not None else None
            modified_fields.append("season")
            if hasattr(item, "set_field_provenance"):
                item.set_field_provenance(
                    "season",
                    "user",
                    source_type=MetadataSource.MANUAL,
                    confidence=1.0,
                    value=item.season,
                )
        except ValueError, TypeError:
            pass
    if "episode" in data:
        try:
            item.episode = int(data["episode"]) if data["episode"] is not None else None
            modified_fields.append("episode")
            if hasattr(item, "set_field_provenance"):
                item.set_field_provenance(
                    "episode",
                    "user",
                    source_type=MetadataSource.MANUAL,
                    confidence=1.0,
                    value=item.episode,
                )
        except ValueError, TypeError:
            pass

    # Audio-specific metadata
    if "album" in data:
        album = data.get("album") or None
        if album:
            album = str(album).strip()[:MAX_PUBLISHER_LENGTH]
        item.album = album
        modified_fields.append("album")
        if hasattr(item, "set_field_provenance"):
            item.set_field_provenance(
                "album",
                "user",
                source_type=MetadataSource.MANUAL,
                confidence=1.0,
                value=album,
            )
    if "track_number" in data:
        try:
            item.track_number = (
                int(data["track_number"]) if data["track_number"] is not None else None
            )
            modified_fields.append("track_number")
            if hasattr(item, "set_field_provenance"):
                item.set_field_provenance(
                    "track_number",
                    "user",
                    source_type=MetadataSource.MANUAL,
                    confidence=1.0,
                    value=item.track_number,
                )
        except ValueError, TypeError:
            pass
    if "disc_number" in data:
        try:
            item.disc_number = (
                int(data["disc_number"]) if data["disc_number"] is not None else None
            )
            modified_fields.append("disc_number")
            if hasattr(item, "set_field_provenance"):
                item.set_field_provenance(
                    "disc_number",
                    "user",
                    source_type=MetadataSource.MANUAL,
                    confidence=1.0,
                    value=item.disc_number,
                )
        except ValueError, TypeError:
            pass

    # Field locking & Protection Invariant:
    # If the user explicitly provided locked_fields, apply that list.
    # Otherwise, automatically lock all fields that were modified by the user.
    if "locked_fields" in data:
        raw_locks = data["locked_fields"]
        if isinstance(raw_locks, list):
            item.set_locked_fields(raw_locks)
        elif isinstance(raw_locks, str):
            item.set_locked_fields(
                [x.strip() for x in raw_locks.split(",") if x.strip()]
            )
    else:
        for f in modified_fields:
            item.lock_field(f)

    return item


def generate_slug(name: str) -> str:
    """Generate a URL-safe, unique slug from a library folder name."""
    base = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-") or "media"
    slug = base
    counter = 1
    all_slugs = set(db.session.scalars(select(Library.slug)).all())
    while slug in all_slugs:
        counter += 1
        slug = f"{base}-{counter}"
    return slug


def resolve_library(identifier) -> Library:
    """Look up a Library by integer ID or string slug; raises KeyError if absent."""
    if str(identifier).isdigit():
        lib = db.session.get(Library, int(identifier))
    else:
        lib = db.session.scalar(select(Library).where(Library.slug == identifier))
    if not lib:
        raise KeyError("Library not found")
    return lib


def path_prefixes(path: str | Path) -> tuple[str, str]:
    """Return (resolved_prefix, raw_prefix) for prefix-matching a filesystem path.

    Used to match catalog item paths that fall within a configured library
    folder while tolerating symlinked / non-normalized storage paths.
    """
    p = Path(path).expanduser()
    try:
        p_res = str(p.resolve()).rstrip("/\\") + "/"
    except Exception:
        p_res = str(p).rstrip("/\\") + "/"
    p_raw = str(p).rstrip("/\\") + "/"
    return p_res, p_raw


def library_path_conditions(library: Library) -> tuple[str, str]:
    """Return (resolved_prefix, raw_prefix) for prefix matching a library path."""
    return path_prefixes(library.path)


def path_match_filter(path: str | Path, library: Library | None = None):
    """Return SQLAlchemy OR conditions matching items within a library path."""
    p_res, p_raw = path_prefixes(path)
    conditions = [
        MediaItem.original_file_path.startswith(p_res),
        MediaItem.original_file_path.startswith(p_raw),
    ]
    if library and getattr(library, "id", None):
        conditions.append(MediaItem.library_id == library.id)
    return or_(*conditions)


def count_media_in_library(library: Library) -> int:
    """Count catalog items indexed under a library folder."""
    p_res, p_raw = library_path_conditions(library)
    conditions = [
        MediaItem.original_file_path.startswith(p_res),
        MediaItem.original_file_path.startswith(p_raw),
    ]
    if getattr(library, "id", None):
        conditions.append(MediaItem.library_id == library.id)
    return (
        db.session.scalar(select(func.count(MediaItem.id)).where(or_(*conditions))) or 0
    )


# ---------------------------------------------------------------------------
# Taxonomy Queries (Creators, Collections, Tags)
# ---------------------------------------------------------------------------


def list_creators_service(
    media_type: str | None = None,
    query: str | None = None,
    page: int = 1,
    per_page: int = 24,
    sort_by: str = "name",
) -> dict:
    """List creators / authors with aggregated media item counts and filtering."""
    q = (
        select(
            Creator.id,
            Creator.name,
            func.count(media_creators.c.media_item_id).label("media_count"),
        )
        .join(media_creators, Creator.id == media_creators.c.creator_id)
        .join(MediaItem, media_creators.c.media_item_id == MediaItem.id)
        .group_by(Creator.id, Creator.name)
    )

    if query:
        q = q.where(Creator.name.ilike(f"%{query.strip()}%"))

    if media_type and media_type != "all":
        if media_type == "audio":
            q = q.where(
                MediaItem.media_type.in_(["audio", "audiobook", "music", "podcast"])
            )
        elif media_type == "book":
            q = q.where(MediaItem.media_type.in_(["book", "comic"]))
        elif media_type == "video":
            q = q.where(MediaItem.media_type.in_(["video", "movie", "tv"]))
        else:
            q = q.where(MediaItem.media_type == media_type)

    if sort_by == "count":
        q = q.order_by(desc("media_count"), asc(Creator.name))
    else:
        q = q.order_by(asc(Creator.name))

    total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    offset = max(0, (page - 1) * per_page)
    rows = db.session.execute(q.offset(offset).limit(per_page)).all()
    items = [{"id": row[0], "name": row[1], "media_count": row[2]} for row in rows]

    return {
        "creators": items,
        "page": page,
        "pages": pages,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


def get_creator_detail_service(creator_id: int) -> dict | None:
    """Retrieve details and media items for a specific creator."""
    creator = db.session.get(Creator, creator_id)
    if not creator:
        return None

    items_stmt = (
        select(MediaItem)
        .join(media_creators, MediaItem.id == media_creators.c.media_item_id)
        .where(media_creators.c.creator_id == creator_id)
        .order_by(desc(MediaItem.created_at))
    )
    items = db.session.scalars(items_stmt).all()

    return {
        "id": creator.id,
        "name": creator.name,
        "media_count": len(items),
        "items": [
            {
                "id": m.id,
                "title": m.title,
                "media_type": m.media_type,
                "file_format": m.file_format,
                "cover_url": f"/api/media/{m.id}/cover",
                "player_url": m.player_url,
                "duration": m.duration,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in items
        ],
    }


def list_collections_service(
    media_type: str | None = None,
    query: str | None = None,
    page: int = 1,
    per_page: int = 24,
    sort_by: str = "name",
) -> dict:
    """List collections / series with aggregated media item counts."""
    q = (
        select(
            Collection.id,
            Collection.name,
            func.count(MediaItem.id).label("media_count"),
        )
        .join(MediaItem, Collection.id == MediaItem.collection_id)
        .group_by(Collection.id, Collection.name)
    )

    if query:
        q = q.where(Collection.name.ilike(f"%{query.strip()}%"))

    if media_type and media_type != "all":
        if media_type == "audio":
            q = q.where(
                MediaItem.media_type.in_(["audio", "audiobook", "music", "podcast"])
            )
        elif media_type == "book":
            q = q.where(MediaItem.media_type.in_(["book", "comic"]))
        elif media_type == "video":
            q = q.where(MediaItem.media_type.in_(["video", "movie", "tv"]))
        else:
            q = q.where(MediaItem.media_type == media_type)

    if sort_by == "count":
        q = q.order_by(desc("media_count"), asc(Collection.name))
    else:
        q = q.order_by(asc(Collection.name))

    total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    offset = max(0, (page - 1) * per_page)
    rows = db.session.execute(q.offset(offset).limit(per_page)).all()
    items = [{"id": row[0], "name": row[1], "media_count": row[2]} for row in rows]

    return {
        "collections": items,
        "page": page,
        "pages": pages,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


def get_collection_detail_service(collection_id: int) -> dict | None:
    """Retrieve details and ordered items for a specific collection."""
    collection = db.session.get(Collection, collection_id)
    if not collection:
        return None

    items_stmt = (
        select(MediaItem)
        .where(MediaItem.collection_id == collection_id)
        .order_by(
            asc(MediaItem.season),
            asc(MediaItem.episode),
            asc(MediaItem.series_index),
            asc(MediaItem.title),
        )
    )
    items = db.session.scalars(items_stmt).all()

    return {
        "id": collection.id,
        "name": collection.name,
        "media_count": len(items),
        "items": [
            {
                "id": m.id,
                "title": m.title,
                "media_type": m.media_type,
                "file_format": m.file_format,
                "season": m.season,
                "episode": m.episode,
                "series_index": m.series_index,
                "cover_url": f"/api/media/{m.id}/cover",
                "player_url": m.player_url,
                "duration": m.duration,
            }
            for m in items
        ],
    }


def list_tags_service(
    query: str | None = None, page: int = 1, per_page: int = 50
) -> dict:
    """List tags / genres with aggregated media item counts."""
    q = (
        select(
            Tag.id,
            Tag.name,
            func.count(media_tags.c.media_item_id).label("media_count"),
        )
        .join(media_tags, Tag.id == media_tags.c.tag_id)
        .group_by(Tag.id, Tag.name)
        .order_by(asc(Tag.name))
    )

    if query:
        q = q.where(Tag.name.ilike(f"%{query.strip()}%"))

    total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
    pages = (total + per_page - 1) // per_page if total > 0 else 1
    offset = max(0, (page - 1) * per_page)
    rows = db.session.execute(q.offset(offset).limit(per_page)).all()
    items = [{"id": row[0], "name": row[1], "media_count": row[2]} for row in rows]

    return {
        "tags": items,
        "page": page,
        "pages": pages,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


# ---------------------------------------------------------------------------
# Home Feed Aggregation (Mobile & TV Dashboards)
# ---------------------------------------------------------------------------


def get_home_feed_service(user_id: int | None) -> dict:
    """Aggregate personalized rails for native mobile and TV home screens."""
    fav_item_ids: set[int] = set()
    if user_id:
        fav_item_ids = set(
            db.session.scalars(
                select(UserFavorite.media_item_id).where(
                    UserFavorite.user_id == user_id
                )
            ).all()
        )

    def _serialize_item(
        item: MediaItem, progress_rec: UserProgress | None = None
    ) -> dict:
        data = {
            "id": item.id,
            "title": item.title,
            "media_type": item.media_type,
            "file_format": item.file_format,
            "creators": [a.name for a in item.creators],
            "creators_display": item.creators_display,
            "cover_url": f"/api/media/{item.id}/cover",
            "player_url": item.player_url,
            "duration": item.duration,
            "season": item.season,
            "episode": item.episode,
            "series_index": item.series_index,
            "collection": item.collection.name if item.collection else None,
            "collection_id": item.collection_id,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "is_favorite": item.id in fav_item_ids,
        }
        if progress_rec:
            data["progress"] = {
                "percentage": progress_rec.percentage,
                "progress": progress_rec.percentage,
                "location": progress_rec.progress_location,
                "is_completed": progress_rec.is_completed,
                "last_accessed_at": (
                    progress_rec.last_accessed_at.isoformat()
                    if progress_rec.last_accessed_at
                    else None
                ),
            }
        return data

    continue_watching: list[dict] = []
    continue_reading: list[dict] = []
    continue_listening: list[dict] = []
    next_up: list[dict] = []

    if user_id:
        # 1. In-progress items
        progress_stmt = (
            select(UserProgress, MediaItem)
            .join(MediaItem, UserProgress.media_item_id == MediaItem.id)
            .options(
                selectinload(MediaItem.creators),
                selectinload(MediaItem.collection),
            )
            .where(
                UserProgress.user_id == user_id,
                UserProgress.is_completed.is_(False),
                UserProgress.percentage > 0,
            )
            .order_by(desc(UserProgress.last_accessed_at))
            .limit(40)
        )
        records = db.session.execute(progress_stmt).all()

        for prog, m in records:
            if m.media_type in ("video", "movie", "tv") and prog.percentage < 90:
                if len(continue_watching) < 12:
                    continue_watching.append(_serialize_item(m, prog))
            elif m.media_type in ("book", "comic") and prog.percentage < 100:
                if len(continue_reading) < 12:
                    continue_reading.append(_serialize_item(m, prog))
            elif (
                m.media_type in ("audio", "audiobook", "music", "podcast")
                and prog.percentage < 95
            ):
                if len(continue_listening) < 12:
                    continue_listening.append(_serialize_item(m, prog))

        # 2. Next Up for TV Shows: find next unwatched episode in in-progress shows
        watched_episodes_stmt = (
            select(MediaItem)
            .join(UserProgress, MediaItem.id == UserProgress.media_item_id)
            .where(
                UserProgress.user_id == user_id,
                MediaItem.collection_id.is_not(None),
                MediaItem.episode.is_not(None),
            )
            .order_by(desc(UserProgress.last_accessed_at))
            .limit(20)
        )
        watched_episodes = db.session.scalars(watched_episodes_stmt).all()
        seen_collections: set[int] = set()

        for ep in watched_episodes:
            if not ep.collection_id or ep.collection_id in seen_collections:
                continue
            seen_collections.add(ep.collection_id)

            # Query the candidate next episode
            cand_stmt = (
                select(MediaItem)
                .options(
                    selectinload(MediaItem.creators),
                    selectinload(MediaItem.collection),
                )
                .where(
                    MediaItem.collection_id == ep.collection_id,
                    or_(
                        (MediaItem.season == ep.season)
                        & (MediaItem.episode == (ep.episode + 1)),
                        (MediaItem.season == ((ep.season or 1) + 1))
                        & (MediaItem.episode == 1),
                    ),
                )
                .order_by(asc(MediaItem.season), asc(MediaItem.episode))
                .limit(1)
            )
            cand = db.session.scalar(cand_stmt)
            if cand:
                # Check if user already finished this candidate
                cand_prog = db.session.scalar(
                    select(UserProgress).where(
                        UserProgress.user_id == user_id,
                        UserProgress.media_item_id == cand.id,
                        UserProgress.is_completed.is_(True),
                    )
                )
                if not cand_prog and len(next_up) < 6:
                    next_up.append(_serialize_item(cand))

    # 3. Recently Added (global catalog)
    recent_stmt = (
        select(MediaItem)
        .options(
            selectinload(MediaItem.creators),
            selectinload(MediaItem.collection),
        )
        .order_by(desc(MediaItem.created_at))
        .limit(20)
    )
    recent_items = [_serialize_item(m) for m in db.session.scalars(recent_stmt).all()]

    # 4. User Favorites
    favorites: list[dict] = []
    if user_id:
        fav_stmt = (
            select(MediaItem)
            .join(UserFavorite, MediaItem.id == UserFavorite.media_item_id)
            .options(
                selectinload(MediaItem.creators),
                selectinload(MediaItem.collection),
            )
            .where(UserFavorite.user_id == user_id)
            .order_by(desc(UserFavorite.created_at))
            .limit(20)
        )
        favorites = [_serialize_item(m) for m in db.session.scalars(fav_stmt).all()]

    return {
        "continue_watching": continue_watching,
        "continue_reading": continue_reading,
        "continue_listening": continue_listening,
        "next_up": next_up,
        "recently_added": recent_items,
        "favorites": favorites,
    }
