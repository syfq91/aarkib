from __future__ import annotations

from flask import Blueprint, abort, render_template
from flask_login import current_user
from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models import Book, UserProgress
from aarkib.routes.auth import optional_or_required_auth

reader_bp = Blueprint("reader", __name__, url_prefix="/reader")


@reader_bp.route("/epub/<int:book_id>")
@optional_or_required_auth
def read_epub(book_id: int):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404, description="Book not found")
    if book.file_format != "epub":
        abort(400, description="Book is not an EPUB")

    user_id = current_user.id if current_user.is_authenticated else None
    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id,
            UserProgress.book_id == book.id,
        )
    )

    return render_template(
        "reader_epub.html",
        book=book,
        initial_location=progress.progress_location if progress else "0",
    )


@reader_bp.route("/cbz/<int:book_id>")
@optional_or_required_auth
def read_cbz(book_id: int):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404, description="Book not found")
    if book.file_format not in ("cbz", "zip", "cbr"):
        abort(400, description="Book is not a CBZ comic")

    user_id = current_user.id if current_user.is_authenticated else None
    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id,
            UserProgress.book_id == book.id,
        )
    )

    initial_page = 1
    if progress and progress.progress_location:
        try:
            initial_page = max(1, int(float(progress.progress_location)))
        except ValueError:
            initial_page = 1

    return render_template(
        "reader_cbz.html",
        book=book,
        initial_page=initial_page,
    )
