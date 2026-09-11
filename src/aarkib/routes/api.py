from __future__ import annotations

import io
import mimetypes
import zipfile
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, jsonify, request, send_file
from flask_login import current_user, login_required, login_user
from sqlalchemy import or_, select, update
from sqlalchemy.orm import selectinload

from aarkib.extensions import db
from aarkib.models import (
    Author,
    Book,
    Bookmark,
    Library,
    MediaItem,
    Playlist,
    PlaylistItem,
    Series,
    Tag,
    User,
    UserFavorite,
    UserProgress,
)
from aarkib.services.job_manager import job_manager
from aarkib.services.media_service import (
    AUDIO_EXTENSIONS,
    MEDIA_TYPE_CHOICES,
    VIDEO_EXTENSIONS,
    count_books_in_library,
    generate_slug,
    library_path_conditions,
    path_match_filter,
    resolve_library,
)
from aarkib.services.parsers.cbz import IMAGE_EXTENSIONS, natural_sort_key
from aarkib.services.scanner import scan_library

api_bp = Blueprint("api", __name__, url_prefix="/api")

MAX_PER_PAGE = 100


def api_error(message: str, status: int = 400):
    """Return a standardized JSON error envelope."""
    return jsonify({"error": message}), status


def _is_within_covers(file_path: Path) -> bool:
    """Return True if a path resolves inside the configured covers directory.

    Guards the cover endpoint against path traversal via a tampered
    ``cover_image_path`` value combined with the covers directory base.
    """
    covers_dir = Path(current_app.config["COVERS_DIR"]).resolve()
    try:
        file_path.resolve().relative_to(covers_dir)
        return True
    except ValueError:
        return False


def api_admin_required(view):
    """Require an authenticated admin user for API endpoints.

    Unlike the generic ``admin_required`` in auth.py (which redirects to the UI
    for HTML pages), this returns a 401/403 JSON response suitable for API
    clients. It is applied in addition to the before_request auth check, so it
    is safe even when ``AUTH_REQUIRED`` is disabled.
    """

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        """Reject non-admin requests with a JSON 403 before calling the view."""
        if not current_user.is_admin:
            return jsonify({"error": "Administrator privileges required"}), 403
        return view(*args, **kwargs)

    return wrapped


@api_bp.before_request
def enforce_api_auth():
    """Authenticate the API request (HTTP Basic or session) and enforce auth settings."""
    # Endpoints exempt from authentication
    if request.endpoint in ("api.get_book_cover", "api.health"):
        return None

    # Authenticate via HTTP Basic auth if credentials are provided
    auth = request.authorization
    if auth and auth.username and auth.password:
        user = db.session.scalar(select(User).where(User.username == auth.username))
        if user and user.check_password(auth.password):
            login_user(user)

    if (
        current_app.config.get("AUTH_REQUIRED", False)
        and not current_user.is_authenticated
    ):
        return jsonify({"error": "Authentication required"}), 401


@api_bp.route("/health", methods=["GET"])
def health():
    """Healthcheck endpoint for container monitoring."""
    return jsonify({"status": "healthy", "app": "aarkib"})


@api_bp.route("/jobs", methods=["GET"])
def list_jobs():
    """List recent background tasks and their execution states."""
    limit = min(request.args.get("limit", 20, type=int), 100)
    jobs = job_manager.list_jobs(limit=limit)
    return jsonify({"jobs": [j.to_dict() for j in jobs]})


@api_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    """Retrieve details, progress, and results for a specific background job."""
    job = job_manager.get_job(job_id)
    if not job:
        return api_error("Job not found", 404)
    return jsonify(job.to_dict())


@api_bp.route("/books", methods=["GET"])
@api_bp.route("/items", methods=["GET"])
@api_bp.route("/media", methods=["GET"])
def list_books():
    """List catalog items with filtering (q, media_type, library), pagination, and progress."""
    q = request.args.get("q", "").strip()
    author_id = request.args.get("author_id", type=int)
    series_id = request.args.get("series_id", type=int)
    tag_id = request.args.get("tag_id", type=int)
    file_format = request.args.get("format", "").strip().lower()
    library_filter = (
        request.args.get("library") or request.args.get("library_dir") or ""
    ).strip()
    media_type = (
        (request.args.get("media_type") or request.args.get("type") or "")
        .strip()
        .lower()
    )
    sort_by = request.args.get("sort", "added_at")
    order = request.args.get("order", "desc")
    page = request.args.get("page", 1, type=int)
    per_page = min(
        request.args.get("per_page", current_app.config.get("PAGE_SIZE", 24), type=int),
        MAX_PER_PAGE,
    )

    query = select(Book).options(
        selectinload(Book.authors),
        selectinload(Book.series),
        selectinload(Book.tags),
    )

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
        if media_type == "audio":
            query = query.filter(
                Book.media_type.in_(["audio", "audiobook", "music", "podcast"])
            )
        else:
            query = query.filter(Book.media_type == media_type)
    if library_filter:
        from aarkib.services.scanner import get_library_definitions

        lib_defs = get_library_definitions(current_app)
        matched_lib = None
        for lib_def in lib_defs:
            if (
                library_filter.lower() == lib_def["id"].lower()
                or library_filter.lower() == lib_def["name"].lower()
                or library_filter == str(lib_def["path"])
                or library_filter == lib_def["path_str"]
            ):
                matched_lib = lib_def
                break

        if matched_lib:
            query = query.filter(path_match_filter(matched_lib["path"]))
        else:
            prefix = library_filter.rstrip("/\\") + "/"
            query = query.filter(Book.original_file_path.startswith(prefix))

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
    user_cond = (
        UserProgress.user_id.is_(None)
        if user_id is None
        else (UserProgress.user_id == user_id)
    )
    progress_map = {}
    book_ids = [b.id for b in pagination.items]
    if book_ids:
        records = db.session.scalars(
            select(UserProgress).where(user_cond, UserProgress.book_id.in_(book_ids))
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
                "player_url": b.player_url,
                "series": b.series.name if b.series else None,
                "series_index": b.series_index,
                "tags": [t.name for t in b.tags],
                "duration": b.duration,
                "resolution_width": b.resolution_width,
                "resolution_height": b.resolution_height,
                "codec": b.codec,
                "season": b.season,
                "episode": b.episode,
                "narrator": getattr(b, "narrator", None),
                "chapters": b.chapters if hasattr(b, "chapters") else [],
                "abridged": getattr(b, "abridged", False),
                "album": getattr(b, "album", None),
                "album_artist": getattr(b, "album_artist", None),
                "genre": getattr(b, "genre", None),
                "release_year": getattr(b, "release_year", None),
                "track_number": getattr(b, "track_number", None),
                "disc_number": getattr(b, "disc_number", None),
                "is_compilation": getattr(b, "is_compilation", False),
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


@api_bp.route("/libraries", methods=["GET"])
def list_libraries():
    """List all configured media folders (libraries) with media types and item counts."""
    from aarkib.services.scanner import get_library_definitions

    lib_defs = get_library_definitions(current_app)
    results = []
    for lib_def in lib_defs:
        results.append(
            {
                "id": lib_def["id"],
                "db_id": lib_def.get("db_id"),
                "name": lib_def["name"],
                "path": str(lib_def["path"]),
                "media_type": lib_def.get("media_type", "all"),
                "count": lib_def.get("count", 0),
            }
        )
    return jsonify({"libraries": results})


@api_bp.route("/libraries", methods=["POST"])
@api_admin_required
def add_library():
    """Add and auto-scan a new media folder (library) with an optional custom media type."""
    data = request.get_json(silent=True) or {}
    raw_path = str(data.get("path", "")).strip()
    if not raw_path:
        return jsonify({"error": "Folder path is required"}), 400

    p = Path(raw_path).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
        norm_path = str(p.resolve())
    except Exception as e:
        return jsonify({"error": f"Invalid folder path: {e}"}), 400

    # Check for duplicate
    existing = db.session.scalar(
        select(Library).where(or_(Library.path == norm_path, Library.path == str(p)))
    )
    if existing:
        return (
            jsonify({"error": f"Library folder '{raw_path}' is already configured"}),
            400,
        )

    folder_name = p.name
    name = str(data.get("name", "")).strip()
    if not name:
        name = (
            folder_name.replace("_", " ").replace("-", " ").title()
            if folder_name and folder_name not in (".", "/", "data")
            else "Media"
        )

    media_type = str(data.get("media_type", "all")).strip().lower()
    if media_type not in MEDIA_TYPE_CHOICES:
        media_type = "all"

    slug = generate_slug(name if name else folder_name)

    new_lib = Library(
        slug=slug,
        name=name,
        path=norm_path,
        media_type=media_type,
    )
    db.session.add(new_lib)
    db.session.commit()

    # Automatically scan the newly added library folder
    scan_res = scan_library(
        current_app,
        library_id=new_lib.id,  # type: ignore
    )

    return (
        jsonify(
            {
                "status": "success",
                "library": new_lib.to_dict(count=scan_res.get("added_or_updated", 0)),
                "scan": scan_res,
            }
        ),
        201,
    )


@api_bp.route("/libraries/<identifier>", methods=["GET"])
def get_library_info(identifier: str):
    """Return details for a single library by ID or slug."""
    try:
        lib = resolve_library(identifier)
    except KeyError:
        return jsonify({"error": "Library not found"}), 404

    return jsonify({"library": lib.to_dict(count=count_books_in_library(lib))})


@api_bp.route("/libraries/<identifier>", methods=["PUT"])
@api_admin_required
def update_library(identifier: str):
    """Update a library's name/media_type and reclassify indexed books accordingly."""
    try:
        lib = resolve_library(identifier)
    except KeyError:
        return jsonify({"error": "Library not found"}), 404

    data = request.get_json(silent=True) or {}
    if "name" in data and str(data["name"]).strip():
        lib.name = str(data["name"]).strip()

    if "media_type" in data:
        new_media_type = str(data["media_type"]).strip().lower()
        if new_media_type in MEDIA_TYPE_CHOICES:
            lib.media_type = new_media_type

            # Propagate media_type update to all books indexed in this folder
            p_res, p_raw = library_path_conditions(lib)
            cond = or_(
                Book.original_file_path.startswith(p_res),
                Book.original_file_path.startswith(p_raw),
            )
            if new_media_type != "all":
                db.session.execute(
                    update(Book).where(cond).values(media_type=new_media_type)
                )
            else:
                books = db.session.scalars(select(Book).where(cond)).all()
                for b in books:
                    if b.file_format in ("cbz", "cbr", "zip"):
                        b.media_type = "comic"
                    elif b.file_format in VIDEO_EXTENSIONS:
                        b.media_type = "video"
                    elif b.file_format == "m4b" or (
                        hasattr(b, "chapters") and b.chapters
                    ):
                        b.media_type = "audiobook"
                    elif b.file_format in AUDIO_EXTENSIONS:
                        b.media_type = "music"
                    else:
                        b.media_type = "book"

    lib.updated_at = datetime.now(UTC)
    db.session.commit()

    return jsonify(
        {
            "status": "success",
            "library": lib.to_dict(count=count_books_in_library(lib)),
        }
    )


@api_bp.route("/libraries/<identifier>", methods=["DELETE"])
@api_admin_required
def delete_library(identifier: str):
    """Delete a library and all catalog items indexed under its folder."""
    try:
        lib = resolve_library(identifier)
    except KeyError:
        return jsonify({"error": "Library not found"}), 404

    # Remove books indexed under this library
    p_res, p_raw = library_path_conditions(lib)
    cond = or_(
        Book.original_file_path.startswith(p_res),
        Book.original_file_path.startswith(p_raw),
    )
    books = db.session.scalars(select(Book).where(cond)).all()
    deleted_count = len(books)
    for b in books:
        db.session.delete(b)

    db.session.delete(lib)
    db.session.commit()

    return jsonify(
        {
            "status": "success",
            "deleted_id": identifier,
            "deleted_books": deleted_count,
        }
    )


@api_bp.route("/libraries/<identifier>/scan", methods=["POST"])
@api_admin_required
def scan_single_library(identifier: str):
    """Trigger a targeted rescan of a specific media folder (library)."""
    sync_mode = request.args.get("sync", "").lower() in ("true", "1", "yes")
    if sync_mode:
        result = scan_library(
            current_app,
            library_id=identifier,  # type: ignore
        )
        return jsonify({"status": "success", "result": result, "scan": result})

    job = job_manager.submit_job(
        "library_scan",
        scan_library,
        current_app._get_current_object(),
        library_id=identifier,
    )
    return (
        jsonify(
            {
                "status": "accepted",
                "job_id": job.id,
                "job_url": f"/api/jobs/{job.id}",
                "message": f"Targeted scan for library '{identifier}' initiated in background",
            }
        ),
        202,
    )


@api_bp.route("/books/<int:book_id>", methods=["GET"])
@api_bp.route("/items/<int:book_id>", methods=["GET"])
@api_bp.route("/media/<int:book_id>", methods=["GET"])
def get_book(book_id: int):
    """Return full item details, including user progress, for a single catalog item."""
    book = db.session.scalar(
        select(Book)
        .options(
            selectinload(Book.authors),
            selectinload(Book.series),
            selectinload(Book.tags),
        )
        .where(Book.id == book_id)
    )
    if not book:
        return api_error("Book not found", 404)

    user_id = current_user.id if current_user.is_authenticated else None
    user_cond = (
        UserProgress.user_id.is_(None)
        if user_id is None
        else (UserProgress.user_id == user_id)
    )
    prog = None
    prog_record = db.session.scalar(
        select(UserProgress).where(user_cond, UserProgress.book_id == book.id)
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
            "duration": book.duration,
            "formatted_duration": book.formatted_duration,
            "resolution_width": book.resolution_width,
            "resolution_height": book.resolution_height,
            "resolution_label": book.resolution_label,
            "codec": book.codec,
            "season": book.season,
            "episode": book.episode,
            "episode_code": book.episode_code,
            "author": getattr(book, "author", None),
            "narrator": getattr(book, "narrator", None),
            "chapters": book.chapters if hasattr(book, "chapters") else [],
            "abridged": getattr(book, "abridged", False),
            "album": getattr(book, "album", None),
            "album_artist": getattr(book, "album_artist", None),
            "genre": getattr(book, "genre", None),
            "release_year": getattr(book, "release_year", None),
            "track_number": getattr(book, "track_number", None),
            "disc_number": getattr(book, "disc_number", None),
            "is_compilation": getattr(book, "is_compilation", False),
            "cover_url": f"/api/books/{book.id}/cover",
            "download_url": f"/api/books/{book.id}/download",
            "file_url": f"/api/books/{book.id}/file",
            "player_url": book.player_url,
            "progress": prog,
            "created_at": book.created_at.isoformat() if book.created_at else None,
        }
    )


@api_bp.route("/books/<int:book_id>/cover", methods=["GET"])
@api_bp.route("/items/<int:book_id>/cover", methods=["GET"])
@api_bp.route("/media/<int:book_id>/cover", methods=["GET"])
def get_book_cover(book_id: int):
    """Serve the cached WebP cover/poster image for a media item."""
    book = db.session.get(Book, book_id)
    if not book:
        abort(404)

    if book.cover_image_path:
        covers_dir = Path(current_app.config["COVERS_DIR"])
        cover_file = covers_dir / book.cover_image_path
        if cover_file.exists() and _is_within_covers(cover_file):
            return send_file(cover_file, mimetype="image/webp")

    # Generate fallback SVG cover
    if book.is_video:
        title = book.title[:30] + ("..." if len(book.title) > 30 else "")
        sub = (
            f"S{book.season:02d}E{book.episode:02d}"
            if (book.season is not None and book.episode is not None)
            else (
                book.publication_date
                or (book.file_format.upper() if book.file_format else "VIDEO")
            )
        )
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">
        <defs>
            <linearGradient id="vidGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stop-color="#0f172a"/>
                <stop offset="100%" stop-color="#1e1b4b"/>
            </linearGradient>
        </defs>
        <rect width="300" height="450" fill="url(#vidGrad)" rx="8"/>
        <rect x="12" y="12" width="276" height="426" fill="none" stroke="#4338ca" stroke-width="2" rx="6" stroke-dasharray="6,4"/>
        <text x="150" y="130" font-size="48" text-anchor="middle">🎬</text>
        <text x="150" y="210" fill="#f8fafc" font-size="18" font-family="system-ui, sans-serif" font-weight="bold" text-anchor="middle">{title}</text>
        <text x="150" y="250" fill="#a5b4fc" font-size="14" font-family="system-ui, sans-serif" text-anchor="middle">{sub}</text>
        <rect x="90" y="295" width="120" height="28" rx="6" fill="#4338ca"/>
        <text x="150" y="314" fill="#ffffff" font-size="12" font-family="system-ui, sans-serif" font-weight="bold" text-anchor="middle">{(book.file_format or "video").upper()}</text>
        <text x="150" y="410" fill="#6366f1" font-size="12" font-family="system-ui, sans-serif" letter-spacing="2" text-anchor="middle">AARKIB VIDEO</text>
    </svg>"""
        return (
            io.BytesIO(svg.encode("utf-8")).getvalue(),
            200,
            {"Content-Type": "image/svg+xml"},
        )

    title = book.title[:30] + ("..." if len(book.title) > 30 else "")
    author = book.authors_display[:25]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">
        <rect width="300" height="450" fill="#1e293b" rx="8"/>
        <rect x="12" y="12" width="276" height="426" fill="none" stroke="#475569" stroke-width="2" rx="6"/>
        <text x="150" y="160" fill="#f8fafc" font-size="20" font-family="system-ui, sans-serif" font-weight="bold" text-anchor="middle">{title}</text>
        <text x="150" y="240" fill="#94a3b8" font-size="14" font-family="system-ui, sans-serif" text-anchor="middle">{author}</text>
        <text x="150" y="380" fill="#64748b" font-size="12" font-family="system-ui, sans-serif" text-anchor="middle">AARKIB</text>
    </svg>"""
    return (
        io.BytesIO(svg.encode("utf-8")).getvalue(),
        200,
        {"Content-Type": "image/svg+xml"},
    )


@api_bp.route("/books/<int:book_id>/file", methods=["GET"])
@api_bp.route("/books/<int:book_id>/file/<path:filename>", methods=["GET"])
@api_bp.route("/books/<int:book_id>/book.epub", methods=["GET"])
@api_bp.route("/books/<int:book_id>/stream", methods=["GET"])
@api_bp.route("/items/<int:book_id>/file", methods=["GET"])
@api_bp.route("/items/<int:book_id>/file/<path:filename>", methods=["GET"])
@api_bp.route("/items/<int:book_id>/stream", methods=["GET"])
@api_bp.route("/media/<int:book_id>/file", methods=["GET"])
@api_bp.route("/media/<int:book_id>/file/<path:filename>", methods=["GET"])
@api_bp.route("/media/<int:book_id>/stream", methods=["GET"])
def get_book_file(book_id: int, filename: str | None = None):
    """Stream the original media file with HTTP 206 byte-range support."""
    book = db.session.get(Book, book_id)
    if not book:
        return api_error("Book not found", 404)

    file_path = Path(book.original_file_path)
    if not file_path.exists():
        return api_error("File missing from storage", 404)

    guessed, _ = mimetypes.guess_type(str(file_path))
    if guessed:
        mimetype = guessed
    elif book.file_format == "epub":
        mimetype = "application/epub+zip"
    elif book.file_format in ("cbz", "zip", "cbr"):
        mimetype = "application/vnd.comicbook+zip"
    elif book.file_format == "mp4":
        mimetype = "video/mp4"
    elif book.file_format == "webm":
        mimetype = "video/webm"
    elif book.file_format == "mkv":
        mimetype = "video/x-matroska"
    elif book.file_format in ("m4b", "m4a"):
        mimetype = "audio/mp4"
    elif book.file_format == "mp3":
        mimetype = "audio/mpeg"
    elif book.file_format == "flac":
        mimetype = "audio/flac"
    elif book.file_format == "wav":
        mimetype = "audio/wav"
    elif book.file_format == "ogg":
        mimetype = "audio/ogg"
    elif book.file_format == "opus":
        mimetype = "audio/opus"
    elif book.file_format == "aac":
        mimetype = "audio/aac"
    else:
        mimetype = "application/octet-stream"

    return send_file(file_path, mimetype=mimetype, conditional=True)


# ---------------------------------------------------------------------------
# Streaming, Remuxing & Transcoding Endpoints (Phase 4)
# ---------------------------------------------------------------------------


@api_bp.route("/stream/<int:book_id>/info", methods=["GET"])
@api_bp.route("/books/<int:book_id>/stream/info", methods=["GET"])
def get_stream_info(book_id: int):
    """Returns technical stream metadata, codecs, tracks, and recommended playback strategy."""
    book = db.session.get(Book, book_id)
    if not book:
        return api_error("Media item not found", 404)

    file_path = Path(book.original_file_path)
    if not file_path.exists():
        return api_error("File missing from storage", 404)

    from aarkib.services.transcoder import (
        evaluate_playback_strategy,
        probe_media_streams,
    )

    streams = probe_media_streams(file_path)
    eval_res = evaluate_playback_strategy(file_path, streams)

    return jsonify(
        {
            "id": book.id,
            "title": book.title,
            "file_format": book.file_format,
            "original_file_path": str(file_path),
            "streams": streams,
            "evaluation": eval_res,
            "direct_url": f"/api/books/{book.id}/file",
            "remux_url": f"/api/stream/{book.id}/remux",
            "hls_url": f"/api/stream/{book.id}/hls/master.m3u8",
        }
    )


@api_bp.route("/stream/<int:book_id>/remux", methods=["GET"])
@api_bp.route("/books/<int:book_id>/stream/remux", methods=["GET"])
def stream_remux_video(book_id: int):
    """Progressive on-the-fly container remux (e.g. MKV -> fragmented MP4) via FFmpeg pipe."""
    book = db.session.get(Book, book_id)
    if not book:
        return api_error("Media item not found", 404)

    file_path = Path(book.original_file_path)
    if not file_path.exists():
        return api_error("File missing from storage", 404)

    from aarkib.services.transcoder import stream_remux_pipe

    seek_sec = request.args.get("start", 0.0, type=float)
    audio_transcode = request.args.get("audio_transcode", "false").lower() in (
        "true",
        "1",
        "yes",
    )

    return Response(
        stream_remux_pipe(
            file_path, seek_seconds=seek_sec, audio_transcode=audio_transcode
        ),
        mimetype="video/mp4",
        headers={
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )


@api_bp.route("/stream/<int:book_id>/hls/master.m3u8", methods=["GET"])
@api_bp.route("/books/<int:book_id>/stream/hls/master.m3u8", methods=["GET"])
def get_hls_master_playlist(book_id: int):
    """Spawns/attaches to an HLS transcode session and returns the master playlist."""
    book = db.session.get(Book, book_id)
    if not book:
        return api_error("Media item not found", 404)

    file_path = Path(book.original_file_path)
    if not file_path.exists():
        return api_error("File missing from storage", 404)

    from aarkib.services.transcoder import RESOLUTION_PRESETS, transcode_supervisor

    resolution = request.args.get("resolution", "original")
    seek_offset = request.args.get("start", 0.0, type=float)
    audio_track = request.args.get("audio_track", 0, type=int)

    transcode_dir = Path(
        current_app.config.get(
            "TRANSCODE_DIR",
            Path(current_app.config.get("DATA_DIR", "data")) / "transcode",
        )
    )

    session = transcode_supervisor.create_or_get_hls_session(
        media_item_id=book.id,
        file_path=file_path,
        transcode_base_dir=transcode_dir,
        resolution=resolution,
        seek_offset=seek_offset,
        audio_track_index=audio_track,
    )

    preset = RESOLUTION_PRESETS.get(resolution, RESOLUTION_PRESETS["original"])
    bandwidth = preset["video_bitrate"] * 1000

    master_content = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},NAME="{resolution}"\n'
        f"/api/stream/{book.id}/hls/{session.session_id}/playlist.m3u8\n"
    )

    return Response(
        master_content,
        mimetype="application/vnd.apple.mpegurl",
        headers={
            "Content-Type": "application/vnd.apple.mpegurl",
            "Cache-Control": "no-cache",
        },
    )


@api_bp.route("/stream/<int:book_id>/hls/<session_id>/playlist.m3u8", methods=["GET"])
def get_hls_session_playlist(book_id: int, session_id: str):
    """Serves the HLS playlist generated by an active transcode session."""
    from aarkib.services.transcoder import transcode_supervisor

    session = transcode_supervisor.get_session(session_id)
    if not session or not session.is_active:
        return api_error("Transcode session expired or not found", 404)

    session.touch()
    playlist_path = session.output_dir / "playlist.m3u8"

    if not playlist_path.exists():
        return api_error("Playlist generating, please retry", 503)

    return send_file(playlist_path, mimetype="application/vnd.apple.mpegurl")


@api_bp.route(
    "/stream/<int:book_id>/hls/<session_id>/<path:segment_name>", methods=["GET"]
)
def get_hls_segment(book_id: int, session_id: str, segment_name: str):
    """Serves an HLS segment (.m4s or init.mp4) and updates session heartbeat."""
    from aarkib.services.transcoder import transcode_supervisor

    # Prevent directory traversal
    if ".." in segment_name or segment_name.startswith(("/", "\\")):
        return api_error("Invalid segment name", 400)

    session = transcode_supervisor.get_session(session_id)
    if not session or not session.is_active:
        return api_error("Transcode session expired or not found", 404)

    session.touch()
    segment_path = (session.output_dir / segment_name).resolve()

    if (
        not segment_path.is_relative_to(session.output_dir.resolve())
        or not segment_path.exists()
    ):
        return api_error("Segment not ready or not found", 404)

    mimetype = "video/iso.segment" if segment_name.endswith(".m4s") else "video/mp4"
    return send_file(segment_path, mimetype=mimetype)


@api_bp.route("/stream/<int:book_id>/hls/<session_id>/heartbeat", methods=["POST"])
def hls_heartbeat(book_id: int, session_id: str):
    """Client heartbeat ping to keep an active HLS transcode session alive."""
    from aarkib.services.transcoder import transcode_supervisor

    session = transcode_supervisor.get_session(session_id)
    if not session or not session.is_active:
        return api_error("Session not found or expired", 404)

    session.touch()
    return jsonify({"status": "ok", "session_id": session_id})


@api_bp.route("/stream/<int:book_id>/hls/<session_id>/stop", methods=["POST"])
def stop_hls_session(book_id: int, session_id: str):
    """Explicitly stops a transcode session and prunes its scratch directory."""
    from aarkib.services.transcoder import transcode_supervisor

    transcode_supervisor.stop_session(session_id)
    return jsonify({"status": "stopped", "session_id": session_id})


@api_bp.route("/stream/<int:book_id>/subtitles", methods=["GET"])
@api_bp.route("/books/<int:book_id>/stream/subtitles", methods=["GET"])
def list_subtitles(book_id: int):
    """Returns list of embedded subtitle tracks for a media item."""
    book = db.session.get(Book, book_id)
    if not book:
        return api_error("Media item not found", 404)

    file_path = Path(book.original_file_path)
    if not file_path.exists():
        return api_error("File missing from storage", 404)

    from aarkib.services.transcoder import probe_media_streams

    streams = probe_media_streams(file_path)
    return jsonify(
        {
            "id": book.id,
            "subtitles": streams.get("subtitles", []),
        }
    )


@api_bp.route("/stream/<int:book_id>/subtitles/<int:track_index>.vtt", methods=["GET"])
@api_bp.route(
    "/books/<int:book_id>/stream/subtitles/<int:track_index>.vtt", methods=["GET"]
)
def get_subtitle_vtt(book_id: int, track_index: int):
    """Extracts and converts the requested embedded subtitle track to WebVTT."""
    book = db.session.get(Book, book_id)
    if not book:
        return api_error("Media item not found", 404)

    file_path = Path(book.original_file_path)
    if not file_path.exists():
        return api_error("File missing from storage", 404)

    from aarkib.services.transcoder import generate_vtt_subtitles

    vtt_bytes = generate_vtt_subtitles(file_path, track_index)
    return Response(
        vtt_bytes,
        mimetype="text/vtt",
        headers={"Content-Type": "text/vtt; charset=utf-8"},
    )


@api_bp.route("/books/<int:book_id>/download", methods=["GET"])
@api_bp.route(
    "/books/<int:book_id>/download/optimized/<any(x3,x4,kindle,kobo,eink,generic):preset>",
    methods=["GET"],
)
def download_book_file(book_id: int, preset: str | None = None):
    """Download a book file, optionally served from a precomputed e-ink optimized EPUB."""
    book = db.session.get(Book, book_id)
    if not book:
        return api_error("Book not found", 404)

    preset_arg = preset or request.args.get("preset") or request.args.get("optimize")
    file_path = Path(book.original_file_path)
    if not file_path.exists():
        return api_error("File missing from storage", 404)

    if preset_arg and book.file_format == "epub":
        optimized_dir = Path(
            current_app.config.get(
                "OPTIMIZED_DIR",
                Path(current_app.config.get("DATA_DIR", "data")) / "optimized",
            )
        )
        from aarkib.services.optimizer import get_or_create_optimized_epub

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
    guessed, _ = mimetypes.guess_type(str(file_path))
    mimetype = guessed or "application/octet-stream"
    return send_file(
        file_path, as_attachment=True, download_name=filename, mimetype=mimetype
    )


@api_bp.route("/optimizer/presets", methods=["GET"])
def get_optimizer_presets():
    """List supported e-ink optimization presets."""
    from aarkib.services.optimizer import DEVICE_PRESETS

    return jsonify(DEVICE_PRESETS)


@api_bp.route("/books/<int:book_id>/optimize", methods=["POST"])
@api_admin_required
def precompute_book_optimization(book_id: int):
    """Pre-generate optimized EPUB cache for a book."""
    book = db.session.get(Book, book_id)
    if not book or book.file_format != "epub":
        return api_error("Book is not an EPUB", 404)

    data = request.get_json(silent=True) or {}
    preset_arg = str(data.get("preset", "generic"))

    optimized_dir = Path(
        current_app.config.get(
            "OPTIMIZED_DIR",
            Path(current_app.config.get("DATA_DIR", "data")) / "optimized",
        )
    )
    from aarkib.services.optimizer import get_or_create_optimized_epub

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
        return api_error(f"Optimization failed: {e}", 500)


@api_bp.route("/books/<int:book_id>/pages", methods=["GET"])
def get_cbz_pages(book_id: int):
    """List page metadata (path, width, height) for a CBZ comic."""
    book = db.session.get(Book, book_id)
    if not book or book.file_format not in ("cbz", "zip", "cbr"):
        return api_error("Book is not a CBZ comic", 404)

    file_path = Path(book.original_file_path)
    if not file_path.exists():
        return api_error("File not found", 404)

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
        current_app.logger.error("Error reading comic archive %s: %s", file_path, e)
        return api_error(f"Unable to read comic archive: {e}", 500)

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
    """Serve a single page image from a CBZ comic archive."""
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
                data,
                200,
                {
                    "Content-Type": mimetypes.get(ext, "image/jpeg"),
                    "Cache-Control": "public, max-age=86400",
                },
            )
    except Exception as e:
        current_app.logger.error(
            "Error serving page %s for book %s: %s", page_num, book_id, e
        )
        abort(500, description=f"Unable to read comic page: {e}")


@api_bp.route("/books/<int:book_id>/progress", methods=["GET", "POST"])
@api_bp.route("/items/<int:book_id>/progress", methods=["GET", "POST"])
@api_bp.route("/media/<int:book_id>/progress", methods=["GET", "POST"])
def book_progress(book_id: int):
    """Fetch or update reading/video progress for a book or media item."""
    book = db.session.get(Book, book_id)
    if not book:
        return api_error("Book not found", 404)

    user_id = current_user.id if current_user.is_authenticated else None
    user_cond = (
        UserProgress.user_id.is_(None)
        if user_id is None
        else (UserProgress.user_id == user_id)
    )

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        location = str(data.get("location", "0"))
        try:
            percentage = float(data.get("percentage", 0.0))
        except ValueError, TypeError:
            percentage = 0.0
        percentage = max(0.0, min(100.0, percentage))
        is_completed = bool(data.get("is_completed", False) or percentage >= 99.0)

        record = db.session.scalar(
            select(UserProgress).where(user_cond, UserProgress.book_id == book.id)
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
        select(UserProgress).where(user_cond, UserProgress.book_id == book.id)
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
@api_bp.route("/items/<int:book_id>/bookmarks", methods=["GET", "POST"])
@api_bp.route("/media/<int:book_id>/bookmarks", methods=["GET", "POST"])
def bookmarks(book_id: int):
    """List or create bookmarks for a book or media item."""
    book = db.session.get(Book, book_id)
    if not book:
        return api_error("Book not found", 404)

    user_id = current_user.id if current_user.is_authenticated else None

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        location = str(data.get("location", ""))
        title = data.get("title", f"Bookmark at {location}")
        snippet = data.get("snippet")

        if not location:
            return api_error("Location is required", 400)

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
    """Delete a bookmark, restricted to its owner (or any anonymous bookmark)."""
    bm = db.session.get(Bookmark, bookmark_id)
    if not bm:
        return api_error("Bookmark not found", 404)
    user_id = current_user.id if current_user.is_authenticated else None
    if bm.user_id and bm.user_id != user_id:
        return api_error("Forbidden", 403)
    db.session.delete(bm)
    db.session.commit()
    return jsonify({"status": "deleted"})


@api_bp.route("/library/scan", methods=["POST"])
@api_bp.route("/libraries/scan", methods=["POST"])
@api_admin_required
def trigger_scan():
    """Trigger a full scan across all configured media folders."""
    sync_mode = request.args.get("sync", "").lower() in ("true", "1", "yes")
    if sync_mode:
        result = scan_library(current_app)  # type: ignore
        return jsonify({"status": "success", "result": result, "scan": result})

    job = job_manager.submit_job(
        "library_scan",
        scan_library,
        current_app._get_current_object(),
    )
    return (
        jsonify(
            {
                "status": "accepted",
                "job_id": job.id,
                "job_url": f"/api/jobs/{job.id}",
                "message": "Full library scan initiated in background",
            }
        ),
        202,
    )


@api_bp.route("/books/<int:book_id>/enrich", methods=["POST"])
@api_bp.route("/items/<int:book_id>/enrich", methods=["POST"])
@api_bp.route("/media/<int:book_id>/enrich", methods=["POST"])
@api_admin_required
def enrich_single_book(book_id: int):
    """Fetch online metadata for a single media item (books, video, music)."""
    item = db.session.get(MediaItem, book_id)
    if not item:
        return api_error("Media item not found", 404)

    data = request.get_json(silent=True) or {}
    overwrite = bool(data.get("overwrite", False))
    provider = str(
        data.get("provider", current_app.config.get("METADATA_PROVIDER", "all"))
    )

    covers_dir = Path(current_app.config["COVERS_DIR"])
    from aarkib.services.enricher import enrich_media_item

    result = enrich_media_item(item, covers_dir, overwrite=overwrite, provider=provider)
    return jsonify(result)


@api_bp.route("/books/<int:item_id>/metadata/search", methods=["GET"])
@api_bp.route("/items/<int:item_id>/metadata/search", methods=["GET"])
@api_bp.route("/media/<int:item_id>/metadata/search", methods=["GET"])
@api_admin_required
def search_metadata_candidates(item_id: int):
    """Search external metadata providers for candidate matches with confidence scores."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    query = request.args.get("q") or item.title or ""
    year = (
        request.args.get("year")
        or item.publication_date
        or getattr(item, "release_year", None)
    )
    provider_name = request.args.get("provider")

    from aarkib.services.metadata import metadata_registry

    candidates = metadata_registry.search(
        media_type=item.media_type or "all",
        query=query,
        year=str(year)[:4] if year else None,
        provider_name=provider_name,
    )

    return jsonify(
        {
            "status": "success",
            "media_id": item.id,
            "media_type": item.media_type,
            "query": query,
            "count": len(candidates),
            "candidates": [c.to_dict() for c in candidates],
        }
    )


@api_bp.route("/books/<int:item_id>/metadata/apply", methods=["POST"])
@api_bp.route("/items/<int:item_id>/metadata/apply", methods=["POST"])
@api_bp.route("/media/<int:item_id>/metadata/apply", methods=["POST"])
@api_admin_required
def apply_metadata_candidate(item_id: int):
    """Apply a selected metadata candidate to the media item and optionally update field locks."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    data = request.get_json(silent=True) or {}
    provider = data.get("provider")
    external_id = data.get("external_id")
    lock_fields = data.get("lock_fields")

    if not provider or not external_id:
        return api_error("Both 'provider' and 'external_id' are required", 400)

    covers_dir = Path(current_app.config["COVERS_DIR"])
    from aarkib.services.enricher import enrich_media_item

    result = enrich_media_item(
        item,
        covers_dir=covers_dir,
        overwrite=True,
        candidate_external_id=str(external_id),
        candidate_provider=str(provider),
    )

    if result.get("status") == "not_found":
        return api_error("Failed to fetch details for candidate from provider", 404)

    # If lock_fields specified, set them on the item after applying metadata
    if lock_fields is not None:
        if isinstance(lock_fields, list):
            item.set_locked_fields(lock_fields)
        elif isinstance(lock_fields, str):
            item.set_locked_fields(
                [f.strip() for f in lock_fields.split(",") if f.strip()]
            )
        db.session.commit()

    return jsonify(
        {
            "status": "success",
            "message": "Metadata applied successfully",
            "media_id": item.id,
            "changes": result.get("changes", []),
            "locked_fields": item.get_locked_fields(),
            "item": {
                "id": item.id,
                "title": item.title,
                "authors": [a.name for a in item.authors],
                "description": item.description,
                "cover_image_path": item.cover_image_path,
                "external_id": item.external_id,
            },
        }
    )


@api_bp.route("/books/<int:item_id>/metadata/locked-fields", methods=["GET", "PUT"])
@api_bp.route("/items/<int:item_id>/metadata/locked-fields", methods=["GET", "PUT"])
@api_bp.route("/media/<int:item_id>/metadata/locked-fields", methods=["GET", "PUT"])
@api_admin_required
def manage_locked_fields(item_id: int):
    """Inspect or update the locked fields configuration for a media item."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    if request.method == "GET":
        return jsonify(
            {
                "status": "success",
                "media_id": item.id,
                "locked_fields": item.get_locked_fields(),
            }
        )

    data = request.get_json(silent=True) or {}
    raw_locks = data.get("locked_fields", [])
    if isinstance(raw_locks, list):
        item.set_locked_fields(raw_locks)
    elif isinstance(raw_locks, str):
        item.set_locked_fields([f.strip() for f in raw_locks.split(",") if f.strip()])
    db.session.commit()

    return jsonify(
        {
            "status": "success",
            "message": "Locked fields updated",
            "media_id": item.id,
            "locked_fields": item.get_locked_fields(),
        }
    )


@api_bp.route("/library/enrich", methods=["POST"])
@api_bp.route("/libraries/enrich", methods=["POST"])
@api_admin_required
def enrich_library():
    """Fetch online metadata for indexed media items across every library."""
    data = request.get_json(silent=True) or {}
    overwrite = bool(data.get("overwrite", False))
    provider = str(
        data.get("provider", current_app.config.get("METADATA_PROVIDER", "all"))
    )
    media_type = str(data.get("media_type", "all"))
    sync_mode = request.args.get("sync", "").lower() in ("true", "1", "yes")

    from aarkib.services.enricher import enrich_all_media

    if sync_mode:
        result = enrich_all_media(
            current_app,
            overwrite=overwrite,
            provider=provider,
            media_type=media_type,
        )
        return jsonify({"status": "success", "result": result})

    def run_enrichment(app, **kwargs):
        return enrich_all_media(
            app,
            overwrite=kwargs.get("overwrite", False),
            provider=kwargs.get("provider", "all"),
            media_type=kwargs.get("media_type", "all"),
        )

    job = job_manager.submit_job(
        "batch_enrich",
        run_enrichment,
        current_app._get_current_object(),
        overwrite=overwrite,
        provider=provider,
        media_type=media_type,
    )
    return (
        jsonify(
            {
                "status": "accepted",
                "job_id": job.id,
                "job_url": f"/api/jobs/{job.id}",
                "message": "Batch metadata enrichment initiated in background",
            }
        ),
        202,
    )


@api_bp.route("/books/<int:book_id>/edit", methods=["POST"])
@api_bp.route("/books/<int:book_id>", methods=["PATCH"])
@api_bp.route("/items/<int:book_id>/edit", methods=["POST"])
@api_bp.route("/items/<int:book_id>", methods=["PATCH"])
@api_bp.route("/media/<int:book_id>/edit", methods=["POST"])
@api_bp.route("/media/<int:book_id>", methods=["PATCH"])
@api_admin_required
def edit_book_metadata(book_id: int):
    """Manually edit a media item's title, creators, series, tags, and descriptive fields."""
    book = db.session.get(Book, book_id)
    if not book:
        return api_error("Book not found", 404)

    from aarkib.services.media_service import edit_media_metadata as apply_edits

    data = request.get_json(silent=True) or request.form
    apply_edits(book, data)
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
                "locked_fields": book.get_locked_fields(),
            },
        }
    )


# ----------------------------------------------------------------------
# User Favorites API
# ----------------------------------------------------------------------


@api_bp.route("/media/<int:item_id>/favorite", methods=["POST"])
def toggle_favorite(item_id: int):
    """Toggle or update favorite status for a media item."""
    user_id = current_user.id if current_user.is_authenticated else None
    if not user_id:
        return api_error("Authentication required to manage favorites", 401)

    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    data = request.get_json(silent=True) or {}
    explicit_state = data.get("favorite")

    fav = db.session.scalar(
        select(UserFavorite).where(
            UserFavorite.user_id == user_id,
            UserFavorite.media_item_id == item_id,
        )
    )

    if explicit_state is True:
        if not fav:
            fav = UserFavorite(user_id=user_id, media_item_id=item_id)
            db.session.add(fav)
            db.session.commit()
        return jsonify(
            {"status": "success", "favorited": True, "media_item_id": item_id}
        )
    elif explicit_state is False:
        if fav:
            db.session.delete(fav)
            db.session.commit()
        return jsonify(
            {"status": "success", "favorited": False, "media_item_id": item_id}
        )
    else:
        # Toggle
        if fav:
            db.session.delete(fav)
            db.session.commit()
            return jsonify(
                {"status": "success", "favorited": False, "media_item_id": item_id}
            )
        else:
            fav = UserFavorite(user_id=user_id, media_item_id=item_id)
            db.session.add(fav)
            db.session.commit()
            return jsonify(
                {"status": "success", "favorited": True, "media_item_id": item_id}
            )


@api_bp.route("/favorites", methods=["GET"])
def get_favorites():
    """Returns all favorited media items for the current user."""
    user_id = current_user.id if current_user.is_authenticated else None
    if not user_id:
        return api_error("Authentication required to list favorites", 401)

    favs = db.session.scalars(
        select(UserFavorite)
        .where(UserFavorite.user_id == user_id)
        .order_by(UserFavorite.created_at.desc())
    ).all()

    items = []
    for f in favs:
        if f.media_item:
            items.append(
                {
                    "favorite_id": f.id,
                    "media_item_id": f.media_item_id,
                    "title": f.media_item.title,
                    "media_type": f.media_item.media_type,
                    "file_format": f.media_item.file_format,
                    "creators": f.media_item.creators_display,
                    "cover_url": f"/api/books/{f.media_item.id}/cover",
                    "player_url": f.media_item.player_url,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
            )

    return jsonify({"favorites": items, "count": len(items)})


# ----------------------------------------------------------------------
# Playlists API
# ----------------------------------------------------------------------


@api_bp.route("/playlists", methods=["GET"])
def get_playlists():
    """List playlists belonging to the user or public playlists."""
    user_id = current_user.id if current_user.is_authenticated else None
    cond = Playlist.is_public.is_(True)
    if user_id:
        cond = or_(cond, Playlist.user_id == user_id)

    playlists = db.session.scalars(
        select(Playlist).where(cond).order_by(Playlist.updated_at.desc())
    ).all()
    return jsonify({"playlists": [p.to_dict(include_items=False) for p in playlists]})


@api_bp.route("/playlists", methods=["POST"])
def create_playlist():
    """Create a new playlist."""
    user_id = current_user.id if current_user.is_authenticated else None
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    if not title:
        return api_error("Playlist title is required", 400)

    playlist = Playlist(
        user_id=user_id,
        title=title,
        description=data.get("description"),
        media_type=data.get("media_type", "music"),
        is_public=bool(data.get("is_public", False)),
    )
    db.session.add(playlist)
    db.session.commit()
    return (
        jsonify(
            {"status": "success", "playlist": playlist.to_dict(include_items=True)}
        ),
        201,
    )


@api_bp.route("/playlists/<int:playlist_id>", methods=["GET"])
def get_playlist_detail(playlist_id: int):
    """Get playlist details and its ordered items."""
    user_id = current_user.id if current_user.is_authenticated else None
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        return api_error("Playlist not found", 404)
    if not playlist.is_public and (not user_id or playlist.user_id != user_id):
        return api_error("Access denied to private playlist", 403)

    return jsonify({"playlist": playlist.to_dict(include_items=True)})


@api_bp.route("/playlists/<int:playlist_id>/items", methods=["POST"])
def add_playlist_item(playlist_id: int):
    """Add a media item to a playlist."""
    user_id = current_user.id if current_user.is_authenticated else None
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        return api_error("Playlist not found", 404)
    if playlist.user_id is not None and playlist.user_id != user_id:
        return api_error("Only the playlist owner can add items", 403)

    data = request.get_json(silent=True) or {}
    item_id = data.get("media_item_id") or data.get("item_id")
    if not item_id:
        return api_error("media_item_id is required", 400)

    media_item = db.session.get(MediaItem, item_id)
    if not media_item:
        return api_error("Media item not found", 404)

    curr_count = len(playlist.items)
    position = int(data.get("position", curr_count))

    playlist_item = PlaylistItem(
        playlist_id=playlist.id,
        media_item_id=media_item.id,
        position=position,
    )
    db.session.add(playlist_item)
    playlist.updated_at = datetime.now(UTC)
    db.session.commit()

    return jsonify({"status": "success", "item": playlist_item.to_dict()}), 201


@api_bp.route("/playlists/<int:playlist_id>/items/<int:item_id>", methods=["DELETE"])
def remove_playlist_item(playlist_id: int, item_id: int):
    """Remove a media item from a playlist."""
    user_id = current_user.id if current_user.is_authenticated else None
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        return api_error("Playlist not found", 404)
    if playlist.user_id is not None and playlist.user_id != user_id:
        return api_error("Only the playlist owner can remove items", 403)

    target_entry = db.session.scalar(
        select(PlaylistItem).where(
            PlaylistItem.playlist_id == playlist_id,
            or_(PlaylistItem.id == item_id, PlaylistItem.media_item_id == item_id),
        )
    )
    if not target_entry:
        return api_error("Playlist item entry not found", 404)

    db.session.delete(target_entry)
    playlist.updated_at = datetime.now(UTC)
    db.session.commit()
    return jsonify({"status": "success", "message": "Item removed from playlist"})


@api_bp.route("/playlists/<int:playlist_id>/reorder", methods=["PUT", "POST"])
def reorder_playlist_items(playlist_id: int):
    """Reorder items in a playlist."""
    user_id = current_user.id if current_user.is_authenticated else None
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        return api_error("Playlist not found", 404)
    if playlist.user_id is not None and playlist.user_id != user_id:
        return api_error("Only the playlist owner can reorder items", 403)

    data = request.get_json(silent=True) or {}
    item_ids = data.get("item_ids", [])
    if not isinstance(item_ids, list):
        return api_error("item_ids list is required", 400)

    for idx, mid in enumerate(item_ids):
        db.session.execute(
            update(PlaylistItem)
            .where(
                PlaylistItem.playlist_id == playlist_id,
                or_(PlaylistItem.id == mid, PlaylistItem.media_item_id == mid),
            )
            .values(position=idx)
        )

    playlist.updated_at = datetime.now(UTC)
    db.session.commit()
    return jsonify({"status": "success", "message": "Playlist reordered"})


@api_bp.route("/playlists/<int:playlist_id>", methods=["DELETE"])
def delete_playlist(playlist_id: int):
    """Delete a playlist."""
    user_id = current_user.id if current_user.is_authenticated else None
    playlist = db.session.get(Playlist, playlist_id)
    if not playlist:
        return api_error("Playlist not found", 404)
    if playlist.user_id is not None and playlist.user_id != user_id:
        return api_error("Only the playlist owner can delete this playlist", 403)

    db.session.delete(playlist)
    db.session.commit()
    return jsonify({"status": "success", "message": "Playlist deleted"})
