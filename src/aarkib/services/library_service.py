"""Library discovery, directory synchronization, and path containment service."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, inspect, or_, select

from aarkib.config import Config, get_env_media_dirs
from aarkib.extensions import db
from aarkib.models import Library, MediaItem

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)


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


def resolve_library(identifier: str | int) -> Library:
    """Look up a Library by integer ID or string slug; raises KeyError if absent."""
    if str(identifier).isdigit():
        lib = db.session.get(Library, int(identifier))
    else:
        lib = db.session.scalar(select(Library).where(Library.slug == str(identifier)))
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


def validate_library_availability(
    library: Library,
    expected_items_count: int | None = None,
    candidate_count: int | None = None,
    force_prune: bool = False,
) -> bool:
    """Validate that library root path exists, is readable, and is not an empty unmounted mount point.

    Returns True if the library is safe to reconcile; False if reconciliation should be aborted.
    """
    if not getattr(library, "path", None):
        logger.warning(
            "Library '%s' (id=%s) has an empty path configured. Aborting reconciliation.",
            getattr(library, "name", "unknown"),
            getattr(library, "id", None),
        )
        return False

    lib_path = Path(library.path).expanduser().resolve()
    if not lib_path.exists():
        logger.warning(
            "Library '%s' (id=%s) path does not exist: %s. Aborting reconciliation for safety.",
            getattr(library, "name", "unknown"),
            getattr(library, "id", None),
            lib_path,
        )
        return False

    if not lib_path.is_dir():
        logger.warning(
            "Library '%s' (id=%s) path is not a directory: %s. Aborting reconciliation for safety.",
            getattr(library, "name", "unknown"),
            getattr(library, "id", None),
            lib_path,
        )
        return False

    if not os.access(lib_path, os.R_OK):
        logger.warning(
            "Library '%s' (id=%s) path is not readable: %s. Aborting reconciliation for safety.",
            getattr(library, "name", "unknown"),
            getattr(library, "id", None),
            lib_path,
        )
        return False

    try:
        expected = (
            expected_items_count
            if expected_items_count is not None
            else count_media_in_library(library)
        )
    except Exception:
        expected = expected_items_count or 0

    if expected > 0 and not force_prune:
        if candidate_count is not None:
            if candidate_count == 0:
                logger.warning(
                    "Library '%s' (id=%s) expected %d items but scan found 0 candidate media files at %s "
                    "(unmounted mount point suspected). Aborting reconciliation to prevent data loss.",
                    getattr(library, "name", "unknown"),
                    getattr(library, "id", None),
                    expected,
                    lib_path,
                )
                return False
        else:
            try:
                entries = [
                    e.name
                    for e in os.scandir(lib_path)
                    if e.name not in ("lost+found", ".stfolder", ".keep")
                    and not e.name.startswith("._")
                ]
                if not entries:
                    logger.warning(
                        "Library '%s' (id=%s) expected %d items but directory %s is empty "
                        "(unmounted mount point suspected). Aborting reconciliation to prevent data loss.",
                        getattr(library, "name", "unknown"),
                        getattr(library, "id", None),
                        expected,
                        lib_path,
                    )
                    return False
            except OSError as exc:
                logger.warning(
                    "Library '%s' (id=%s) directory access error at %s: %s. Aborting reconciliation.",
                    getattr(library, "name", "unknown"),
                    getattr(library, "id", None),
                    lib_path,
                    exc,
                )
                return False

    return True


def get_media_dirs_from_config(app: Flask | None = None) -> list[Path]:
    """Resolves one or more media directories directly from app config and environment variables."""
    raw_candidates: list[Any] = []

    if app is not None:
        raw_dirs = app.config.get("MEDIA_DIRS")
        raw_dir = app.config.get("MEDIA_DIR")

        default_dirs = Config.MEDIA_DIRS
        default_dir = Config.MEDIA_DIR

        if raw_dirs is not None and raw_dirs != default_dirs:
            if isinstance(raw_dirs, (list, tuple, set)):
                raw_candidates.extend(raw_dirs)
            else:
                raw_candidates.append(raw_dirs)

        if raw_dir is not None and raw_dir != default_dir:
            if isinstance(raw_dir, (list, tuple, set)):
                raw_candidates.extend(raw_dir)
            else:
                raw_candidates.append(raw_dir)

    # Check explicitly defined environment variables
    env_paths = get_env_media_dirs()
    if env_paths:
        raw_candidates.extend(env_paths)

    # If neither app config override nor explicit env vars were found
    if not raw_candidates:
        if app is not None:
            raw = app.config.get("MEDIA_DIRS") or app.config.get("MEDIA_DIR")
            if raw is not None:
                if isinstance(raw, (list, tuple, set)):
                    raw_candidates.extend(raw)
                else:
                    raw_candidates.append(raw)
            else:
                data_dir = Path(app.config.get("DATA_DIR", "data"))
                raw_candidates.append(data_dir / "media")
        else:
            raw_candidates.append(Path("data/media"))

    # Parse and deduplicate
    final_paths: list[Path] = []
    seen: set[str] = set()

    for item in raw_candidates:
        if isinstance(item, (list, tuple, set)):
            path_objs = [
                Path(sub).expanduser() if not isinstance(sub, Path) else sub
                for sub in item
                if sub
            ]
        elif item:
            path_objs = [
                Path(item).expanduser() if not isinstance(item, Path) else item
            ]
        else:
            path_objs = []

        for path_obj in path_objs:
            try:
                norm_key = str(path_obj.resolve())
            except Exception:
                norm_key = str(path_obj)

            if norm_key not in seen:
                seen.add(norm_key)
                final_paths.append(path_obj)

    if not final_paths:
        data_dir = (
            Path(app.config.get("DATA_DIR", "data"))
            if app is not None
            else Path("data")
        )
        return [data_dir / "media"]

    return final_paths


get_library_dirs_from_config = get_media_dirs_from_config


def sync_and_get_libraries(app: Flask | None = None) -> list[Library]:
    """Synchronizes configured environment/default directories with the database Library table.

    Returns the complete list of persisted Library records.
    """
    try:
        inspector = inspect(db.engine)
        if not inspector.has_table("libraries"):
            return []
    except Exception:
        return []

    try:
        existing_libs = list(
            db.session.scalars(select(Library).order_by(Library.id.asc())).all()
        )
    except Exception:
        db.session.rollback()
        return []

    if not existing_libs:
        target_dirs = get_media_dirs_from_config(app)
    else:
        target_dirs = get_env_media_dirs()
        if app is not None:
            custom_dirs = app.config.get("MEDIA_DIRS")
            default_dirs = Config.MEDIA_DIRS
            if custom_dirs and custom_dirs != default_dirs:
                items = (
                    custom_dirs
                    if isinstance(custom_dirs, (list, tuple, set))
                    else [custom_dirs]
                )
                for d in items:
                    p = Path(d).expanduser() if not isinstance(d, Path) else d
                    if p not in target_dirs:
                        target_dirs.append(p)

    existing_paths: set[str] = set()
    for lib in existing_libs:
        existing_paths.add(lib.path)
        try:
            existing_paths.add(str(Path(lib.path).expanduser().resolve()))
        except Exception:
            logger.debug(
                "Failed to resolve library path %s: %s", lib.path, exc_info=True
            )

    has_new = False
    for p in target_dirs:
        p_expanded = p.expanduser()
        p_raw = str(p_expanded)
        try:
            p_res = str(p_expanded.resolve())
        except Exception:
            p_res = p_raw

        if p_raw in existing_paths or p_res in existing_paths:
            continue

        folder_name = p_expanded.name
        if not folder_name or folder_name in (".", "/", "data"):
            name = "Media" if not existing_libs else f"Library {len(existing_libs) + 1}"
        else:
            name = folder_name.replace("_", " ").replace("-", " ").title()

        lower_name = (folder_name or name or "").lower()
        if any(w in lower_name for w in ("comic", "manga", "cbz")):
            media_type = "book"
        elif any(w in lower_name for w in ("show", "tv", "series", "season")):
            media_type = "tv"
        elif any(w in lower_name for w in ("video", "movie", "film", "anime")):
            media_type = "movie"
        elif any(w in lower_name for w in ("podcast", "podcasts")):
            media_type = "podcast"
        elif any(w in lower_name for w in ("audiobook", "audiobooks")):
            media_type = "audiobook"
        elif any(w in lower_name for w in ("music", "song", "album")):
            media_type = "music"
        elif "book" in lower_name:
            media_type = "book"
        else:
            media_type = "book"

        base_slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-") or "media"
        slug = base_slug
        c = 1
        all_slugs = {item.slug for item in existing_libs}
        while slug in all_slugs:
            c += 1
            slug = f"{base_slug}-{c}"
        all_slugs.add(slug)

        new_lib = Library(
            slug=slug,
            name=name,
            path=p_raw,
            media_type=media_type,
        )
        db.session.add(new_lib)
        existing_libs.append(new_lib)
        existing_paths.add(p_raw)
        existing_paths.add(p_res)
        has_new = True

    if has_new:
        try:
            db.session.commit()
        except Exception as exc:
            logger.warning("Error committing synced libraries: %s", exc)
            db.session.rollback()

    try:
        return list(
            db.session.scalars(select(Library).order_by(Library.id.asc())).all()
        )
    except Exception:
        return existing_libs


def get_library_dirs(app: Flask | None = None) -> list[Path]:
    """Resolves one or more library directories from app config, database, or environment variables."""
    try:
        from flask import current_app, has_app_context

        target_app = (
            app
            if app is not None
            else (current_app._get_current_object() if has_app_context() else None)
        )
        if target_app is not None:
            if has_app_context():
                libs = sync_and_get_libraries(target_app)
            else:
                with target_app.app_context():
                    libs = sync_and_get_libraries(target_app)
            if libs:
                return [Path(lib.path) for lib in libs]
    except Exception:
        logger.debug("Library directory resolution skipped: %s", exc_info=True)

    return get_library_dirs_from_config(app)


def get_library_definitions(app: Flask | None = None) -> list[dict[str, Any]]:
    """Resolves all configured library definitions with human-friendly metadata, media_type, and counts."""
    try:
        from flask import current_app, has_app_context

        target_app = (
            app
            if app is not None
            else (current_app._get_current_object() if has_app_context() else None)
        )
        if target_app is not None:
            if has_app_context():
                libs = sync_and_get_libraries(target_app)
            else:
                with target_app.app_context():
                    libs = sync_and_get_libraries(target_app)
            if libs:
                total_items = db.session.scalar(select(func.count(MediaItem.id))) or 0
                definitions: list[dict[str, Any]] = []
                for lib in libs:
                    p = Path(lib.path).expanduser()
                    p_res, p_raw = library_path_conditions(lib)

                    lib_cond = or_(
                        MediaItem.original_file_path.startswith(p_res),
                        MediaItem.original_file_path.startswith(p_raw),
                        MediaItem.original_file_path == str(p),
                    )
                    count = (
                        db.session.scalar(
                            select(func.count(MediaItem.id)).where(lib_cond)
                        )
                        or 0
                    )
                    if count == 0 and len(libs) == 1 and total_items > 0:
                        count = total_items

                    definitions.append(
                        {
                            "id": lib.slug,
                            "db_id": lib.id,
                            "name": lib.name,
                            "path": p,
                            "path_str": str(p),
                            "media_type": lib.media_type,
                            "count": count,
                        }
                    )
                return definitions
    except Exception as e:
        logger.debug("Database library definitions fallback: %s", e)

    dirs = get_library_dirs_from_config(app)
    definitions = []
    for idx, p in enumerate(dirs):
        folder_name = p.name
        name = (
            "Media"
            if not folder_name or folder_name in (".", "/", "data")
            else folder_name.replace("_", " ").title()
        )
        slug = (
            re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-") or f"lib-{idx + 1}"
        )
        definitions.append(
            {
                "id": slug,
                "db_id": None,
                "name": name,
                "path": p,
                "path_str": str(p),
                "media_type": "all",
                "count": 0,
            }
        )
    return definitions
