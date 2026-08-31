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

from buukuu.extensions import db
from buukuu.models import Author, Book, Series, Tag, User, UserProgress
from buukuu.routes.auth import optional_or_required_auth

ui_bp = Blueprint("ui", __name__)


@ui_bp.route("/")
@optional_or_required_auth
def index():
    authors = db.session.scalars(select(Author).order_by(Author.name.asc())).all()
    series_list = db.session.scalars(select(Series).order_by(Series.name.asc())).all()
    tags = db.session.scalars(select(Tag).order_by(Tag.name.asc())).all()
    total_books = db.session.scalar(select(func.count(Book.id))) or 0

    # Initial books for instant SSR rendering
    initial_books = db.session.scalars(
        select(Book)
        .order_by(Book.created_at.desc())
        .limit(current_app.config.get("PAGE_SIZE", 24))
    ).all()

    user_id = current_user.id if current_user.is_authenticated else None
    progress_map: dict[int, float] = {}
    if initial_books:
        book_ids = [b.id for b in initial_books]
        records = db.session.scalars(
            select(UserProgress).where(
                UserProgress.user_id == user_id,
                UserProgress.book_id.in_(book_ids),
            )
        ).all()
        progress_map = {r.book_id: r.percentage for r in records}

    return render_template(
        "library.html",
        authors=authors,
        series_list=series_list,
        tags=tags,
        total_books=total_books,
        initial_books=initial_books,
        progress_map=progress_map,
    )


@ui_bp.route("/book/<int:book_id>")
@optional_or_required_auth
def book_detail(book_id: int):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404, description="Book not found")

    user_id = current_user.id if current_user.is_authenticated else None
    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id,
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
        if current_user.is_admin
        else []
    )
    return render_template("settings.html", users=users)


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
