from __future__ import annotations

import io
import mimetypes
import os
import zipfile
from datetime import UTC, datetime
from functools import wraps
from http import HTTPStatus
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    g,
    jsonify,
    render_template,
    request,
    send_file,
    url_for,
)
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required, login_user
from sqlalchemy import or_, select, update
from sqlalchemy.orm import selectinload

from aarkib.extensions import db, safe_commit
from aarkib.models import (
    Collection,
    Creator,
    Library,
    MediaItem,
    Tag,
    User,
    UserProgress,
)
from aarkib.services.job_manager import job_manager
from aarkib.services.media_service import (
    AUDIO_EXTENSIONS,
    MEDIA_TYPE_CHOICES,
    VIDEO_EXTENSIONS,
    count_media_in_library,
    generate_slug,
    library_path_conditions,
    path_match_filter,
    resolve_library,
)
from aarkib.services.parsers.cbz import IMAGE_EXTENSIONS, natural_sort_key
from aarkib.services.playlist_service import (
    add_playlist_item as add_playlist_item_service,
)
from aarkib.services.playlist_service import (
    create_playlist as create_playlist_service,
)
from aarkib.services.playlist_service import (
    delete_playlist as delete_playlist_service,
)
from aarkib.services.playlist_service import (
    get_playlist as get_playlist_service,
)
from aarkib.services.playlist_service import (
    list_favorites as list_favorites_service,
)
from aarkib.services.playlist_service import (
    list_playlists as list_playlists_service,
)
from aarkib.services.playlist_service import (
    remove_playlist_item as remove_playlist_item_service,
)
from aarkib.services.playlist_service import (
    reorder_playlist_items as reorder_playlist_items_service,
)
from aarkib.services.playlist_service import (
    toggle_favorite as toggle_favorite_service,
)
from aarkib.services.progress_service import (
    add_bookmark as add_bookmark_service,
)
from aarkib.services.progress_service import (
    delete_bookmark as delete_bookmark_service,
)
from aarkib.services.progress_service import (
    get_progress as get_progress_service,
)
from aarkib.services.progress_service import (
    get_progress_for_items as get_progress_for_items_service,
)
from aarkib.services.progress_service import (
    list_bookmarks as list_bookmarks_service,
)
from aarkib.services.progress_service import (
    update_progress as update_progress_service,
)
from aarkib.services.scanner import scan_library

api_bp = Blueprint("api", __name__, url_prefix="/api")

MAX_PER_PAGE = 100


def api_error(
    message: str, status: int | HTTPStatus = HTTPStatus.BAD_REQUEST
) -> ResponseReturnValue:
    """Return a standardized JSON error envelope."""
    return jsonify({"error": message}), int(status)


def _is_within_covers(file_path: Path) -> bool:
    """Return True if a path resolves inside the configured covers directory.

    Guards the cover endpoint against path traversal via a tampered
    ``cover_image_path`` value combined with the covers directory base.
    """
    covers_dir = Path(current_app.config["COVERS_DIR"]).resolve()
    return file_path.resolve().is_relative_to(covers_dir)


def is_safe_media_path(file_path: Path | str) -> bool:
    """Return True if file_path resolves within configured library, media, or data roots.

    Guards media streaming and download endpoints against path traversal,
    absolute-path injection, and symlink escapes outside permitted directories.
    """
    try:
        resolved = Path(file_path).resolve()
    except OSError, RuntimeError, ValueError:
        return False

    allowed_roots: list[Path] = []

    # 1. Configured app directories
    for key in (
        "MEDIA_DIRS",
        "MEDIA_DIR",
        "LIBRARY_DIRS",
        "LIBRARY_DIR",
        "DATA_DIR",
        "COVERS_DIR",
        "OPTIMIZED_DIR",
        "TRANSCODE_DIR",
        "BACKUP_DIR",
        "BACKUPS_DIR",
    ):
        val = current_app.config.get(key)
        if isinstance(val, (list, tuple, set)):
            for p in val:
                try:
                    allowed_roots.append(Path(p).resolve())
                except Exception:
                    pass
        elif val:
            try:
                allowed_roots.append(Path(val).resolve())
            except Exception:
                pass

    # In testing mode, also allow parent of DATA_DIR (tmp_path fixture)
    if current_app.config.get("TESTING"):
        data_dir = current_app.config.get("DATA_DIR")
        if data_dir:
            try:
                allowed_roots.append(Path(data_dir).resolve().parent)
            except Exception:
                pass

    # 2. Database library roots
    try:
        from aarkib.models.library import Library

        lib_paths = db.session.execute(select(Library.path)).scalars().all()
        for lp in lib_paths:
            if lp:
                try:
                    allowed_roots.append(Path(lp).resolve())
                except Exception:
                    pass
    except Exception as e:
        current_app.logger.debug(
            "Error querying library roots for path validation: %s", e
        )

    for root in allowed_roots:
        try:
            if resolved.is_relative_to(root):
                return True
        except ValueError, AttributeError:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue

    return False


def api_admin_required(view):
    """Require an authenticated admin user for API endpoints.

    Unlike the generic ``admin_required`` in auth.py (which redirects to the UI
    for HTML pages), this returns a 401/403 JSON response suitable for API
    clients.
    """

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        """Reject non-admin requests with a JSON 403 before calling the view."""
        token = getattr(g, "device_token", None)
        if token is not None and not token.has_scope("admin"):
            return (
                jsonify({"error": "Device token missing required 'admin' scope"}),
                HTTPStatus.FORBIDDEN,
            )
        if not current_user.is_admin:
            return (
                jsonify({"error": "Administrator privileges required"}),
                HTTPStatus.FORBIDDEN,
            )
        return view(*args, **kwargs)

    return wrapped


def require_token_scope(required_scope: str):
    """Enforce that if the request was authenticated via a DeviceToken, it holds required_scope."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            token = getattr(g, "device_token", None)
            if token is not None and not token.has_scope(required_scope):
                return (
                    jsonify(
                        {
                            "error": f"Device token missing required scope: '{required_scope}'"
                        }
                    ),
                    HTTPStatus.FORBIDDEN,
                )
            return view(*args, **kwargs)

        return wrapped

    return decorator


@api_bp.before_request
def enforce_api_auth():
    """Authenticate the API request (Bearer token, HTTP Basic, or session) and enforce auth settings."""
    # Endpoints exempt from authentication
    AUTH_EXEMPT_ENDPOINTS = {
        "api.get_media_cover",
        "api.health",
        "api.api_login",
        "api.request_device_code",
        "api.poll_device_token",
        "api.api_docs",
        "api.openapi_json",
    }
    if request.endpoint in AUTH_EXEMPT_ENDPOINTS:
        return None

    # Authenticate via Bearer token (DeviceToken) if provided
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        raw_token = auth_header[7:].strip()
        from aarkib.models.token import DeviceToken

        token_hash = DeviceToken.hash_token(raw_token)
        token_record = db.session.scalar(
            select(DeviceToken).where(DeviceToken.token_hash == token_hash)
        )
        if token_record:
            # Check expiration
            if token_record.expires_at:
                now_utc = datetime.now(UTC)
                exp_utc = (
                    token_record.expires_at.replace(tzinfo=UTC)
                    if token_record.expires_at.tzinfo is None
                    else token_record.expires_at
                )
                if exp_utc < now_utc:
                    return (
                        jsonify({"error": "Device token has expired"}),
                        HTTPStatus.UNAUTHORIZED,
                    )

            token_record.last_used_at = datetime.now(UTC)
            db.session.commit()
            g.device_token = token_record
            login_user(token_record.user)
        else:
            return (
                jsonify({"error": "Invalid API token"}),
                HTTPStatus.UNAUTHORIZED,
            )

    # Authenticate via HTTP Basic auth if credentials are provided
    auth = request.authorization
    if auth and auth.username:
        from aarkib.services.security import auth_rate_limiter, get_client_ip

        client_ip = get_client_ip()
        limited, retry_after = auth_rate_limiter.is_rate_limited(client_ip)
        if limited:
            return (
                jsonify(
                    {
                        "error": f"Too many failed login attempts. Retry after {retry_after}s."
                    }
                ),
                HTTPStatus.TOO_MANY_REQUESTS,
                {"Retry-After": str(retry_after)},
            )

        user = db.session.scalar(select(User).where(User.username == auth.username))
        allow_remote_pwless = current_app.config.get("ALLOW_PASSWORDLESS_REMOTE", False)
        if user and user.check_password(
            auth.password or "",
            client_ip=client_ip,
            allow_remote_passwordless=allow_remote_pwless,
        ):
            auth_rate_limiter.reset(client_ip)
            login_user(user)
        else:
            auth_rate_limiter.record_failure(client_ip)
            return (
                jsonify({"error": "Invalid credentials"}),
                HTTPStatus.UNAUTHORIZED,
            )

    if not current_user.is_authenticated:
        return jsonify({"error": "Authentication required"}), HTTPStatus.UNAUTHORIZED


@api_bp.route("/health", methods=["GET"])
def health() -> ResponseReturnValue:
    """Healthcheck endpoint for container monitoring."""
    return jsonify({"status": "healthy", "app": "aarkib"})


# ---------------------------------------------------------------------------
# Native Mobile & TV Authentication & Pairing Endpoints
# ---------------------------------------------------------------------------


@api_bp.route("/auth/login", methods=["POST"])
def api_login() -> ResponseReturnValue:
    """Authenticate with username and password, returning an API Bearer token."""
    from aarkib.services.security import auth_rate_limiter, get_client_ip

    client_ip = get_client_ip()
    limited, retry_after = auth_rate_limiter.is_rate_limited(client_ip)
    if limited:
        return (
            jsonify(
                {
                    "error": f"Too many failed login attempts. Retry after {retry_after}s."
                }
            ),
            HTTPStatus.TOO_MANY_REQUESTS,
            {"Retry-After": str(retry_after)},
        )

    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    device_name = str(data.get("device_name", "")).strip() or "API Client"

    if not username:
        return api_error("Username is required", 400)

    user = db.session.scalar(select(User).where(User.username == username))
    allow_remote_pwless = current_app.config.get("ALLOW_PASSWORDLESS_REMOTE", False)

    if user and user.check_password(
        password,
        client_ip=client_ip,
        allow_remote_passwordless=allow_remote_pwless,
    ):
        auth_rate_limiter.reset(client_ip)
        from aarkib.models.token import DeviceToken

        token_obj, raw_token = DeviceToken.create_token(
            user_id=user.id,
            name=device_name,
            scopes=["*"],
        )
        db.session.add(token_obj)
        safe_commit()
        return jsonify(
            {
                "status": "success",
                "token": raw_token,
                "token_type": "Bearer",
                "token_id": token_obj.id,
                "token_prefix": token_obj.token_prefix,
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "is_admin": user.is_admin,
                },
            }
        )
    else:
        auth_rate_limiter.record_failure(client_ip)
        return api_error("Invalid credentials", 401)


@api_bp.route("/auth/device-code", methods=["POST"])
def request_device_code() -> ResponseReturnValue:
    """Request a TV / 10-foot device pairing code."""
    from aarkib.services.device_auth_service import create_device_pairing_code

    data = request.get_json(silent=True) or {}
    device_name = str(data.get("device_name", "")).strip() or "TV Client"
    _, device_code, user_code = create_device_pairing_code(device_name=device_name)
    verification_url = url_for("ui.pair_device", _external=True)
    return jsonify(
        {
            "status": "success",
            "device_code": device_code,
            "user_code": user_code,
            "verification_url": verification_url,
            "verification_uri": verification_url,
            "verification_uri_complete": f"{verification_url}?code={user_code}",
            "expires_in": 300,
            "interval": 5,
        }
    )


@api_bp.route("/auth/device-code/token", methods=["POST"])
def poll_device_token() -> ResponseReturnValue:
    """Poll for token after TV user authorizes the pairing code."""
    from aarkib.services.device_auth_service import poll_device_pairing_code

    data = request.get_json(silent=True) or {}
    device_code = str(data.get("device_code", "")).strip()
    if not device_code:
        return api_error("device_code is required", 400)

    res = poll_device_pairing_code(device_code)
    status_str = res.get("status")
    if status_str in ("error", "pending"):
        return jsonify(res), 400
    return jsonify(res), 200


# ---------------------------------------------------------------------------
# Home Screen Feed & Taxonomy REST Endpoints
# ---------------------------------------------------------------------------


@api_bp.route("/home", methods=["GET"])
@require_token_scope("media:read")
def home_feed() -> ResponseReturnValue:
    """Retrieve aggregated rails for mobile and TV home screen dashboards."""
    from aarkib.services.media_service import get_home_feed_service

    user_id = current_user.id if current_user.is_authenticated else None
    feed = get_home_feed_service(user_id)
    return jsonify({"status": "success", **feed})


@api_bp.route("/creators", methods=["GET"])
@api_bp.route("/authors", methods=["GET"])
@require_token_scope("media:read")
def list_creators() -> ResponseReturnValue:
    """List creators / authors with filtering, search, and pagination."""
    from aarkib.services.media_service import list_creators_service

    q = request.args.get("q")
    media_type = request.args.get("media_type") or request.args.get("type")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(request.args.get("per_page", 24, type=int), 100))
    sort = request.args.get("sort", "name")
    return jsonify(
        list_creators_service(
            media_type=media_type,
            query=q,
            page=page,
            per_page=per_page,
            sort_by=sort,
        )
    )


@api_bp.route("/creators/<int:creator_id>", methods=["GET"])
@api_bp.route("/authors/<int:creator_id>", methods=["GET"])
@require_token_scope("media:read")
def get_creator_detail(creator_id: int) -> ResponseReturnValue:
    """Get creator / author details and their media items."""
    from aarkib.services.media_service import get_creator_detail_service

    res = get_creator_detail_service(creator_id)
    if not res:
        return api_error("Creator not found", 404)
    return jsonify(res)


@api_bp.route("/collections", methods=["GET"])
@api_bp.route("/series", methods=["GET"])
@require_token_scope("media:read")
def list_collections() -> ResponseReturnValue:
    """List collections / series with filtering and pagination."""
    from aarkib.services.media_service import list_collections_service

    q = request.args.get("q")
    media_type = request.args.get("media_type") or request.args.get("type")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(request.args.get("per_page", 24, type=int), 100))
    sort = request.args.get("sort", "name")
    return jsonify(
        list_collections_service(
            media_type=media_type,
            query=q,
            page=page,
            per_page=per_page,
            sort_by=sort,
        )
    )


@api_bp.route("/collections/<int:collection_id>", methods=["GET"])
@api_bp.route("/series/<int:collection_id>", methods=["GET"])
@require_token_scope("media:read")
def get_collection_detail(collection_id: int) -> ResponseReturnValue:
    """Get collection / series details and its ordered media items."""
    from aarkib.services.media_service import get_collection_detail_service

    res = get_collection_detail_service(collection_id)
    if not res:
        return api_error("Collection not found", 404)
    return jsonify(res)


@api_bp.route("/tags", methods=["GET"])
@require_token_scope("media:read")
def list_tags() -> ResponseReturnValue:
    """List tags / genres with media counts."""
    from aarkib.services.media_service import list_tags_service

    q = request.args.get("q")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(request.args.get("per_page", 50, type=int), 100))
    return jsonify(list_tags_service(query=q, page=page, per_page=per_page))


# ---------------------------------------------------------------------------
# OpenAPI Specification & Interactive Documentation Explorer
# ---------------------------------------------------------------------------


@api_bp.route("/openapi.json", methods=["GET"])
def openapi_json() -> ResponseReturnValue:
    """Serve OpenAPI 3.1 specification as JSON."""
    spec_path = Path(__file__).resolve().parent.parent / "static" / "openapi.json"
    if not spec_path.is_file():
        abort(404, description="OpenAPI specification not found")
    return send_file(spec_path, mimetype="application/json")


@api_bp.route("/docs", methods=["GET"])
def api_docs() -> ResponseReturnValue:
    """Interactive API documentation explorer for developers."""
    return render_template("swagger_ui.html")


@api_bp.route("/plugins", methods=["GET"])
def list_plugins() -> ResponseReturnValue:
    """Lists all registered plugins with their status, type, and health metrics."""
    from aarkib.plugins import plugin_registry

    plugins_data = [
        {
            "name": plugin.name,
            "display_name": plugin.display_name or plugin.name.title(),
            "type": plugin.plugin_type,
            "description": plugin.description,
            "enabled": plugin.enabled,
            "config_keys": getattr(plugin, "config_keys", []),
            "health": plugin.check_health(),
        }
        for plugin in plugin_registry.get_all_plugins()
    ]
    return jsonify({"plugins": plugins_data})


@api_bp.route("/jobs", methods=["GET"])
def list_jobs() -> ResponseReturnValue:
    """List recent background tasks and their execution states."""
    limit = min(request.args.get("limit", 20, type=int), 100)
    jobs = job_manager.list_jobs(limit=limit)
    return jsonify({"jobs": [j.to_dict() for j in jobs]})


@api_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str) -> ResponseReturnValue:
    """Retrieve details, progress, and results for a specific background job."""
    job = job_manager.get_job(job_id)
    if not job:
        return api_error("Job not found", 404)
    return jsonify(job.to_dict())


@api_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@api_admin_required
def cancel_job(job_id: str) -> ResponseReturnValue:
    """Cancel a queued or running background job."""
    res = job_manager.cancel_job(job_id, app=current_app._get_current_object())
    if not res:
        return api_error("Job not found", 404)
    if not res.get("cancelled"):
        return api_error(res.get("message", "Job could not be cancelled"), 400)
    return jsonify({"status": "success", "job": res})


@api_bp.route("/media", methods=["GET"])
@require_token_scope("media:read")
def list_media() -> ResponseReturnValue:
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
    in_progress = request.args.get("in_progress", "").lower() in ("true", "1", "yes")
    sort_by = request.args.get("sort", "added_at")
    order = request.args.get("order", "desc")
    raw_page = request.args.get("page", 1, type=int)
    page = max(1, raw_page if raw_page is not None else 1)
    raw_per_page = request.args.get(
        "per_page", current_app.config.get("PAGE_SIZE", 24), type=int
    )
    per_page = max(1, min(raw_per_page or 24, MAX_PER_PAGE))

    query = select(MediaItem).options(
        selectinload(MediaItem.creators),
        selectinload(MediaItem.collection),
        selectinload(MediaItem.tags),
    )

    relevance_order = False
    if q:
        from aarkib.services.search import search_media_ids

        # Attempt FTS5 search
        matching_ids = search_media_ids(q, limit=2000)
        if matching_ids:
            query = query.filter(MediaItem.id.in_(matching_ids))
            if sort_by in ("relevance", "rank") or (
                sort_by == "added_at" and "sort" not in request.args
            ):
                from sqlalchemy import case

                relevance_order = True
                order_case = case(
                    {mid: idx for idx, mid in enumerate(matching_ids)},
                    value=MediaItem.id,
                )
                query = query.order_by(order_case.asc())
        elif matching_ids == []:
            query = query.filter(MediaItem.id == -1)
        else:
            search_filter = or_(
                MediaItem.title.ilike(f"%{q}%"),
                MediaItem.description.ilike(f"%{q}%"),
                MediaItem.creators.any(Creator.name.ilike(f"%{q}%")),
                MediaItem.tags.any(Tag.name.ilike(f"%{q}%")),
                MediaItem.collection.has(Collection.name.ilike(f"%{q}%")),
            )
            query = query.filter(search_filter)

    if author_id:
        query = query.filter(MediaItem.creators.any(Creator.id == author_id))
    if series_id:
        query = query.filter(MediaItem.collection_id == series_id)
    if tag_id:
        query = query.filter(MediaItem.tags.any(Tag.id == tag_id))
    if file_format:
        query = query.filter(MediaItem.file_format == file_format)
    if media_type:
        if media_type == "audio":
            query = query.filter(
                MediaItem.media_type.in_(["audio", "audiobook", "music", "podcast"])
            )
        elif media_type == "book":
            query = query.filter(MediaItem.media_type.in_(["book", "comic"]))
        elif media_type == "movie":
            query = query.filter(
                or_(
                    MediaItem.media_type == "movie",
                    (MediaItem.media_type == "video")
                    & MediaItem.season.is_(None)
                    & MediaItem.episode.is_(None),
                )
            )
        elif media_type == "tv":
            query = query.filter(
                or_(
                    MediaItem.media_type == "tv",
                    (MediaItem.media_type == "video")
                    & (MediaItem.season.is_not(None) | MediaItem.episode.is_not(None)),
                )
            )
        elif media_type == "video":
            query = query.filter(MediaItem.media_type.in_(["video", "movie", "tv"]))
        else:
            query = query.filter(MediaItem.media_type == media_type)
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
            query = query.filter(MediaItem.original_file_path.startswith(prefix))

    # User progress filtering
    if in_progress:
        user_id = current_user.id if current_user.is_authenticated else None
        if user_id:
            in_prog_subq = select(UserProgress.media_item_id).where(
                UserProgress.user_id == user_id,
                UserProgress.is_completed.is_(False),
                UserProgress.percentage > 0,
            )
            query = query.filter(MediaItem.id.in_(in_prog_subq))
        else:
            query = query.filter(MediaItem.id == -1)

    # Sorting
    if not relevance_order:
        if sort_by == "title":
            col = (
                MediaItem.sort_title
                if hasattr(MediaItem, "sort_title")
                else MediaItem.title
            )
        elif sort_by == "series_index":
            col = MediaItem.series_index
        elif sort_by == "author":
            col = MediaItem.title
        else:
            col = MediaItem.created_at

        query = query.order_by(col.desc() if order == "desc" else col.asc())

    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)

    # Fetch user progress if user is authenticated or guest
    user_id = current_user.id if current_user.is_authenticated else None
    item_ids = [b.id for b in pagination.items]
    raw_progress = get_progress_for_items_service(user_id, item_ids) if item_ids else {}
    progress_map = {
        item_id: {
            "percentage": p["percentage"],
            "location": p["location"],
            "completed": p["is_completed"],
        }
        for item_id, p in raw_progress.items()
    }

    items = []
    for b in pagination.items:
        items.append(
            {
                "id": b.id,
                "title": b.title,
                "media_type": b.media_type,
                "creators": [a.name for a in b.creators],
                "creators_display": b.creators_display,
                "file_format": b.file_format,
                "file_size": b.file_size,
                "cover_url": f"/api/media/{b.id}/cover",
                "player_url": b.player_url,
                "collection": b.collection.name if b.collection else None,
                "collection_id": b.collection_id,
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
            "items": items,
            "page": pagination.page,
            "pages": pagination.pages,
            "total": pagination.total,
            "has_prev": pagination.has_prev,
            "has_next": pagination.has_next,
        }
    )


@api_bp.route("/search", methods=["GET"])
def search_catalog() -> ResponseReturnValue:
    """Unified full-text search returning results grouped by media type."""
    q = request.args.get("q", "").strip()
    library_id = request.args.get("library_id", type=int)
    limit = min(request.args.get("limit", 8, type=int), 50)

    from aarkib.services.search import search_grouped

    results = search_grouped(q=q, library_id=library_id, limit_per_group=limit)
    return jsonify(results)


@api_bp.route("/search/reindex", methods=["POST"])
@api_admin_required
def reindex_search() -> ResponseReturnValue:
    """Admin-only endpoint to trigger a complete rebuild of the SQLite FTS5 index."""
    from aarkib.services.search import rebuild_search_index

    count = rebuild_search_index()
    return jsonify(
        {
            "status": "success",
            "message": f"FTS5 search index rebuilt successfully ({count} items indexed).",
            "indexed_count": count,
        }
    )


@api_bp.route("/fs/directories", methods=["GET"])
@api_admin_required
def browse_directories() -> ResponseReturnValue:
    """Browse server directories for interactive folder selection (Admin only)."""
    raw_path = request.args.get("path", "").strip()
    if not raw_path:
        raw_path = current_app.config.get("DATA_DIR", "data")

    try:
        p = Path(raw_path).expanduser().resolve()
        if not p.exists() or not p.is_dir():
            data_dir = Path(current_app.config.get("DATA_DIR", "data")).resolve()
            p = (
                data_dir
                if data_dir.exists() and data_dir.is_dir()
                else Path("/").resolve()
            )
    except Exception as e:
        return jsonify({"error": f"Invalid directory path: {e}"}), 400

    subdirs = []
    try:
        with os.scandir(p) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(
                        follow_symlinks=False
                    ) and not entry.name.startswith("."):
                        subdirs.append(
                            {
                                "name": entry.name,
                                "path": str(Path(entry.path).resolve()),
                            }
                        )
                except PermissionError, OSError:
                    continue
    except PermissionError:
        return (
            jsonify({"error": f"Permission denied reading directory: {p}"}),
            HTTPStatus.FORBIDDEN,
        )
    except Exception as e:
        return jsonify(
            {"error": f"Error reading directory: {e}"}
        ), HTTPStatus.BAD_REQUEST

    subdirs.sort(key=lambda x: x["name"].lower())

    parent_path = str(p.parent) if p.parent != p else None

    quick_locations = []
    data_dir_path = Path(current_app.config.get("DATA_DIR", "data")).resolve()
    media_dir_path = Path(
        current_app.config.get("MEDIA_DIR", data_dir_path / "media")
    ).resolve()

    candidates: list[tuple[str, Path]] = [
        ("Data Root", data_dir_path),
        ("Media Folder", media_dir_path),
    ]

    for md in current_app.config.get("MEDIA_DIRS", []):
        try:
            p_md = Path(md).resolve()
            candidates.append((f"Media ({p_md.name or str(p_md)})", p_md))
        except Exception:
            continue

    try:
        home_path = Path.home().resolve()
        candidates.append(("User Home (~)", home_path))
    except Exception:
        pass

    candidates.extend(
        [
            ("System Root (/)", Path("/").resolve()),
            ("Mounted Media (/media)", Path("/media").resolve()),
            ("Mounts (/mnt)", Path("/mnt").resolve()),
            ("App Data (/app/data)", Path("/app/data").resolve()),
        ]
    )
    for label, candidate_path in candidates:
        try:
            if candidate_path.exists() and candidate_path.is_dir():
                path_str = str(candidate_path)
                if not any(loc["path"] == path_str for loc in quick_locations):
                    quick_locations.append({"label": label, "path": path_str})
        except Exception:
            continue

    return jsonify(
        {
            "status": "success",
            "current_path": str(p),
            "parent_path": parent_path,
            "directories": subdirs,
            "quick_locations": quick_locations,
        }
    )


@api_bp.route("/libraries", methods=["GET"])
def list_libraries() -> ResponseReturnValue:
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
def add_library() -> ResponseReturnValue:
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

    import json

    slug = generate_slug(name if name else folder_name)
    settings_val = data.get("settings")
    settings_json = json.dumps(settings_val) if isinstance(settings_val, dict) else None

    new_lib = Library(
        slug=slug,
        name=name,
        path=norm_path,
        media_type=media_type,
        settings_json=settings_json,
    )
    db.session.add(new_lib)
    try:
        safe_commit()
    except Exception as e:
        current_app.logger.error("Failed to create library %s: %s", slug, e)
        return api_error(
            "Failed to create library: slug or path may already exist", 409
        )

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
def get_library_info(identifier: str) -> ResponseReturnValue:
    """Return details for a single library by ID or slug."""
    try:
        lib = resolve_library(identifier)
    except KeyError:
        return jsonify({"error": "Library not found"}), 404

    return jsonify({"library": lib.to_dict(count=count_media_in_library(lib))})


@api_bp.route("/libraries/<identifier>", methods=["PUT"])
@api_admin_required
def update_library(identifier: str) -> ResponseReturnValue:
    """Update a library's name/media_type and reclassify indexed books accordingly."""
    try:
        lib = resolve_library(identifier)
    except KeyError:
        return jsonify({"error": "Library not found"}), 404

    data = request.get_json(silent=True) or {}
    if "name" in data and str(data["name"]).strip():
        lib.name = str(data["name"]).strip()

    if "settings" in data and isinstance(data["settings"], dict):
        import json

        lib.settings_json = json.dumps(data["settings"])

    if "media_type" in data:
        new_media_type = str(data["media_type"]).strip().lower()
        if new_media_type in MEDIA_TYPE_CHOICES:
            lib.media_type = new_media_type

            # Propagate media_type update to all items indexed in this folder
            p_res, p_raw = library_path_conditions(lib)
            cond = or_(
                MediaItem.original_file_path.startswith(p_res),
                MediaItem.original_file_path.startswith(p_raw),
            )
            if new_media_type != "all":
                db.session.execute(
                    update(MediaItem).where(cond).values(media_type=new_media_type)
                )
            else:
                items = db.session.scalars(select(MediaItem).where(cond)).all()
                for b in items:
                    if b.file_format in ("cbz", "cbr", "zip"):
                        b.media_type = "comic"
                    elif b.file_format in VIDEO_EXTENSIONS:
                        b.media_type = (
                            "tv"
                            if (
                                getattr(b, "season", None) is not None
                                or getattr(b, "episode", None) is not None
                            )
                            else "movie"
                        )
                    elif b.file_format == "m4b" or (
                        hasattr(b, "chapters") and b.chapters
                    ):
                        b.media_type = "audiobook"
                    elif b.file_format in AUDIO_EXTENSIONS:
                        b.media_type = "music"
                    else:
                        b.media_type = "book"

    lib.updated_at = datetime.now(UTC)
    safe_commit()

    return jsonify(
        {
            "status": "success",
            "library": lib.to_dict(count=count_media_in_library(lib)),
        }
    )


@api_bp.route("/libraries/<identifier>", methods=["DELETE"])
@api_admin_required
def delete_library(identifier: str) -> ResponseReturnValue:
    """Delete a library and all catalog items indexed under its folder."""
    try:
        lib = resolve_library(identifier)
    except KeyError:
        return jsonify({"error": "Library not found"}), 404

    # Remove items indexed under this library
    p_res, p_raw = library_path_conditions(lib)
    cond = or_(
        MediaItem.original_file_path.startswith(p_res),
        MediaItem.original_file_path.startswith(p_raw),
    )
    items = db.session.scalars(select(MediaItem).where(cond)).all()
    deleted_count = len(items)
    item_ids = [b.id for b in items]
    for b in items:
        db.session.delete(b)

    db.session.delete(lib)
    safe_commit()

    from aarkib.services.search import remove_media_item_fts

    for bid in item_ids:
        remove_media_item_fts(bid)

    return jsonify(
        {
            "status": "success",
            "deleted_id": identifier,
            "deleted_items": deleted_count,
            "deleted_books": deleted_count,
        }
    )


@api_bp.route("/libraries/<identifier>/scan", methods=["POST"])
@api_admin_required
def scan_single_library(identifier: str) -> ResponseReturnValue:
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


@api_bp.route("/media/<int:item_id>", methods=["GET"])
@require_token_scope("media:read")
def get_media_item(item_id: int) -> ResponseReturnValue:
    """Return full item details, including user progress, for a single catalog item."""
    item = db.session.scalar(
        select(MediaItem)
        .options(
            selectinload(MediaItem.creators),
            selectinload(MediaItem.collection),
            selectinload(MediaItem.tags),
        )
        .where(MediaItem.id == item_id)
    )
    if not item:
        return api_error("Media item not found", 404)

    user_id = current_user.id if current_user.is_authenticated else None
    prog_data = get_progress_service(user_id, item)
    prog = (
        {
            "percentage": prog_data["percentage"],
            "location": prog_data["location"],
            "is_completed": prog_data["is_completed"],
            "last_accessed_at": prog_data.get("last_accessed_at"),
        }
        if prog_data.get("last_accessed_at")
        else None
    )

    return jsonify(
        {
            "id": item.id,
            "title": item.title,
            "media_type": item.media_type,
            "creators": [a.name for a in item.creators],
            "creators_display": item.creators_display,
            "description": item.description,
            "publisher": item.publisher,
            "language": item.language,
            "isbn": item.isbn,
            "publication_date": item.publication_date,
            "file_format": item.file_format,
            "file_size": item.file_size,
            "collection": item.collection.name if item.collection else None,
            "collection_id": item.collection_id,
            "series_index": item.series_index,
            "tags": [t.name for t in item.tags],
            "page_count": item.page_count,
            "duration": item.duration,
            "formatted_duration": item.formatted_duration,
            "resolution_width": item.resolution_width,
            "resolution_height": item.resolution_height,
            "resolution_label": item.resolution_label,
            "codec": item.codec,
            "season": item.season,
            "episode": item.episode,
            "episode_code": item.episode_code,
            "author": getattr(item, "author", None),
            "narrator": getattr(item, "narrator", None),
            "chapters": item.chapters if hasattr(item, "chapters") else [],
            "abridged": getattr(item, "abridged", False),
            "album": getattr(item, "album", None),
            "album_artist": getattr(item, "album_artist", None),
            "genre": getattr(item, "genre", None),
            "release_year": getattr(item, "release_year", None),
            "track_number": getattr(item, "track_number", None),
            "disc_number": getattr(item, "disc_number", None),
            "is_compilation": getattr(item, "is_compilation", False),
            "cover_url": f"/api/media/{item.id}/cover",
            "download_url": f"/api/media/{item.id}/download",
            "file_url": f"/api/media/{item.id}/file",
            "player_url": item.player_url,
            "progress": prog,
            "locked_fields": item.get_locked_fields(),
            "provenance": item.get_field_provenance(),
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
    )


@api_bp.route("/media/<int:item_id>/cover", methods=["GET"])
def get_media_cover(item_id: int) -> ResponseReturnValue:
    """Serve the cached WebP cover/poster image for a media item."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        abort(404)

    if item.cover_image_path:
        covers_dir = Path(current_app.config["COVERS_DIR"])
        cover_file = covers_dir / item.cover_image_path
        if cover_file.exists() and _is_within_covers(cover_file):
            db.session.close()
            return send_file(cover_file, mimetype="image/webp")

    # Generate fallback SVG cover
    if item.is_video:
        title = item.title[:30] + ("..." if len(item.title) > 30 else "")
        sub = (
            f"S{item.season:02d}E{item.episode:02d}"
            if (item.season is not None and item.episode is not None)
            else (
                item.publication_date
                or (item.file_format.upper() if item.file_format else "VIDEO")
            )
        )
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">
        <defs>
            <linearGradient id="vidGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stop-color="#0b0e14"/>
                <stop offset="100%" stop-color="#181f2e"/>
            </linearGradient>
        </defs>
        <rect width="300" height="450" fill="url(#vidGrad)" rx="16"/>
        <rect x="12" y="12" width="276" height="426" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1.5" rx="12"/>
        <text x="150" y="130" font-size="48" text-anchor="middle">🎬</text>
        <text x="150" y="210" fill="#f8fafc" font-size="18" font-family="'Plus Jakarta Sans', system-ui, sans-serif" font-weight="bold" text-anchor="middle">{title}</text>
        <text x="150" y="250" fill="#94a3b8" font-size="14" font-family="'Inter', system-ui, sans-serif" text-anchor="middle">{sub}</text>
        <rect x="90" y="295" width="120" height="28" rx="6" fill="#0f172a" stroke="rgba(255,255,255,0.15)" stroke-width="1"/>
        <text x="150" y="314" fill="#00d2ff" font-size="11" font-family="'Inter', system-ui, sans-serif" font-weight="bold" letter-spacing="1" text-anchor="middle">{(item.file_format or "video").upper()}</text>
        <text x="150" y="410" fill="#00d2ff" font-size="11" font-family="'Inter', system-ui, sans-serif" letter-spacing="2" font-weight="600" text-anchor="middle">AARKIB VIDEO</text>
    </svg>"""
        db.session.close()
        return (
            io.BytesIO(svg.encode("utf-8")).getvalue(),
            200,
            {"Content-Type": "image/svg+xml"},
        )

    title = item.title[:30] + ("..." if len(item.title) > 30 else "")
    author = item.creators_display[:25]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">
        <rect width="300" height="450" fill="#181f2e" rx="16"/>
        <rect x="12" y="12" width="276" height="426" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1.5" rx="12"/>
        <text x="150" y="160" fill="#f8fafc" font-size="20" font-family="'Plus Jakarta Sans', system-ui, sans-serif" font-weight="bold" text-anchor="middle">{title}</text>
        <text x="150" y="240" fill="#94a3b8" font-size="14" font-family="'Inter', system-ui, sans-serif" text-anchor="middle">{author}</text>
        <text x="150" y="380" fill="#00d2ff" font-size="11" font-family="'Inter', system-ui, sans-serif" letter-spacing="2" font-weight="bold" text-anchor="middle">AARKIB</text>
    </svg>"""
    db.session.close()
    return (
        io.BytesIO(svg.encode("utf-8")).getvalue(),
        200,
        {"Content-Type": "image/svg+xml"},
    )


@api_bp.route("/media/<int:item_id>/file", methods=["GET"])
@api_bp.route("/media/<int:item_id>/file/<path:filename>", methods=["GET"])
@api_bp.route("/media/<int:item_id>/stream", methods=["GET"])
@require_token_scope("media:stream")
def get_media_file(item_id: int, filename: str | None = None) -> ResponseReturnValue:
    """Stream the original media file with HTTP 206 byte-range support."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        return api_error(
            "Access denied: media file resides outside configured library roots", 403
        )
    if not file_path.is_file():
        return api_error("File missing from storage", 404)

    guessed, _ = mimetypes.guess_type(str(file_path))
    if guessed:
        mimetype = guessed
    elif item.file_format == "epub":
        mimetype = "application/epub+zip"
    elif item.file_format in ("cbz", "zip", "cbr"):
        mimetype = "application/vnd.comicbook+zip"
    elif item.file_format == "mp4":
        mimetype = "video/mp4"
    elif item.file_format == "webm":
        mimetype = "video/webm"
    elif item.file_format == "mkv":
        mimetype = "video/x-matroska"
    elif item.file_format in ("m4b", "m4a"):
        mimetype = "audio/mp4"
    elif item.file_format == "mp3":
        mimetype = "audio/mpeg"
    elif item.file_format == "flac":
        mimetype = "audio/flac"
    elif item.file_format == "wav":
        mimetype = "audio/wav"
    elif item.file_format == "ogg":
        mimetype = "audio/ogg"
    elif item.file_format == "opus":
        mimetype = "audio/opus"
    elif item.file_format == "aac":
        mimetype = "audio/aac"
    else:
        mimetype = "application/octet-stream"

    db.session.close()
    return send_file(file_path, mimetype=mimetype, conditional=True)


@api_bp.route("/media/<int:item_id>/playback", methods=["GET"])
@require_token_scope("media:read")
def get_media_playback(item_id: int) -> ResponseReturnValue:
    """Retrieve format-agnostic playback or reading descriptor for any media item."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    user_id = current_user.id if current_user.is_authenticated else None
    from aarkib.plugins import plugin_registry

    plugin = plugin_registry.get_plugin_for_media_type(item.media_type or "")
    if not plugin:
        plugin = plugin_registry.get_plugin_for_extension(f".{item.file_format}")

    if plugin:
        descriptor = plugin.get_playback_info(item, user_id=user_id)
    else:
        descriptor = {
            "media_id": item.id,
            "media_type": item.media_type,
            "title": item.title,
            "file_format": item.file_format,
            "file_url": f"/api/media/{item.id}/file",
            "cover_url": f"/api/media/{item.id}/cover",
            "player_url": item.player_url,
        }

    return jsonify({"status": "ok", "playback": descriptor})


# ---------------------------------------------------------------------------
# Streaming, Remuxing & Transcoding Endpoints (Phase 4)
# ---------------------------------------------------------------------------


@api_bp.route("/media/<int:item_id>/stream/info", methods=["GET"])
@api_bp.route("/stream/<int:item_id>/info", methods=["GET"])
@require_token_scope("media:read")
def get_stream_info(item_id: int) -> ResponseReturnValue:
    """Returns technical stream metadata, codecs, tracks, and recommended playback strategy."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        return api_error(
            "Access denied: media file resides outside configured library roots", 403
        )
    if not file_path.is_file():
        return api_error("File missing from storage", 404)

    item_id_val = item.id
    item_title = item.title
    item_format = item.file_format
    db.session.close()

    from aarkib.services.transcoder import (
        evaluate_playback_strategy,
        probe_media_streams,
    )

    streams = probe_media_streams(file_path)
    eval_res = evaluate_playback_strategy(file_path, streams)

    return jsonify(
        {
            "id": item_id_val,
            "title": item_title,
            "file_format": item_format,
            "original_file_path": str(file_path),
            "streams": streams,
            "evaluation": eval_res,
            "direct_url": f"/api/media/{item_id_val}/file",
            "remux_url": f"/api/media/{item_id_val}/stream/remux",
            "hls_url": f"/api/media/{item_id_val}/stream/hls/master.m3u8",
        }
    )


@api_bp.route("/media/<int:item_id>/stream/remux", methods=["GET"])
@api_bp.route("/stream/<int:item_id>/remux", methods=["GET"])
@require_token_scope("media:stream")
def stream_remux_video(item_id: int) -> ResponseReturnValue:
    """Progressive on-the-fly container remux (e.g. MKV -> fragmented MP4) via FFmpeg pipe."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        return api_error(
            "Access denied: media file resides outside configured library roots", 403
        )
    if not file_path.is_file():
        return api_error("File missing from storage", 404)

    from aarkib.services.transcoder import stream_remux_pipe

    seek_sec = request.args.get("start", 0.0, type=float)
    audio_transcode = request.args.get("audio_transcode", "false").lower() in (
        "true",
        "1",
        "yes",
    )

    db.session.close()
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


@api_bp.route("/media/<int:item_id>/stream/hls/master.m3u8", methods=["GET"])
@api_bp.route("/stream/<int:item_id>/hls/master.m3u8", methods=["GET"])
@require_token_scope("media:stream")
def get_hls_master_playlist(item_id: int) -> ResponseReturnValue:
    """Spawns/attaches to an HLS transcode session and returns the master playlist."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        return api_error(
            "Access denied: media file resides outside configured library roots", 403
        )
    if not file_path.is_file():
        return api_error("File missing from storage", 404)

    item_id_val = item.id
    db.session.close()

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
        media_item_id=item_id_val,
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
        f"/api/media/{item_id_val}/stream/hls/{session.session_id}/playlist.m3u8\n"
    )

    return Response(
        master_content,
        mimetype="application/vnd.apple.mpegurl",
        headers={
            "Content-Type": "application/vnd.apple.mpegurl",
            "Cache-Control": "no-cache",
        },
    )


@api_bp.route(
    "/media/<int:item_id>/stream/hls/<session_id>/playlist.m3u8", methods=["GET"]
)
@api_bp.route("/stream/<int:item_id>/hls/<session_id>/playlist.m3u8", methods=["GET"])
@require_token_scope("media:stream")
def get_hls_session_playlist(item_id: int, session_id: str) -> ResponseReturnValue:
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
    "/media/<int:item_id>/stream/hls/<session_id>/<path:segment_name>", methods=["GET"]
)
@api_bp.route(
    "/stream/<int:item_id>/hls/<session_id>/<path:segment_name>", methods=["GET"]
)
@require_token_scope("media:stream")
def get_hls_segment(
    item_id: int, session_id: str, segment_name: str
) -> ResponseReturnValue:
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


@api_bp.route(
    "/media/<int:item_id>/stream/hls/<session_id>/heartbeat", methods=["POST"]
)
@api_bp.route("/stream/<int:item_id>/hls/<session_id>/heartbeat", methods=["POST"])
def hls_heartbeat(item_id: int, session_id: str) -> ResponseReturnValue:
    """Client heartbeat ping to keep an active HLS transcode session alive."""
    from aarkib.services.transcoder import transcode_supervisor

    session = transcode_supervisor.get_session(session_id)
    if not session or not session.is_active:
        return api_error("Session not found or expired", 404)

    session.touch()
    return jsonify({"status": "ok", "session_id": session_id})


@api_bp.route("/media/<int:item_id>/stream/hls/<session_id>/stop", methods=["POST"])
@api_bp.route("/stream/<int:item_id>/hls/<session_id>/stop", methods=["POST"])
def stop_hls_session(item_id: int, session_id: str) -> ResponseReturnValue:
    """Explicitly stops a transcode session and prunes its scratch directory."""
    from aarkib.services.transcoder import transcode_supervisor

    transcode_supervisor.stop_session(session_id)
    return jsonify({"status": "stopped", "session_id": session_id})


@api_bp.route("/media/<int:item_id>/stream/subtitles", methods=["GET"])
@api_bp.route("/stream/<int:item_id>/subtitles", methods=["GET"])
@require_token_scope("media:read")
def list_subtitles(item_id: int) -> ResponseReturnValue:
    """Returns list of embedded subtitle tracks for a media item."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        return api_error(
            "Access denied: media file resides outside configured library roots", 403
        )
    if not file_path.is_file():
        return api_error("File missing from storage", 404)

    item_id_val = item.id
    db.session.close()

    from aarkib.services.transcoder import probe_media_streams

    streams = probe_media_streams(file_path)
    return jsonify(
        {
            "id": item_id_val,
            "subtitles": streams.get("subtitles", []),
        }
    )


@api_bp.route(
    "/media/<int:item_id>/stream/subtitles/<int:track_index>.vtt", methods=["GET"]
)
@api_bp.route("/stream/<int:item_id>/subtitles/<int:track_index>.vtt", methods=["GET"])
@require_token_scope("media:read")
def get_subtitle_vtt(item_id: int, track_index: int) -> ResponseReturnValue:
    """Extracts and converts the requested embedded subtitle track to WebVTT."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        return api_error(
            "Access denied: media file resides outside configured library roots", 403
        )
    if not file_path.is_file():
        return api_error("File missing from storage", 404)

    db.session.close()

    from aarkib.services.transcoder import generate_vtt_subtitles

    vtt_bytes = generate_vtt_subtitles(file_path, track_index)
    return Response(
        vtt_bytes,
        mimetype="text/vtt",
        headers={"Content-Type": "text/vtt; charset=utf-8"},
    )


@api_bp.route("/media/<int:item_id>/download", methods=["GET"])
@api_bp.route(
    "/media/<int:item_id>/download/optimized/<any(x3,x4,kindle,kobo,eink,generic):preset>",
    methods=["GET"],
)
@require_token_scope("media:stream")
def download_media_file(item_id: int, preset: str | None = None) -> ResponseReturnValue:
    """Download a media file, optionally served from a precomputed e-ink optimized EPUB."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    preset_arg = preset or request.args.get("preset") or request.args.get("optimize")
    if preset_arg:
        from aarkib.plugins.optimizer import DEVICE_PRESETS

        clean_preset = preset_arg.lower().strip()
        if item.file_format == "epub" and clean_preset not in DEVICE_PRESETS:
            return api_error(
                f"Invalid optimizer preset '{preset_arg}'. "
                f"Supported: {', '.join(sorted(DEVICE_PRESETS.keys()))}",
                400,
            )
        preset_arg = clean_preset

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        return api_error(
            "Access denied: media file resides outside configured library roots", 403
        )
    if not file_path.is_file():
        return api_error("File missing from storage", 404)

    item_id_val = item.id
    item_title = item.title
    item_format = item.file_format
    item_file_hash = item.file_hash
    orig_path_str = item.original_file_path

    if preset_arg and item_format == "epub":
        optimized_dir = Path(
            current_app.config.get(
                "OPTIMIZED_DIR",
                Path(current_app.config.get("DATA_DIR", "data")) / "optimized",
            )
        )
        from aarkib.plugins.optimizer import get_or_create_optimized_epub

        db.session.close()
        try:
            opt_path = get_or_create_optimized_epub(
                item_id=item_id_val,
                file_path=orig_path_str,
                file_hash=item_file_hash,
                preset_key=preset_arg,
                optimized_dir=optimized_dir,
            )
            download_name = f"{item_title} ({preset_arg.upper()}).epub"
            return send_file(
                opt_path,
                as_attachment=True,
                download_name=download_name,
                mimetype="application/epub+zip",
            )
        except Exception as e:
            current_app.logger.error(
                "Failed optimizing on download for %s: %s", item_title, e
            )

    filename = f"{item_title}.{item_format}"
    guessed, _ = mimetypes.guess_type(str(file_path))
    mimetype = guessed or "application/octet-stream"
    db.session.close()
    return send_file(
        file_path, as_attachment=True, download_name=filename, mimetype=mimetype
    )


@api_bp.route("/optimizer/presets", methods=["GET"])
def get_optimizer_presets() -> ResponseReturnValue:
    """List supported e-ink optimization presets."""
    from aarkib.plugins.optimizer import DEVICE_PRESETS

    return jsonify(DEVICE_PRESETS)


@api_bp.route("/media/<int:item_id>/optimize", methods=["POST"])
@api_admin_required
def precompute_media_optimization(item_id: int) -> ResponseReturnValue:
    """Pre-generate optimized EPUB cache for a media item."""
    item = db.session.get(MediaItem, item_id)
    if not item or item.file_format != "epub":
        return api_error("Item is not an EPUB", 400)

    data = request.get_json(silent=True) or {}
    preset_arg = str(data.get("preset", "generic"))
    from aarkib.plugins.optimizer import DEVICE_PRESETS

    clean_preset = preset_arg.lower().strip()
    if clean_preset not in DEVICE_PRESETS:
        return api_error(
            f"Invalid optimizer preset '{preset_arg}'. "
            f"Supported: {', '.join(sorted(DEVICE_PRESETS.keys()))}",
            400,
        )
    preset_arg = clean_preset

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        return api_error(
            "Access denied: media file resides outside configured library roots", 403
        )
    if not file_path.is_file():
        return api_error("File missing from storage", 404)

    item_id_val = item.id
    item_hash = item.file_hash
    orig_path_str = item.original_file_path
    orig_size = item.file_size or Path(item.original_file_path).stat().st_size
    db.session.close()

    optimized_dir = Path(
        current_app.config.get(
            "OPTIMIZED_DIR",
            Path(current_app.config.get("DATA_DIR", "data")) / "optimized",
        )
    )
    from aarkib.plugins.optimizer import get_or_create_optimized_epub

    try:
        opt_path = get_or_create_optimized_epub(
            item_id=item_id_val,
            file_path=orig_path_str,
            file_hash=item_hash,
            preset_key=preset_arg,
            optimized_dir=optimized_dir,
        )
        opt_size = opt_path.stat().st_size
        reduction = (
            round((1.0 - (opt_size / orig_size)) * 100, 1) if orig_size > 0 else 0
        )

        return jsonify(
            {
                "status": "success",
                "id": item_id_val,
                "item_id": item_id_val,
                "preset": preset_arg,
                "original_size": orig_size,
                "optimized_size": opt_size,
                "reduction_percent": reduction,
                "download_url": f"/api/media/{item_id_val}/download/optimized/{preset_arg}",
            }
        )
    except Exception as e:
        current_app.logger.error("Optimization failed: %s", e)
        return api_error(f"Optimization failed: {e}", 500)


@api_bp.route("/media/<int:item_id>/pages", methods=["GET"])
def get_cbz_pages(item_id: int) -> ResponseReturnValue:
    """List page metadata (path, width, height) for a CBZ comic."""
    item = db.session.get(MediaItem, item_id)
    if not item or item.file_format not in ("cbz", "zip", "cbr"):
        return api_error("Item is not a CBZ comic", 400)

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        return api_error(
            "Access denied: media file resides outside configured library roots", 403
        )
    if not file_path.is_file():
        return api_error("File missing from storage", 404)

    item_id_val = item.id
    db.session.close()

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
            "url": f"/api/media/{item_id_val}/page/{idx + 1}",
            "filename": Path(name).name,
        }
        for idx, name in enumerate(image_names)
    ]
    return jsonify(
        {
            "id": item_id_val,
            "item_id": item_id_val,
            "total_pages": len(pages),
            "pages": pages,
        }
    )


@api_bp.route("/media/<int:item_id>/page/<int:page_num>", methods=["GET"])
def get_cbz_page_image(item_id: int, page_num: int) -> ResponseReturnValue:
    """Serve a single page image from a CBZ comic archive."""
    item = db.session.get(MediaItem, item_id)
    if not item or item.file_format not in ("cbz", "zip", "cbr"):
        abort(404)

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        abort(403)
    if not file_path.is_file():
        abort(404)

    db.session.close()

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
            "Error serving page %s for media item %s: %s", page_num, item_id, e
        )
        abort(500, description=f"Unable to read comic page: {e}")


@api_bp.route("/media/<int:item_id>/progress", methods=["GET", "POST"])
def media_progress(item_id: int) -> ResponseReturnValue:
    """Fetch or update reading/video progress for a media item."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    user_id = current_user.id if current_user.is_authenticated else None
    token = getattr(g, "device_token", None)

    if request.method == "POST":
        if token is not None and not token.has_scope("progress:write"):
            return (
                jsonify(
                    {"error": "Device token missing required scope: 'progress:write'"}
                ),
                HTTPStatus.FORBIDDEN,
            )
        data = request.get_json(silent=True) or {}
        record = update_progress_service(user_id, item, data)
        return jsonify(
            {
                "status": "ok",
                "percentage": record.percentage,
                "location": record.progress_location,
                "position_seconds": record.position_seconds,
                "duration": record.duration,
                "playback_speed": record.playback_speed,
                "playback_type": record.playback_type,
            }
        )

    # GET request
    if token is not None and not token.has_scope("media:read"):
        return (
            jsonify({"error": "Device token missing required scope: 'media:read'"}),
            HTTPStatus.FORBIDDEN,
        )
    return jsonify(get_progress_service(user_id, item))


@api_bp.route("/media/<int:item_id>/bookmarks", methods=["GET", "POST"])
def bookmarks(item_id: int) -> ResponseReturnValue:
    """List or create bookmarks for a media item."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    user_id = current_user.id if current_user.is_authenticated else None

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        location = str(data.get("location", ""))
        title = data.get("title")
        snippet = data.get("snippet")

        try:
            bm = add_bookmark_service(
                item.id, user_id, location=location, title=title, snippet=snippet
            )
        except ValueError as exc:
            return api_error(str(exc), 400)

        return (
            jsonify(
                {
                    "id": bm.id,
                    "location": bm.location,
                    "title": bm.title,
                    "snippet": bm.snippet,
                }
            ),
            201,
        )

    bms = list_bookmarks_service(item.id, user_id)
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
def delete_bookmark(bookmark_id: int) -> ResponseReturnValue:
    """Delete a bookmark, restricted to its owner (or any anonymous bookmark)."""
    user_id = current_user.id if current_user.is_authenticated else None
    try:
        delete_bookmark_service(bookmark_id, user_id)
    except KeyError:
        return api_error("Bookmark not found", 404)
    except PermissionError:
        return api_error("Forbidden", 403)
    return jsonify({"status": "deleted"})


@api_bp.route("/library/scan", methods=["POST"])
@api_bp.route("/libraries/scan", methods=["POST"])
@api_admin_required
def trigger_scan() -> ResponseReturnValue:
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


@api_bp.route("/media/<int:item_id>/enrich", methods=["POST"])
@api_admin_required
def enrich_media_item(item_id: int) -> ResponseReturnValue:
    """Fetch online metadata for a single media item (books, video, music)."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    data = request.get_json(silent=True) or {}
    overwrite = bool(data.get("overwrite", False))
    provider = str(
        data.get("provider", current_app.config.get("METADATA_PROVIDER", "all"))
    )

    covers_dir = Path(current_app.config["COVERS_DIR"])
    from aarkib.services.enricher import enrich_media_item as run_enrich

    result = run_enrich(item, covers_dir, overwrite=overwrite, provider=provider)
    return jsonify(result)


@api_bp.route("/media/<int:item_id>/metadata/search", methods=["GET"])
@api_admin_required
def search_metadata_candidates(item_id: int) -> ResponseReturnValue:
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
    media_type = item.media_type or "all"
    item_id_val = item.id

    # C3 fix: Detach DB session before performing external provider search
    db.session.close()

    from aarkib.services.metadata import metadata_registry

    candidates = metadata_registry.search(
        media_type=media_type,
        query=query,
        year=str(year)[:4] if year else None,
        provider_name=provider_name,
    )

    return jsonify(
        {
            "status": "success",
            "media_id": item_id_val,
            "media_type": media_type,
            "query": query,
            "count": len(candidates),
            "candidates": [c.to_dict() for c in candidates],
        }
    )


@api_bp.route("/media/<int:item_id>/metadata/apply", methods=["POST"])
@api_admin_required
def apply_metadata_candidate(item_id: int) -> ResponseReturnValue:
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
    from aarkib.services.enricher import enrich_media_item as run_enrich

    result = run_enrich(
        item,
        covers_dir=covers_dir,
        overwrite=True,
        candidate_external_id=str(external_id),
        candidate_provider=str(provider),
    )

    if result.get("status") == "not_found":
        return api_error("Failed to fetch details for candidate from provider", 404)

    # Re-fetch item since run_enrich closed the previous session and committed in a new one
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found after enrichment", 404)

    # If lock_fields specified, set them on the item after applying metadata
    if lock_fields is not None:
        if isinstance(lock_fields, list):
            item.set_locked_fields(lock_fields)
        elif isinstance(lock_fields, str):
            item.set_locked_fields(
                [f.strip() for f in lock_fields.split(",") if f.strip()]
            )
        safe_commit()

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
                "creators": [a.name for a in item.creators],
                "description": item.description,
                "cover_image_path": item.cover_image_path,
                "external_id": item.external_id,
            },
        }
    )


@api_bp.route("/media/<int:item_id>/metadata/locked-fields", methods=["GET", "PUT"])
@api_admin_required
def manage_locked_fields(item_id: int) -> ResponseReturnValue:
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
    safe_commit()

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
def enrich_library() -> ResponseReturnValue:
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
            progress_callback=kwargs.get("progress_callback"),
            cancel_event=kwargs.get("cancel_event"),
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


@api_bp.route("/media/<int:item_id>", methods=["PATCH"])
@api_bp.route("/media/<int:item_id>/edit", methods=["POST"])
@api_admin_required
def edit_media_metadata(item_id: int) -> ResponseReturnValue:
    """Manually edit a media item's title, creators, series, tags, and descriptive fields."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", 404)

    from aarkib.services.media_service import edit_media_metadata as apply_edits

    data = request.get_json(silent=True) or request.form
    apply_edits(item, data)
    safe_commit()

    from aarkib.services.search import sync_media_item_fts

    sync_media_item_fts(item.id)

    item_dict = {
        "id": item.id,
        "title": item.title,
        "collection": item.collection.name if item.collection else None,
        "collection_id": item.collection_id,
        "series_index": item.series_index,
        "creators": [a.name for a in item.creators],
        "creators_display": item.creators_display,
        "tags": [t.name for t in item.tags],
        "locked_fields": item.get_locked_fields(),
        "provenance": item.get_field_provenance(),
    }

    return jsonify(
        {
            "status": "success",
            "message": "Metadata updated successfully",
            "item": item_dict,
        }
    )


# ----------------------------------------------------------------------
# User Favorites API
# ----------------------------------------------------------------------


@api_bp.route("/media/<int:item_id>/favorite", methods=["POST"])
def toggle_favorite(item_id: int) -> ResponseReturnValue:
    """Toggle or update favorite status for a media item."""
    if not current_user.is_authenticated:
        return api_error("Authentication required to manage favorites", 401)

    data = request.get_json(silent=True) or {}
    explicit_state = data.get("favorite")
    if explicit_state is not None:
        explicit_state = bool(explicit_state)

    try:
        favorited = toggle_favorite_service(
            current_user.id, item_id, explicit_state=explicit_state
        )
    except KeyError:
        return api_error("Media item not found", 404)

    return jsonify(
        {"status": "success", "favorited": favorited, "media_item_id": item_id}
    )


@api_bp.route("/favorites", methods=["GET"])
def get_favorites() -> ResponseReturnValue:
    """Returns all favorited media items for the current user."""
    if not current_user.is_authenticated:
        return api_error("Authentication required to list favorites", 401)

    favs = list_favorites_service(current_user.id)
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
                    "cover_url": f"/api/media/{f.media_item.id}/cover",
                    "player_url": f.media_item.player_url,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
            )

    return jsonify({"favorites": items, "count": len(items)})


# ----------------------------------------------------------------------
# Playlists API
# ----------------------------------------------------------------------


@api_bp.route("/playlists", methods=["GET"])
def get_playlists() -> ResponseReturnValue:
    """List playlists belonging to the user or public playlists."""
    user_id = current_user.id if current_user.is_authenticated else None
    playlists = list_playlists_service(user_id)
    return jsonify({"playlists": [p.to_dict(include_items=False) for p in playlists]})


@api_bp.route("/playlists", methods=["POST"])
def create_playlist() -> ResponseReturnValue:
    """Create a new playlist."""
    user_id = current_user.id if current_user.is_authenticated else None
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()

    try:
        playlist = create_playlist_service(
            user_id=user_id,
            title=title,
            description=data.get("description"),
            media_type=data.get("media_type", "music"),
            is_public=bool(data.get("is_public", False)),
        )
    except ValueError as exc:
        return api_error(str(exc), 400)

    return (
        jsonify(
            {"status": "success", "playlist": playlist.to_dict(include_items=True)}
        ),
        201,
    )


@api_bp.route("/playlists/<int:playlist_id>", methods=["GET"])
def get_playlist_detail(playlist_id: int) -> ResponseReturnValue:
    """Get playlist details and its ordered items."""
    user_id = current_user.id if current_user.is_authenticated else None
    try:
        playlist = get_playlist_service(playlist_id, user_id)
    except KeyError:
        return api_error("Playlist not found", 404)
    except PermissionError as exc:
        return api_error(str(exc), 403)

    return jsonify({"playlist": playlist.to_dict(include_items=True)})


@api_bp.route("/playlists/<int:playlist_id>/items", methods=["POST"])
def add_playlist_item(playlist_id: int) -> ResponseReturnValue:
    """Add a media item to a playlist."""
    user_id = current_user.id if current_user.is_authenticated else None
    data = request.get_json(silent=True) or {}
    item_id = data.get("media_item_id") or data.get("item_id")
    if not item_id:
        return api_error("media_item_id is required", 400)

    try:
        playlist_item = add_playlist_item_service(
            playlist_id=playlist_id,
            user_id=user_id,
            media_item_id=item_id,
            position=data.get("position"),
        )
    except KeyError as exc:
        return api_error(str(exc), 404)
    except PermissionError as exc:
        return api_error(str(exc), 403)

    return jsonify({"status": "success", "item": playlist_item.to_dict()}), 201


@api_bp.route("/playlists/<int:playlist_id>/items/<int:item_id>", methods=["DELETE"])
def remove_playlist_item(playlist_id: int, item_id: int) -> ResponseReturnValue:
    """Remove a media item from a playlist."""
    user_id = current_user.id if current_user.is_authenticated else None
    try:
        remove_playlist_item_service(playlist_id, user_id, item_id)
    except KeyError as exc:
        return api_error(str(exc), 404)
    except PermissionError as exc:
        return api_error(str(exc), 403)

    return jsonify({"status": "success", "message": "Item removed from playlist"})


@api_bp.route("/playlists/<int:playlist_id>/reorder", methods=["PUT", "POST"])
def reorder_playlist_items(playlist_id: int) -> ResponseReturnValue:
    """Reorder items in a playlist."""
    user_id = current_user.id if current_user.is_authenticated else None
    data = request.get_json(silent=True) or {}
    item_ids = data.get("item_ids", [])
    if not isinstance(item_ids, list):
        return api_error("item_ids list is required", 400)

    try:
        reorder_playlist_items_service(playlist_id, user_id, item_ids)
    except KeyError as exc:
        return api_error(str(exc), 404)
    except PermissionError as exc:
        return api_error(str(exc), 403)

    return jsonify({"status": "success", "message": "Playlist reordered"})


@api_bp.route("/playlists/<int:playlist_id>", methods=["DELETE"])
def delete_playlist(playlist_id: int) -> ResponseReturnValue:
    """Delete a playlist."""
    user_id = current_user.id if current_user.is_authenticated else None
    try:
        delete_playlist_service(playlist_id, user_id)
    except KeyError as exc:
        return api_error(str(exc), 404)
    except PermissionError as exc:
        return api_error(str(exc), 403)

    return jsonify({"status": "success", "message": "Playlist deleted"})


@api_bp.route("/podcasts/opml/import", methods=["POST"])
@api_admin_required
def import_opml() -> ResponseReturnValue:
    """Import podcast show subscriptions from an uploaded OPML file or XML payload."""
    xml_content = None
    if "file" in request.files:
        upload = request.files["file"]
        xml_content = upload.read().decode("utf-8", errors="ignore")
    elif request.is_json:
        data = request.get_json(silent=True) or {}
        xml_content = data.get("opml") or data.get("content")
    else:
        raw_text = request.get_data(as_text=True)
        if raw_text and "<opml" in raw_text:
            xml_content = raw_text

    if not xml_content or not xml_content.strip():
        return api_error("No OPML file or XML content provided", 400)

    try:
        from aarkib.services.opml import import_opml_channels, parse_opml

        feeds = parse_opml(xml_content)
        if not feeds:
            return api_error("No valid podcast feed outlines found in OPML", 400)

        result = import_opml_channels(feeds)
        return jsonify({"status": "success", "data": result})
    except ValueError as exc:
        return api_error(str(exc), 400)
    except Exception as exc:
        current_app.logger.exception("Failed to import OPML: %s", exc)
        return api_error(f"OPML import error: {exc}", 500)


@api_bp.route("/settings", methods=["GET"])
@api_admin_required
def get_settings() -> ResponseReturnValue:
    """Retrieve all effective system settings and their metadata."""
    from aarkib.services.settings_service import get_effective_settings

    result = get_effective_settings(current_app)
    return jsonify({"status": "success", **result})


@api_bp.route("/settings", methods=["PATCH", "PUT"])
@api_admin_required
def update_system_settings() -> ResponseReturnValue:
    """Update system settings dynamically."""
    from aarkib.services.settings_service import update_settings

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    try:
        result = update_settings(current_app, data)
        return jsonify(
            {
                "status": "success",
                "message": "Settings updated successfully",
                **result,
            }
        )
    except ValueError as err:
        return jsonify({"error": str(err)}), 400
    except Exception as err:
        current_app.logger.error("Failed to update settings: %s", err, exc_info=True)
        return jsonify({"error": "Failed to update settings"}), 500


@api_bp.route("/settings/reset", methods=["POST"])
@api_admin_required
def reset_system_settings() -> ResponseReturnValue:
    """Reset system settings to environment / hardcoded defaults."""
    from aarkib.services.settings_service import reset_settings_to_defaults

    try:
        result = reset_settings_to_defaults(current_app)
        return jsonify(
            {
                "status": "success",
                "message": "Settings reset to environment defaults",
                **result,
            }
        )
    except Exception as err:
        current_app.logger.error("Failed to reset settings: %s", err, exc_info=True)
        return jsonify({"error": "Failed to reset settings"}), 500


# ---------------------------------------------------------------------------
# Backup & Disaster Recovery Endpoints
# ---------------------------------------------------------------------------


@api_bp.route("/backup", methods=["GET"])
@api_admin_required
def list_backups_endpoint() -> ResponseReturnValue:
    """List all available backup archives with manifest metadata."""
    from aarkib.services.backup import list_backups

    backups = list_backups(current_app)
    return jsonify({"status": "success", "backups": backups, "count": len(backups)})


@api_bp.route("/backup", methods=["POST"])
@api_admin_required
def create_backup_endpoint() -> ResponseReturnValue:
    """Create a new hot backup snapshot of the database and covers."""
    from aarkib.services.backup import create_backup

    include_covers = request.args.get("include_covers", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    try:
        archive_path = create_backup(current_app, include_covers=include_covers)
        return (
            jsonify(
                {
                    "status": "success",
                    "message": f"Backup created successfully: {archive_path.name}",
                    "filename": archive_path.name,
                }
            ),
            201,
        )
    except Exception as exc:
        current_app.logger.error("Failed to create backup: %s", exc, exc_info=True)
        return jsonify({"error": f"Failed to create backup: {exc}"}), 500


@api_bp.route("/backup/download/<filename>", methods=["GET"])
@api_admin_required
def download_backup_endpoint(filename: str) -> ResponseReturnValue:
    """Download a backup archive file."""
    from aarkib.services.backup import get_backup_dir

    backup_dir = get_backup_dir(current_app)
    file_path = (backup_dir / filename).resolve()
    if not is_safe_media_path(file_path) or not file_path.is_file():
        abort(404, description="Backup file not found")

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/zip",
    )


@api_bp.route("/backup/restore", methods=["POST"])
@api_admin_required
def restore_backup_endpoint() -> ResponseReturnValue:
    """Restore from an existing backup file in BACKUP_DIR or an uploaded archive."""
    from aarkib.services.backup import get_backup_dir, restore_backup

    # Check if a file was uploaded in request.files
    if "backup_file" in request.files:
        upload = request.files["backup_file"]
        if not upload or not upload.filename:
            return jsonify({"error": "No backup file selected"}), 400

        backup_dir = get_backup_dir(current_app)
        safe_filename = Path(upload.filename).name
        target_path = backup_dir / f"uploaded_{safe_filename}"
        upload.save(target_path)
    else:
        # Check JSON payload for existing filename
        payload = request.get_json(silent=True) or {}
        filename = payload.get("filename") or request.form.get("filename")
        if not filename:
            return jsonify({"error": "No backup filename provided"}), 400

        backup_dir = get_backup_dir(current_app)
        target_path = (backup_dir / Path(filename).name).resolve()

    if not is_safe_media_path(target_path) or not target_path.is_file():
        return jsonify({"error": "Backup file not found"}), 404

    success, msg = restore_backup(current_app, target_path)
    if not success:
        return jsonify({"status": "error", "error": msg}), 400

    return jsonify({"status": "success", "message": msg})


@api_bp.route("/backup/<filename>", methods=["DELETE"])
@api_admin_required
def delete_backup_endpoint(filename: str) -> ResponseReturnValue:
    """Delete a backup archive."""
    from aarkib.services.backup import delete_backup

    if delete_backup(current_app, filename):
        return jsonify({"status": "success", "message": f"Deleted {filename}"})
    return jsonify({"error": "Failed to delete backup file"}), 404


# ---------------------------------------------------------------------------
# API / Device Token Endpoints
# ---------------------------------------------------------------------------


@api_bp.route("/tokens", methods=["GET"])
@login_required
def list_device_tokens() -> ResponseReturnValue:
    """List active API and device tokens for the authenticated user."""
    from aarkib.models.token import DeviceToken

    tokens = db.session.scalars(
        select(DeviceToken)
        .where(DeviceToken.user_id == current_user.id)
        .order_by(DeviceToken.created_at.desc())
    ).all()
    return jsonify({"tokens": [t.to_dict() for t in tokens], "count": len(tokens)})


@api_bp.route("/tokens", methods=["POST"])
@login_required
def create_device_token() -> ResponseReturnValue:
    """Generate a new API or device token for the current user."""
    from aarkib.models.token import DeviceToken

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Token name is required"}), 400

    scopes = payload.get("scopes") or ["*"]
    expires_in_days = payload.get("expires_in_days")
    if expires_in_days is not None:
        try:
            expires_in_days = int(expires_in_days)
        except ValueError, TypeError:
            expires_in_days = None

    token_obj, raw_token = DeviceToken.create_token(
        user_id=current_user.id,
        name=name,
        scopes=scopes,
        expires_in_days=expires_in_days,
    )
    db.session.add(token_obj)
    db.session.commit()

    return (
        jsonify(
            {
                "status": "success",
                "message": (
                    "Device token created successfully. Store this token "
                    "securely as it will not be shown again."
                ),
                "token": raw_token,
                "token_id": token_obj.id,
                "name": token_obj.name,
                "token_prefix": token_obj.token_prefix,
            }
        ),
        201,
    )


@api_bp.route("/tokens/<int:token_id>", methods=["DELETE"])
@login_required
def revoke_device_token(token_id: int) -> ResponseReturnValue:
    """Revoke and delete a device token."""
    from aarkib.models.token import DeviceToken

    token_obj = db.session.get(DeviceToken, token_id)
    if not token_obj:
        return jsonify({"error": "Token not found"}), 404

    # Authorization: Must be owner or admin
    if token_obj.user_id != current_user.id and not current_user.is_admin:
        return jsonify({"error": "Unauthorized"}), 403

    name = token_obj.name
    db.session.delete(token_obj)
    db.session.commit()
    return jsonify({"status": "success", "message": f"Revoked token '{name}'"})
