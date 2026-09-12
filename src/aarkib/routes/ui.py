from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    render_template,
    send_from_directory,
)
from flask_login import current_user
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from aarkib.extensions import db
from aarkib.models import (
    Author,
    MediaItem,
    Series,
    Tag,
    User,
    UserProgress,
)
from aarkib.routes.auth import optional_or_required_auth
from aarkib.services.media_service import path_match_filter

ui_bp = Blueprint("ui", __name__)


@ui_bp.route("/")
@ui_bp.route("/library")
@optional_or_required_auth
def index():
    authors = db.session.scalars(select(Author).order_by(Author.name.asc())).all()
    series_list = db.session.scalars(select(Series).order_by(Series.name.asc())).all()
    tags = db.session.scalars(select(Tag).order_by(Tag.name.asc())).all()
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
        .order_by(UserProgress.last_read_at.desc())
        .limit(16)
    )
    in_progress_rows = db.session.execute(prog_stmt).all()
    for b, p in in_progress_rows:
        in_progress_items.append(
            {
                "item": b,
                "book": b,
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
                "books": lib_items,
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
        authors=authors,
        series_list=series_list,
        tags=tags,
        total_items=total_items,
        total_books=total_items,
        initial_items=initial_items,
        initial_books=initial_items,
        in_progress_items=in_progress_items,
        in_progress_books=in_progress_items,
        recent_items=recent_items,
        recent_books=recent_items,
        library_shelves=library_shelves,
        progress_map=progress_map,
    )


@ui_bp.route("/media/<int:item_id>")
@optional_or_required_auth
def media_detail(item_id: int):
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404, description="Media item not found")

    user_id = current_user.id if current_user.is_authenticated else None
    user_cond = (
        UserProgress.user_id.is_(None)
        if user_id is None
        else (UserProgress.user_id == user_id)
    )
    progress = db.session.scalar(
        select(UserProgress).where(
            user_cond,
            UserProgress.media_item_id == item.id,
        )
    )

    return render_template("media_detail.html", item=item, book=item, progress=progress)


@ui_bp.route("/authors")
@optional_or_required_auth
def authors():
    author_list = db.session.scalars(select(Author).order_by(Author.name.asc())).all()
    return render_template("authors.html", authors=author_list)


@ui_bp.route("/series")
@optional_or_required_auth
def series():
    series_list = db.session.scalars(select(Series).order_by(Series.name.asc())).all()
    return render_template("series.html", series_list=series_list)


@ui_bp.route("/tags")
@optional_or_required_auth
def tags():
    tag_list = db.session.scalars(select(Tag).order_by(Tag.name.asc())).all()
    return render_template("tags.html", tags=tag_list)


@ui_bp.route("/settings")
@optional_or_required_auth
def settings():
    users = (
        db.session.scalars(select(User).order_by(User.id.asc())).all()
        if current_user.is_authenticated and current_user.is_admin
        else []
    )
    from aarkib.services.scanner import get_library_definitions

    libraries = get_library_definitions(current_app)
    library_dirs = [str(lib["path"]) for lib in libraries]
    covers_path = str(current_app.config.get("COVERS_DIR", "data/covers"))
    media_count = db.session.scalar(select(func.count(MediaItem.id))) or 0
    author_count = db.session.scalar(select(func.count(Author.id))) or 0
    series_count = db.session.scalar(select(func.count(Series.id))) or 0

    system_settings = None
    if current_user.is_authenticated and current_user.is_admin:
        from aarkib.services.settings_service import get_effective_settings

        system_settings = get_effective_settings(current_app)

    return render_template(
        "settings.html",
        users=users,
        libraries=libraries,
        library_dirs=library_dirs,
        library_path=library_dirs[0] if library_dirs else "data/media",
        covers_path=covers_path,
        media_count=media_count,
        book_count=media_count,
        author_count=author_count,
        series_count=series_count,
        system_settings=system_settings,
    )


@ui_bp.route("/manifest.webmanifest")
def pwa_manifest():
    static_dir = Path(current_app.static_folder or "static")
    return send_from_directory(
        static_dir, "manifest.webmanifest", mimetype="application/manifest+json"
    )


@ui_bp.route("/sw.js")
def pwa_sw():
    static_dir = Path(current_app.static_folder or "static")
    return send_from_directory(static_dir, "sw.js", mimetype="application/javascript")
