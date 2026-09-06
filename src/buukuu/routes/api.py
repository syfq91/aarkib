from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request, send_file
from flask_login import current_user
from sqlalchemy import or_, select
from werkzeug.utils import secure_filename

from buukuu.extensions import db
from buukuu.models import Author, Book, Bookmark, Series, Tag, User, UserProgress
from buukuu.services.parsers.cbz import IMAGE_EXTENSIONS, natural_sort_key
from buukuu.services.scanner import index_single_book, scan_library

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.before_request
def enforce_api_auth():
    if (
        current_app.config.get("AUTH_REQUIRED", False)
        and not current_user.is_authenticated
    ):
        # Support HTTP Basic auth for API clients
        auth = request.authorization
        if auth and auth.username and auth.password:
            user = db.session.scalar(select(User).where(User.username == auth.username))
            if user and user.check_password(auth.password):
                return None

        # Allow public cover viewing if desired
        if request.endpoint == "api.get_book_cover":
            return None

        return jsonify({"error": "Authentication required"}), 401


@api_bp.route("/books", methods=["GET"])
def list_books():
    q = request.args.get("q", "").strip()
    author_id = request.args.get("author_id", type=int)
    series_id = request.args.get("series_id", type=int)
    tag_id = request.args.get("tag_id", type=int)
    file_format = request.args.get("format", "").strip().lower()
    media_type = (
        (request.args.get("media_type") or request.args.get("type") or "")
        .strip()
        .lower()
    )
    sort_by = request.args.get("sort", "added_at")
    order = request.args.get("order", "desc")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get(
        "per_page", current_app.config.get("PAGE_SIZE", 24), type=int
    )

    query = select(Book)

    if q:
        search_filter = or_(
            Book.title.ilike(f"%{q}%"),
            Book.description.ilike(f"%{q}%"),
            Book.authors.any(Author.name.ilike(f"%{q}%")),
            Book.tags.any(Tag.name.ilike(f"%{q}%")),
            Book.series.has(Series.name.ilike(f"%{q}%")),
        )
        query = query.filter(search_filter)

    if author_id:
        query = query.filter(Book.authors.any(Author.id == author_id))
    if series_id:
        query = query.filter(Book.series_id == series_id)
    if tag_id:
        query = query.filter(Book.tags.any(Tag.id == tag_id))
    if file_format:
        query = query.filter(Book.file_format == file_format)
    if media_type:
        query = query.filter(Book.media_type == media_type)

    # Sorting
    if sort_by == "title":
        col = Book.sort_title if hasattr(Book, "sort_title") else Book.title
    elif sort_by == "series_index":
        col = Book.series_index
    elif sort_by == "author":
        col = Book.title
    else:
        col = Book.created_at

    query = query.order_by(col.desc() if order == "desc" else col.asc())

    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)

    # Fetch user progress if user is authenticated or guest
    user_id = current_user.id if current_user.is_authenticated else None
    progress_map = {}
    book_ids = [b.id for b in pagination.items]
    if book_ids:
        records = db.session.scalars(
            select(UserProgress).where(
                UserProgress.user_id == user_id, UserProgress.book_id.in_(book_ids)
            )
        ).all()
        progress_map = {
            r.book_id: {
                "percentage": r.percentage,
                "location": r.progress_location,
                "completed": r.is_completed,
            }
            for r in records
        }

    items = []
    for b in pagination.items:
        items.append(
            {
                "id": b.id,
                "title": b.title,
                "media_type": b.media_type,
                "authors": [a.name for a in b.authors],
                "authors_display": b.authors_display,
                "file_format": b.file_format,
                "file_size": b.file_size,
                "cover_url": f"/api/books/{b.id}/cover",
                "series": b.series.name if b.series else None,
                "series_index": b.series_index,
                "tags": [t.name for t in b.tags],
                "progress": progress_map.get(
                    b.id, {"percentage": 0.0, "location": "0", "completed": False}
                ),
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
        )

    return jsonify(
        {
            "books": items,
            "page": pagination.page,
            "pages": pagination.pages,
            "total": pagination.total,
            "has_prev": pagination.has_prev,
            "has_next": pagination.has_next,
        }
    )


@api_bp.route("/books/<int:book_id>", methods=["GET"])
def get_book(book_id: int):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404, description="Book not found")

    user_id = current_user.id if current_user.is_authenticated else None
    prog = None
    prog_record = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id, UserProgress.book_id == book.id
        )
    )
    if prog_record:
        prog = {
            "percentage": prog_record.percentage,
            "location": prog_record.progress_location,
            "is_completed": prog_record.is_completed,
            "last_read_at": prog_record.last_read_at.isoformat(),
        }

    return jsonify(
        {
            "id": book.id,
            "title": book.title,
            "media_type": book.media_type,
            "authors": [a.name for a in book.authors],
            "authors_display": book.authors_display,
            "description": book.description,
            "publisher": book.publisher,
            "language": book.language,
            "isbn": book.isbn,
            "publication_date": book.publication_date,
            "file_format": book.file_format,
            "file_size": book.file_size,
            "series": book.series.name if book.series else None,
            "series_index": book.series_index,
            "tags": [t.name for t in book.tags],
            "page_count": book.page_count,
            "cover_url": f"/api/books/{book.id}/cover",
            "download_url": f"/api/books/{book.id}/download",
            "file_url": f"/api/books/{book.id}/file",
            "progress": prog,
            "created_at": book.created_at.isoformat() if book.created_at else None,
        }
    )


@api_bp.route("/books/<int:book_id>/cover", methods=["GET"])
def get_book_cover(book_id: int):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)

    if book.cover_image_path:
        covers_dir = Path(current_app.config["COVERS_DIR"])
        cover_file = covers_dir / book.cover_image_path
        if cover_file.exists():
            return send_file(cover_file, mimetype="image/webp")

    # Generate fallback SVG cover
    title = book.title[:30] + ("..." if len(book.title) > 30 else "")
    author = book.authors_display[:25]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">
        <rect width="300" height="450" fill="#1e293b" rx="8"/>
        <rect x="12" y="12" width="276" height="426" fill="none" stroke="#475569" stroke-width="2" rx="6"/>
        <text x="150" y="160" fill="#f8fafc" font-size="20" font-family="system-ui, sans-serif" font-weight="bold" text-anchor="middle">{title}</text>
        <text x="150" y="240" fill="#94a3b8" font-size="14" font-family="system-ui, sans-serif" text-anchor="middle">{author}</text>
        <text x="150" y="380" fill="#64748b" font-size="12" font-family="system-ui, sans-serif" text-anchor="middle">BUUKUU</text>
    </svg>"""
    return (
        io.BytesIO(svg.encode("utf-8")).getvalue(),
        200,
        {"Content-Type": "image/svg+xml"},
    )


@api_bp.route("/books/<int:book_id>/file", methods=["GET"])
@api_bp.route("/books/<int:book_id>/file/<path:filename>", methods=["GET"])
@api_bp.route("/books/<int:book_id>/book.epub", methods=["GET"])
def get_book_file(book_id: int, filename: str | None = None):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)

    file_path = Path(book.original_file_path)
    if not file_path.exists():
        abort(404, description="File missing from storage")

    mimetype = (
        "application/epub+zip"
        if book.file_format == "epub"
        else "application/vnd.comicbook+zip"
    )
    return send_file(file_path, mimetype=mimetype, conditional=True)


@api_bp.route("/books/<int:book_id>/download", methods=["GET"])
@api_bp.route(
    "/books/<int:book_id>/download/optimized/<any(x3,x4,kindle,kobo,eink,generic):preset>",
    methods=["GET"],
)
def download_book_file(book_id: int, preset: str | None = None):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)

    preset_arg = preset or request.args.get("preset") or request.args.get("optimize")
    file_path = Path(book.original_file_path)
    if not file_path.exists():
        abort(404, description="File missing from storage")

    if preset_arg and book.file_format == "epub":
        optimized_dir = Path(
            current_app.config.get(
                "OPTIMIZED_DIR",
                Path(current_app.config.get("DATA_DIR", "data")) / "optimized",
            )
        )
        from buukuu.services.optimizer import get_or_create_optimized_epub

        try:
            opt_path = get_or_create_optimized_epub(
                book_id=book.id,
                file_path=book.original_file_path,
                file_hash=book.file_hash,
                preset_key=preset_arg,
                optimized_dir=optimized_dir,
            )
            download_name = f"{book.title} ({preset_arg.upper()}).epub"
            return send_file(
                opt_path,
                as_attachment=True,
                download_name=download_name,
                mimetype="application/epub+zip",
            )
        except Exception as e:
            current_app.logger.error(
                "Failed optimizing on download for %s: %s", book.title, e
            )

    filename = f"{book.title}.{book.file_format}"
    return send_file(file_path, as_attachment=True, download_name=filename)


@api_bp.route("/optimizer/presets", methods=["GET"])
def get_optimizer_presets():
    """List supported e-ink optimization presets."""
    from buukuu.services.optimizer import DEVICE_PRESETS

    return jsonify(DEVICE_PRESETS)


@api_bp.route("/books/<int:book_id>/optimize", methods=["POST"])
def precompute_book_optimization(book_id: int):
    """Pre-generate optimized EPUB cache for a book."""
    book = db.session.get(Book, book_id)
    if not book or book.file_format != "epub":
        abort(404, description="Book is not an EPUB")

    data = request.get_json(silent=True) or {}
    preset_arg = str(data.get("preset", "generic"))

    optimized_dir = Path(
        current_app.config.get(
            "OPTIMIZED_DIR",
            Path(current_app.config.get("DATA_DIR", "data")) / "optimized",
        )
    )
    from buukuu.services.optimizer import get_or_create_optimized_epub

    try:
        opt_path = get_or_create_optimized_epub(
            book_id=book.id,
            file_path=book.original_file_path,
            file_hash=book.file_hash,
            preset_key=preset_arg,
            optimized_dir=optimized_dir,
        )
        opt_size = opt_path.stat().st_size
        orig_size = book.file_size or Path(book.original_file_path).stat().st_size
        reduction = (
            round((1.0 - (opt_size / orig_size)) * 100, 1) if orig_size > 0 else 0
        )

        return jsonify(
            {
                "status": "success",
                "book_id": book.id,
                "preset": preset_arg,
                "original_size": orig_size,
                "optimized_size": opt_size,
                "reduction_percent": reduction,
                "download_url": f"/api/books/{book.id}/download/optimized/{preset_arg}",
            }
        )
    except Exception as e:
        current_app.logger.error("Optimization failed: %s", e)
        abort(500, description=f"Optimization failed: {e}")


@api_bp.route("/books/<int:book_id>/pages", methods=["GET"])
def get_cbz_pages(book_id: int):
    book = db.session.get(Book, book_id)
    if not book or book.file_format not in ("cbz", "zip", "cbr"):
        abort(404, description="Book is not a CBZ comic")

    file_path = Path(book.original_file_path)
    if not file_path.exists():
        abort(404, description="File not found")

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            image_names = [
                name
                for name in zf.namelist()
                if Path(name).suffix.lower() in IMAGE_EXTENSIONS
                and not Path(name).name.startswith(".")
                and "__MACOSX" not in name
            ]
            image_names.sort(key=natural_sort_key)
    except Exception as e:
        current_app.logger.error(f"Error reading comic archive {file_path}: {e}")
        abort(500, description=f"Unable to read comic archive: {e}")

    pages = [
        {
            "page_number": idx + 1,
            "url": f"/api/books/{book.id}/page/{idx + 1}",
            "filename": Path(name).name,
        }
        for idx, name in enumerate(image_names)
    ]
    return jsonify({"book_id": book.id, "total_pages": len(pages), "pages": pages})


@api_bp.route("/books/<int:book_id>/page/<int:page_num>", methods=["GET"])
def get_cbz_page_image(book_id: int, page_num: int):
    book = db.session.get(Book, book_id)
    if not book or book.file_format not in ("cbz", "zip", "cbr"):
        abort(404)

    file_path = Path(book.original_file_path)
    if not file_path.exists():
        abort(404)

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            image_names = [
                name
                for name in zf.namelist()
                if Path(name).suffix.lower() in IMAGE_EXTENSIONS
                and not Path(name).name.startswith(".")
                and "__MACOSX" not in name
            ]
            image_names.sort(key=natural_sort_key)

            if page_num < 1 or page_num > len(image_names):
                abort(404, description="Page not found")

            page_name = image_names[page_num - 1]
            data = zf.read(page_name)
            ext = Path(page_name).suffix.lower()
            mimetypes = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".avif": "image/avif",
                ".bmp": "image/bmp",
                ".tiff": "image/tiff",
            }
            return (
                io.BytesIO(data).getvalue(),
                200,
                {
                    "Content-Type": mimetypes.get(ext, "image/jpeg"),
                    "Cache-Control": "public, max-age=86400",
                },
            )
    except Exception as e:
        current_app.logger.error(
            f"Error serving page {page_num} for book {book_id}: {e}"
        )
        abort(500, description=f"Unable to read comic page: {e}")


@api_bp.route("/books/<int:book_id>/progress", methods=["GET", "POST"])
def book_progress(book_id: int):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)

    user_id = current_user.id if current_user.is_authenticated else None

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        location = str(data.get("location", "0"))
        percentage = float(data.get("percentage", 0.0))
        is_completed = bool(data.get("is_completed", False) or percentage >= 99.0)

        record = db.session.scalar(
            select(UserProgress).where(
                UserProgress.user_id == user_id, UserProgress.book_id == book.id
            )
        )

        if not record:
            record = UserProgress(user_id=user_id, book_id=book.id)

        # Only update location if new location is non-zero or record has no valid location
        if location and location != "0":
            record.progress_location = location
        elif not record.progress_location:
            record.progress_location = location

        # Don't reset a known positive percentage to 0 on race condition
        if percentage > 0.0 or not record.percentage:
            record.percentage = percentage

        record.is_completed = is_completed
        record.last_read_at = datetime.now(UTC)
        db.session.add(record)
        db.session.commit()

        return jsonify(
            {
                "status": "ok",
                "percentage": record.percentage,
                "location": record.progress_location,
            }
        )

    # GET request
    record = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id, UserProgress.book_id == book.id
        )
    )

    if record:
        return jsonify(
            {
                "percentage": record.percentage,
                "location": record.progress_location,
                "is_completed": record.is_completed,
                "last_read_at": record.last_read_at.isoformat(),
            }
        )
    return jsonify({"percentage": 0.0, "location": "0", "is_completed": False})


@api_bp.route("/books/<int:book_id>/bookmarks", methods=["GET", "POST"])
def bookmarks(book_id: int):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)

    user_id = current_user.id if current_user.is_authenticated else None

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        location = str(data.get("location", ""))
        title = data.get("title", f"Bookmark at {location}")
        snippet = data.get("snippet")

        if not location:
            abort(400, description="Location is required")

        bm = Bookmark(
            user_id=user_id,
            book_id=book.id,
            location=location,
            title=title,
            snippet=snippet,
        )
        db.session.add(bm)
        db.session.commit()
        return jsonify(
            {
                "id": bm.id,
                "location": bm.location,
                "title": bm.title,
                "snippet": bm.snippet,
            }
        ), 201

    query = select(Bookmark).where(Bookmark.book_id == book.id)
    if user_id:
        query = query.where(Bookmark.user_id == user_id)
    bms = db.session.scalars(query.order_by(Bookmark.created_at.desc())).all()
    return jsonify(
        [
            {
                "id": b.id,
                "location": b.location,
                "title": b.title,
                "snippet": b.snippet,
                "created_at": b.created_at.isoformat(),
            }
            for b in bms
        ]
    )


@api_bp.route("/bookmarks/<int:bookmark_id>", methods=["DELETE"])
def delete_bookmark(bookmark_id: int):
    bm = db.session.get(Bookmark, bookmark_id)
    if not bm:
        abort(404)
    db.session.delete(bm)
    db.session.commit()
    return jsonify({"status": "deleted"})


@api_bp.route("/library/scan", methods=["POST"])
def trigger_scan():
    result = scan_library(current_app._get_current_object())  # type: ignore
    return jsonify({"status": "success", "result": result})


@api_bp.route("/library/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        abort(400, description="No file uploaded")

    file = request.files["file"]
    if not file.filename:
        abort(400, description="Empty filename")

    filename = secure_filename(file.filename)
    ext = Path(filename).suffix.lower()
    if ext not in (".epub", ".cbz", ".zip"):
        abort(400, description="Unsupported format. Only EPUB and CBZ are supported.")

    from buukuu.services.scanner import get_library_dirs

    library_dirs = get_library_dirs(current_app)
    library_dir = library_dirs[0]
    covers_dir = Path(current_app.config["COVERS_DIR"])
    library_dir.mkdir(parents=True, exist_ok=True)
    covers_dir.mkdir(parents=True, exist_ok=True)

    dest_path = library_dir / filename
    file.save(dest_path)

    auto_enrich = current_app.config.get("AUTO_ENRICH", False)
    book = index_single_book(dest_path, covers_dir, auto_enrich=auto_enrich)
    if not book:
        abort(500, description="Failed to index uploaded book")

    return jsonify({"status": "success", "book_id": book.id, "title": book.title})


@api_bp.route("/books/<int:book_id>/enrich", methods=["POST"])
def enrich_single_book(book_id: int):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404, description="Book not found")

    data = request.get_json(silent=True) or {}
    overwrite = bool(data.get("overwrite", False))
    provider = str(
        data.get("provider", current_app.config.get("METADATA_PROVIDER", "all"))
    )

    covers_dir = Path(current_app.config["COVERS_DIR"])
    from buukuu.services.enricher import enrich_book

    result = enrich_book(book, covers_dir, overwrite=overwrite, provider=provider)
    return jsonify(result)


@api_bp.route("/library/enrich", methods=["POST"])
def enrich_library():
    data = request.get_json(silent=True) or {}
    overwrite = bool(data.get("overwrite", False))
    provider = str(
        data.get("provider", current_app.config.get("METADATA_PROVIDER", "all"))
    )

    from buukuu.services.enricher import enrich_all_books

    result = enrich_all_books(
        current_app._get_current_object(),  # type: ignore
        overwrite=overwrite,
        provider=provider,
    )
    return jsonify({"status": "success", "result": result})


@api_bp.route("/books/<int:book_id>/edit", methods=["POST"])
def edit_book_metadata(book_id: int):
    book = db.session.get(Book, book_id)
    if not book:
        abort(404, description="Book not found")

    data = request.get_json(silent=True) or request.form

    title = data.get("title", "").strip() if data.get("title") else None
    if title:
        book.title = title

    # Authors
    authors_input = data.get("authors")
    if authors_input is not None:
        if isinstance(authors_input, str):
            author_names = [a.strip() for a in authors_input.split(",") if a.strip()]
        else:
            author_names = [str(a).strip() for a in authors_input if str(a).strip()]

        book_authors_list = []
        for name in author_names:
            author = db.session.scalar(select(Author).where(Author.name == name))
            if not author:
                author = Author(name=name)
                db.session.add(author)
                db.session.flush()
            book_authors_list.append(author)
        if book_authors_list:
            book.authors = book_authors_list

    # Series
    series_name = data.get("series", "").strip() if data.get("series") else None
    series_index_raw = data.get("series_index")
    if series_name:
        series_obj = db.session.scalar(select(Series).where(Series.name == series_name))
        if not series_obj:
            series_obj = Series(name=series_name)
            db.session.add(series_obj)
            db.session.flush()
        book.series = series_obj
        if series_index_raw not in (None, ""):
            try:
                book.series_index = float(series_index_raw)
            except ValueError:
                pass
        else:
            book.series_index = None
    elif "series" in data:
        book.series = None
        book.series_index = None

    # Tags / Categories
    tags_input = data.get("tags")
    if tags_input is not None:
        if isinstance(tags_input, str):
            tag_names = [t.strip() for t in tags_input.split(",") if t.strip()]
        else:
            tag_names = [str(t).strip() for t in tags_input if str(t).strip()]

        book_tags_list = []
        for tag_name in tag_names:
            tag = db.session.scalar(select(Tag).where(Tag.name == tag_name))
            if not tag:
                tag = Tag(name=tag_name)
                db.session.add(tag)
                db.session.flush()
            book_tags_list.append(tag)
        book.tags = book_tags_list

    # Optional descriptive fields
    if "description" in data:
        book.description = data.get("description") or None
    if "publisher" in data:
        book.publisher = data.get("publisher") or None
    if "publication_date" in data:
        book.publication_date = data.get("publication_date") or None
    if "isbn" in data:
        book.isbn = data.get("isbn") or None
    if "language" in data:
        book.language = data.get("language") or "en"

    db.session.commit()
    return jsonify(
        {
            "status": "success",
            "message": "Metadata updated successfully",
            "book": {
                "id": book.id,
                "title": book.title,
                "series": book.series.name if book.series else None,
                "series_index": book.series_index,
                "authors": [a.name for a in book.authors],
                "tags": [t.name for t in book.tags],
            },
        }
    )
