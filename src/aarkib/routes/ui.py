from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_login import current_user
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from aarkib.extensions import db
from aarkib.models import (
    Collection,
    Creator,
    MediaItem,
    Tag,
    User,
    UserProgress,
)
from aarkib.routes.auth import require_auth
from aarkib.services.media_service import path_match_filter

ui_bp = Blueprint("ui", __name__)


@ui_bp.route("/")
@ui_bp.route("/library")
@require_auth
def index() -> ResponseReturnValue:
    """Render the main library bookshelf view with shelves, continue-reading, and SSR fallback."""
    total_items = db.session.scalar(select(func.count(MediaItem.id))) or 0

    user_id = current_user.id if current_user.is_authenticated else None
    user_cond = (
        UserProgress.user_id.is_(None)
        if user_id is None
        else (UserProgress.user_id == user_id)
    )

    # 1. In-progress items for the current user
    in_progress_items = []
    prog_stmt = (
        select(MediaItem, UserProgress)
        .join(UserProgress, MediaItem.id == UserProgress.media_item_id)
        .options(
            selectinload(MediaItem.creators),
            selectinload(MediaItem.collection),
        )
        .where(
            user_cond,
            UserProgress.percentage > 0,
            UserProgress.percentage < 100,
            UserProgress.is_completed.is_(False),
        )
        .order_by(UserProgress.last_accessed_at.desc())
        .limit(16)
    )
    in_progress_rows = db.session.execute(prog_stmt).all()
    for b, p in in_progress_rows:
        in_progress_items.append(
            {
                "item": b,
                "progress": p,
                "percentage": round(p.percentage, 1),
            }
        )

    # 2. Recently added items across all libraries
    recent_items = db.session.scalars(
        select(MediaItem)
        .options(
            selectinload(MediaItem.creators),
            selectinload(MediaItem.collection),
        )
        .order_by(MediaItem.created_at.desc())
        .limit(16)
    ).all()

    # 3. Dynamic shelves based on configured libraries
    from aarkib.services.scanner import get_library_definitions

    lib_defs = get_library_definitions(current_app)
    library_shelves = []
    for lib in lib_defs:
        lib_filter = path_match_filter(lib["path"])
        lib_count = (
            db.session.scalar(select(func.count(MediaItem.id)).where(lib_filter)) or 0
        )

        # Resilient fallback if only 1 library configured and test items indexed outside prefix
        if lib_count == 0 and len(lib_defs) == 1 and total_items > 0:
            lib_count = total_items
            lib_items = recent_items
        else:
            lib_items = db.session.scalars(
                select(MediaItem)
                .options(
                    selectinload(MediaItem.creators),
                    selectinload(MediaItem.collection),
                )
                .where(lib_filter)
                .order_by(MediaItem.created_at.desc())
                .limit(16)
            ).all()

        library_shelves.append(
            {
                "id": lib["id"],
                "name": lib["name"],
                "path": str(lib["path"]),
                "media_type": lib.get("media_type", "all"),
                "count": lib_count,
                "items": lib_items,
            }
        )

    # Compile progress map for all items shown across shelves
    all_item_ids = {b.id for b in recent_items}
    for item_entry in in_progress_items:
        all_item_ids.add(item_entry["item"].id)
    for shelf in library_shelves:
        for b in shelf["items"]:
            all_item_ids.add(b.id)

    progress_map: dict[int, float] = {}
    if all_item_ids:
        records = db.session.scalars(
            select(UserProgress).where(
                user_cond,
                UserProgress.media_item_id.in_(all_item_ids),
            )
        ).all()
        progress_map = {r.media_item_id: r.percentage for r in records}

    # Initial items for SSR / grid fallback
    initial_items = recent_items[: current_app.config.get("PAGE_SIZE", 24)]

    return render_template(
        "library.html",
        total_items=total_items,
        initial_items=initial_items,
        in_progress_items=in_progress_items,
        recent_items=recent_items,
        library_shelves=library_shelves,
        progress_map=progress_map,
    )


@ui_bp.route("/media/<int:item_id>")
@require_auth
def media_detail(item_id: int) -> ResponseReturnValue:
    """Render media item detail view with metadata and reading progress."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404)

    progress = None
    user_cond = (
        UserProgress.user_id == current_user.id
        if current_user.is_authenticated
        else UserProgress.user_id.is_(None)
    )
    progress = db.session.scalar(
        select(UserProgress).where(
            user_cond,
            UserProgress.media_item_id == item.id,
        )
    )

    return render_template("media_detail.html", item=item, progress=progress)


@ui_bp.route("/authors")
@require_auth
def authors() -> ResponseReturnValue:
    """Render the authors and creators catalog view."""
    from sqlalchemy import func

    from aarkib.models.creator import media_creators

    rows = db.session.execute(
        select(Creator, func.count(media_creators.c.media_item_id).label("book_count"))
        .outerjoin(media_creators, media_creators.c.creator_id == Creator.id)
        .group_by(Creator.id)
        .order_by(Creator.name.asc())
    ).all()
    author_list = [(row[0], row[1]) for row in rows]
    return render_template("authors.html", authors=author_list)


@ui_bp.route("/series")
@require_auth
def series() -> ResponseReturnValue:
    """Render the series and collections catalog view."""
    from sqlalchemy import func

    rows = db.session.execute(
        select(Collection, func.count(MediaItem.id).label("book_count"))
        .outerjoin(MediaItem, MediaItem.collection_id == Collection.id)
        .group_by(Collection.id)
        .order_by(Collection.name.asc())
    ).all()
    series_list = [(row[0], row[1]) for row in rows]
    return render_template("series.html", series_list=series_list)


@ui_bp.route("/tags")
@require_auth
def tags() -> ResponseReturnValue:
    """Render the tags and genres catalog view."""
    from sqlalchemy import func

    from aarkib.models.tag import media_tags

    rows = db.session.execute(
        select(Tag, func.count(media_tags.c.media_item_id).label("book_count"))
        .outerjoin(media_tags, media_tags.c.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(Tag.name.asc())
    ).all()
    tag_list = [(row[0], row[1]) for row in rows]
    return render_template("tags.html", tags=tag_list)


@ui_bp.route("/pair", methods=["GET", "POST"])
@require_auth
def pair_device() -> ResponseReturnValue:
    """Authorize a TV or remote device using a 6-character user code."""
    from aarkib.services.device_auth_service import authorize_device_pairing_code

    code_param = request.args.get("code", "").strip().upper()
    if request.method == "POST":
        user_code = (request.form.get("user_code") or "").strip().upper()
        if not user_code:
            return render_template(
                "pair.html", code=user_code, error="Please enter a pairing code."
            ), 400

        success, msg = authorize_device_pairing_code(user_code, current_user.id)
        if success:
            return render_template("pair.html", success=True, message=msg)
        else:
            return render_template("pair.html", code=user_code, error=msg), 400

    return render_template("pair.html", code=code_param)


VALID_SETTINGS_CATEGORIES = {
    "system": "System Preferences",
    "libraries": "Media Folders & Libraries",
    "plugins": "Plugin Registry & Extensions",
    "users": "User Management",
    "integrations": "Integrations & OPDS Feeds",
    "backup": "Backup & Disaster Recovery",
}
ADMIN_ONLY_CATEGORIES = {"system", "plugins", "users", "backup"}


@ui_bp.route("/settings")
@ui_bp.route("/settings/<category>")
@require_auth
def settings(category: str | None = None) -> ResponseReturnValue:
    """Render settings views for configuration categories (system, libraries, users, plugins, integrations)."""
    is_admin = bool(current_user.is_authenticated and current_user.is_admin)

    # Default category selection: admin defaults to system, reader to libraries
    if not category:
        category = "system" if is_admin else "libraries"

    category = category.lower().strip()
    if category not in VALID_SETTINGS_CATEGORIES:
        abort(404, description=f"Settings category '{category}' not found")

    # Role enforcement for admin-only categories
    if category in ADMIN_ONLY_CATEGORIES and not is_admin:
        flash(
            "Administrator privileges required to access this settings category.",
            "error",
        )
        return redirect(url_for("ui.settings", category="libraries"))

    from aarkib.services.scanner import get_library_definitions

    libraries = get_library_definitions(current_app)
    library_dirs = [str(lib["path"]) for lib in libraries]
    covers_path = str(current_app.config.get("COVERS_DIR", "data/covers"))
    media_count = db.session.scalar(select(func.count(MediaItem.id))) or 0
    author_count = db.session.scalar(select(func.count(Creator.id))) or 0
    series_count = db.session.scalar(select(func.count(Collection.id))) or 0

    from aarkib.services.settings_service import get_effective_settings

    system_settings = get_effective_settings(current_app)

    from aarkib.plugins import plugin_registry

    enabled_plugins = {p.name: p.enabled for p in plugin_registry.get_all_plugins()}

    plugins_info = None
    if is_admin:
        plugins_info = [
            {
                "name": p.name,
                "display_name": p.display_name or p.name.title(),
                "type": p.plugin_type,
                "description": p.description,
                "enabled": p.enabled,
                "health": p.check_health(),
                "setting_key": f"ENABLE_{p.name.upper()}",
            }
            for p in plugin_registry.get_all_plugins()
        ]

    users = (
        db.session.scalars(select(User).order_by(User.id.asc())).all()
        if is_admin
        else []
    )

    backups = []
    if is_admin and category == "backup":
        from aarkib.services.backup import list_backups

        backups = list_backups(current_app)

    system_health = None
    if is_admin and category == "system":
        import sqlite3

        from aarkib.config import get_ffmpeg_binary, get_ffprobe_binary

        db_path_str = current_app.config.get("SQLALCHEMY_DATABASE_URI", "").replace(
            "sqlite:///", ""
        )
        db_file = (
            Path(db_path_str) if db_path_str and db_path_str != ":memory:" else None
        )
        db_size_mb = (
            round(db_file.stat().st_size / (1024 * 1024), 2)
            if db_file and db_file.is_file()
            else 0.0
        )

        wal_file = Path(f"{db_path_str}-wal") if db_file else None
        wal_size_mb = (
            round(wal_file.stat().st_size / (1024 * 1024), 2)
            if wal_file and wal_file.is_file()
            else 0.0
        )

        journal_mode = "WAL"
        try:
            from sqlalchemy import text

            journal_mode = (
                db.session.execute(text("PRAGMA journal_mode;")).scalar() or "WAL"
            )
        except Exception:
            pass

        fts_count = 0
        try:
            from sqlalchemy import text

            fts_count = (
                db.session.scalar(text("SELECT count(*) FROM media_items_fts;")) or 0
            )
        except Exception:
            fts_count = 0

        watcher = (
            current_app.extensions.get("library_watcher")
            if hasattr(current_app, "extensions")
            else None
        )
        watcher_running = bool(watcher and watcher.is_alive())

        ffmpeg_bin = get_ffmpeg_binary(current_app.config)
        ffprobe_bin = get_ffprobe_binary(current_app.config)

        data_dir = Path(current_app.config.get("DATA_DIR", "data"))
        covers_dir = Path(current_app.config.get("COVERS_DIR", data_dir / "covers"))
        backups_dir = Path(current_app.config.get("BACKUP_DIR", data_dir / "backups"))

        covers_count = len(list(covers_dir.glob("*"))) if covers_dir.is_dir() else 0
        backups_count = (
            len(list(backups_dir.glob("*.zip"))) if backups_dir.is_dir() else 0
        )

        system_health = {
            "sqlite_version": sqlite3.sqlite_version,
            "journal_mode": str(journal_mode).upper(),
            "db_size_mb": db_size_mb,
            "wal_size_mb": wal_size_mb,
            "fts_indexed_items": fts_count,
            "watcher_running": watcher_running,
            "ffmpeg_available": bool(ffmpeg_bin),
            "ffmpeg_path": ffmpeg_bin or "Not Found",
            "ffprobe_available": bool(ffprobe_bin),
            "ffprobe_path": ffprobe_bin or "Not Found",
            "covers_count": covers_count,
            "backups_count": backups_count,
        }

        from aarkib.models.job import JobRecord

        recent_jobs = db.session.scalars(
            select(JobRecord).order_by(JobRecord.created_at.desc()).limit(5)
        ).all()
    else:
        recent_jobs = []

    category_title = VALID_SETTINGS_CATEGORIES[category]

    return render_template(
        f"settings/{category}.html",
        active_category=category,
        category_title=category_title,
        is_admin=is_admin,
        users=users,
        libraries=libraries,
        library_dirs=library_dirs,
        library_path=library_dirs[0] if library_dirs else "data/media",
        covers_path=covers_path,
        media_count=media_count,
        author_count=author_count,
        series_count=series_count,
        system_settings=system_settings,
        plugins_info=plugins_info,
        enabled_plugins=enabled_plugins,
        backups=backups,
        system_health=system_health,
        recent_jobs=recent_jobs,
    )


@ui_bp.route("/manifest.webmanifest")
def pwa_manifest() -> ResponseReturnValue:
    """Serve the Web App Manifest for PWA installation."""
    static_dir = Path(current_app.static_folder or "static")
    return send_from_directory(
        static_dir, "manifest.webmanifest", mimetype="application/manifest+json"
    )


@ui_bp.route("/sw.js")
def pwa_sw() -> ResponseReturnValue:
    """Serve the Progressive Web App service worker script."""
    static_dir = Path(current_app.static_folder or "static")
    return send_from_directory(static_dir, "sw.js", mimetype="application/javascript")
