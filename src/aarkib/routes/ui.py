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

from aarkib.extensions import db
from aarkib.models import (
    Author,
    Book,
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
    total_books = db.session.scalar(select(func.count(Book.id))) or 0

    user_id = current_user.id if current_user.is_authenticated else None
    user_cond = (
        UserProgress.user_id.is_(None)
        if user_id is None
        else (UserProgress.user_id == user_id)
    )

    # 1. In-progress books for the current user
    in_progress_books = []
    prog_stmt = (
        select(Book, UserProgress)
        .join(UserProgress, Book.id == UserProgress.book_id)
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
        in_progress_books.append(
            {
                "book": b,
                "progress": p,
                "percentage": round(p.percentage, 1),
            }
        )

    # 2. Recently added books across all libraries
    recent_books = db.session.scalars(
        select(Book).order_by(Book.created_at.desc()).limit(16)
    ).all()

    # 3. Dynamic shelves based on configured libraries
    from aarkib.services.scanner import get_library_definitions

    lib_defs = get_library_definitions(current_app)
    library_shelves = []
    for lib in lib_defs:
        lib_filter = path_match_filter(lib["path"])
        lib_count = (
            db.session.scalar(select(func.count(Book.id)).where(lib_filter)) or 0
        )

        # Resilient fallback if only 1 library configured and test books indexed outside prefix
        if lib_count == 0 and len(lib_defs) == 1 and total_books > 0:
            lib_count = total_books
            lib_books = recent_books
        else:
            lib_books = db.session.scalars(
                select(Book)
                .where(lib_filter)
                .order_by(Book.created_at.desc())
                .limit(16)
            ).all()

        library_shelves.append(
            {
                "id": lib["id"],
                "name": lib["name"],
                "path": str(lib["path"]),
                "media_type": lib.get("media_type", "all"),
                "count": lib_count,
                "books": lib_books,
            }
        )

    # Compile progress map for all books shown across shelves
    all_book_ids = {b.id for b in recent_books}
    for item in in_progress_books:
        all_book_ids.add(item["book"].id)
    for shelf in library_shelves:
        for b in shelf["books"]:
            all_book_ids.add(b.id)

    progress_map: dict[int, float] = {}
    if all_book_ids:
        records = db.session.scalars(
            select(UserProgress).where(
                user_cond,
                UserProgress.book_id.in_(all_book_ids),
            )
        ).all()
        progress_map = {r.book_id: r.percentage for r in records}

    # Initial books for SSR / grid fallback
    initial_books = recent_books[: current_app.config.get("PAGE_SIZE", 24)]

    return render_template(
        "library.html",
        authors=authors,
        series_list=series_list,
        tags=tags,
        total_books=total_books,
        initial_books=initial_books,
        in_progress_books=in_progress_books,
        recent_books=recent_books,
        library_shelves=library_shelves,
        progress_map=progress_map,
    )


@ui_bp.route("/book/<int:book_id>")
@ui_bp.route("/item/<int:book_id>")
@ui_bp.route("/media/<int:book_id>")
@optional_or_required_auth
def book_detail(book_id: int):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404, description="Book not found")

    user_id = current_user.id if current_user.is_authenticated else None
    user_cond = (
        UserProgress.user_id.is_(None)
        if user_id is None
        else (UserProgress.user_id == user_id)
    )
    progress = db.session.scalar(
        select(UserProgress).where(
            user_cond,
            UserProgress.book_id == book.id,
        )
    )

    return render_template("book_detail.html", book=book, progress=progress)


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
    book_count = db.session.scalar(select(func.count(Book.id))) or 0
    author_count = db.session.scalar(select(func.count(Author.id))) or 0
    series_count = db.session.scalar(select(func.count(Series.id))) or 0

    return render_template(
        "settings.html",
        users=users,
        libraries=libraries,
        library_dirs=library_dirs,
        library_path=library_dirs[0] if library_dirs else "data/media",
        covers_path=covers_path,
        book_count=book_count,
        author_count=author_count,
        series_count=series_count,
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
