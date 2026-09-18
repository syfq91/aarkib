"""Clean, versioned REST API v1 blueprint for Aarkib."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import wraps
from http import HTTPStatus
from typing import Any

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    request,
)
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_user
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from aarkib.extensions import db
from aarkib.models import (
    Library,
    MediaItem,
    Profile,
    ProfileLibraryAccess,
    User,
)
from aarkib.models.token import DeviceToken
from aarkib.plugins import plugin_registry
from aarkib.services.authorization import authorization
from aarkib.services.capability_service import capability_service
from aarkib.services.job_manager import job_manager
from aarkib.services.library_service import validate_library_availability
from aarkib.services.media_service import (
    count_media_in_library,
    resolve_library,
)
from aarkib.services.playback_service import playback_service
from aarkib.services.progress_service import (
    get_progress as get_progress_service,
)
from aarkib.services.scanner import scan_library
from aarkib.services.security import auth_rate_limiter, get_client_ip

logger = logging.getLogger(__name__)

api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def api_error(
    message: str, status: int | HTTPStatus = HTTPStatus.BAD_REQUEST
) -> ResponseReturnValue:
    """Return a standardized JSON error envelope."""
    status_int = int(status)
    return jsonify(
        {"error": message, "status": "error", "status_code": status_int}
    ), status_int


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
                            "error": f"Device token missing required scope: '{required_scope}'",
                            "status": "error",
                            "status_code": HTTPStatus.FORBIDDEN,
                        }
                    ),
                    HTTPStatus.FORBIDDEN,
                )
            return view(*args, **kwargs)

        return wrapped

    return decorator


def api_admin_required(view):
    """Require administrator privileges for privileged v1 endpoints."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        token = getattr(g, "device_token", None)
        if token is not None and not token.has_scope("admin"):
            return (
                jsonify(
                    {
                        "error": "Device token missing required 'admin' scope",
                        "status": "error",
                        "status_code": HTTPStatus.FORBIDDEN,
                    }
                ),
                HTTPStatus.FORBIDDEN,
            )
        if not current_user.is_authenticated or not authorization.can(
            current_user, "admin"
        ):
            return (
                jsonify(
                    {
                        "error": "Administrator privileges required",
                        "status": "error",
                        "status_code": HTTPStatus.FORBIDDEN,
                    }
                ),
                HTTPStatus.FORBIDDEN,
            )
        return view(*args, **kwargs)

    return wrapped


@api_v1_bp.before_request
def enforce_api_v1_auth():
    """Authenticate API v1 requests (Bearer token, HTTP Basic auth, or session)."""
    exempt_endpoints = {"api_v1.health"}
    if request.endpoint in exempt_endpoints:
        return None

    # 1. Bearer Token (DeviceToken)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        raw_token = auth_header[7:].strip()
        token_hash = DeviceToken.hash_token(raw_token)
        token_record = db.session.scalar(
            select(DeviceToken).where(DeviceToken.token_hash == token_hash)
        )
        if token_record:
            if token_record.expires_at:
                now_utc = datetime.now(UTC)
                exp_utc = (
                    token_record.expires_at.replace(tzinfo=UTC)
                    if token_record.expires_at.tzinfo is None
                    else token_record.expires_at
                )
                if exp_utc < now_utc:
                    return (
                        jsonify(
                            {
                                "error": "Device token has expired",
                                "status": "error",
                                "status_code": HTTPStatus.UNAUTHORIZED,
                            }
                        ),
                        HTTPStatus.UNAUTHORIZED,
                    )
            token_record.last_used_at = datetime.now(UTC)
            db.session.commit()
            g.device_token = token_record
            login_user(token_record.user)
        else:
            return (
                jsonify(
                    {
                        "error": "Invalid API token",
                        "status": "error",
                        "status_code": HTTPStatus.UNAUTHORIZED,
                    }
                ),
                HTTPStatus.UNAUTHORIZED,
            )

    # 2. HTTP Basic Auth
    auth = request.authorization
    if auth and auth.username:
        client_ip = get_client_ip()
        limited, retry_after = auth_rate_limiter.is_rate_limited(client_ip)
        if limited:
            return (
                jsonify(
                    {
                        "error": f"Too many failed login attempts. Retry after {retry_after}s.",
                        "status": "error",
                        "status_code": HTTPStatus.TOO_MANY_REQUESTS,
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
                jsonify(
                    {
                        "error": "Invalid credentials",
                        "status": "error",
                        "status_code": HTTPStatus.UNAUTHORIZED,
                    }
                ),
                HTTPStatus.UNAUTHORIZED,
            )

    # 3. Session / Authenticated User check
    if not current_user.is_authenticated:
        return (
            jsonify(
                {
                    "error": "Authentication required",
                    "status": "error",
                    "status_code": HTTPStatus.UNAUTHORIZED,
                }
            ),
            HTTPStatus.UNAUTHORIZED,
        )

    return None


@api_v1_bp.route("/health", methods=["GET"])
def health() -> ResponseReturnValue:
    """API v1 Health check endpoint."""
    return jsonify({"status": "healthy", "version": "v1", "app": "aarkib"})


# ---------------------------------------------------------------------------
# Canonical Media & Deterministic Playback Planning
# ---------------------------------------------------------------------------


def _serialize_media_item(
    item: MediaItem, user: User, active_profile: Profile | None
) -> dict[str, Any]:
    """Serialize canonical MediaItem dictionary."""
    user_id = user.id if user else None
    profile_id = getattr(active_profile, "id", None)
    prog_data = get_progress_service(user_id, item, profile_id=profile_id)
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

    return {
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
        "library_id": item.library_id,
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
        "detailed_provenance": item.get_all_detailed_provenance(),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


@api_v1_bp.route("/media/<int:item_id>", methods=["GET"])
@require_token_scope("media:read")
def get_media_item_v1(item_id: int) -> ResponseReturnValue:
    """Retrieve canonical media item metadata along with client playback descriptors."""
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
        return api_error("Media item not found", HTTPStatus.NOT_FOUND)

    active_prof = authorization.get_active_profile(current_user)
    subject = active_prof or current_user
    if subject and not authorization.can(subject, "library.read", item.library_id):
        return api_error(
            "Access denied by library access control", HTTPStatus.FORBIDDEN
        )

    media_dict = _serialize_media_item(item, current_user, active_prof)

    # Attach client capability detection and playback plan
    capabilities = capability_service.detect(request)
    plan = playback_service.plan(item, capabilities=capabilities)

    plugin = plugin_registry.get_plugin_for_media_type(item.media_type or "")
    if not plugin:
        plugin = plugin_registry.get_plugin_for_extension(f".{item.file_format}")

    if plugin:
        descriptor = plugin.get_playback_info(item, user_id=current_user.id)
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

    descriptor["plan"] = plan.to_dict()
    descriptor["playback_plan"] = plan.to_dict()
    media_dict["playback"] = descriptor

    return jsonify(
        {
            "status": "success",
            "data": media_dict,
            **media_dict,
        }
    )


@api_v1_bp.route("/media/<int:item_id>/playback-plan", methods=["GET"])
@require_token_scope("media:read")
def get_playback_plan_v1(item_id: int) -> ResponseReturnValue:
    """Evaluate client capabilities and return deterministic PlaybackPlan."""
    item = db.session.get(MediaItem, item_id)
    if not item:
        return api_error("Media item not found", HTTPStatus.NOT_FOUND)

    active_prof = authorization.get_active_profile(current_user)
    subject = active_prof or current_user
    if subject and not authorization.can(subject, "media.stream", item):
        return api_error(
            "Access denied by library access control", HTTPStatus.FORBIDDEN
        )

    capabilities = capability_service.detect(request)
    plan = playback_service.plan(item, capabilities=capabilities)
    plan_dict = plan.to_dict()

    return jsonify(
        {
            "status": "success",
            "data": plan_dict,
            "plan": plan_dict,
            "playback_plan": plan_dict,
        }
    )


@api_v1_bp.route("/media", methods=["GET"])
@require_token_scope("media:read")
def list_media_v1() -> ResponseReturnValue:
    """List catalog items with filtering, pagination, and active profile ACL filtering."""
    q = request.args.get("q", "").strip()
    media_type = request.args.get("media_type", "").strip()
    library_ident = (
        request.args.get("library_id") or request.args.get("library") or ""
    ).strip()

    limit = min(request.args.get("limit", 24, type=int), 100)
    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * limit

    active_prof = authorization.get_active_profile(current_user)

    stmt = (
        select(MediaItem)
        .options(
            selectinload(MediaItem.creators),
            selectinload(MediaItem.collection),
            selectinload(MediaItem.tags),
        )
        .order_by(MediaItem.title.asc())
    )

    # ACL filtering
    if active_prof:
        allowed_lib_ids = {
            a.library_id for a in active_prof.library_access if a.can_read
        }
        # If specific ACLs are set, restrict to those libraries
        if allowed_lib_ids:
            stmt = stmt.where(MediaItem.library_id.in_(allowed_lib_ids))
        elif active_prof.library_access:
            # All configured ACLs have can_read=False
            return jsonify(
                {
                    "status": "success",
                    "data": [],
                    "items": [],
                    "page": page,
                    "limit": limit,
                    "total": 0,
                }
            )

    if media_type:
        stmt = stmt.where(MediaItem.media_type == media_type)

    if library_ident:
        lib = resolve_library(library_ident)
        if lib:
            stmt = stmt.where(MediaItem.library_id == lib.id)
        else:
            return jsonify(
                {
                    "status": "success",
                    "data": [],
                    "items": [],
                    "page": page,
                    "limit": limit,
                    "total": 0,
                }
            )

    if q:
        stmt = stmt.where(MediaItem.title.ilike(f"%{q}%"))

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.session.scalar(count_stmt) or 0

    items = db.session.scalars(stmt.offset(offset).limit(limit)).all()
    serialized = [_serialize_media_item(i, current_user, active_prof) for i in items]

    return jsonify(
        {
            "status": "success",
            "data": serialized,
            "items": serialized,
            "page": page,
            "limit": limit,
            "total": total,
        }
    )


# ---------------------------------------------------------------------------
# Profile Management & Granular ACLs
# ---------------------------------------------------------------------------


@api_v1_bp.route("/profiles", methods=["GET"])
def list_profiles_v1() -> ResponseReturnValue:
    """List all profiles associated with the authenticated user."""
    profiles = db.session.scalars(
        select(Profile)
        .options(selectinload(Profile.library_access))
        .where(Profile.user_id == current_user.id)
        .order_by(Profile.name.asc())
    ).all()

    p_dicts = [p.to_dict() for p in profiles]
    return jsonify(
        {
            "status": "success",
            "data": p_dicts,
            "profiles": p_dicts,
        }
    )


@api_v1_bp.route("/profiles", methods=["POST"])
def create_profile_v1() -> ResponseReturnValue:
    """Create a new profile with name, child status, avatar, and optional library ACLs."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return api_error("Invalid JSON payload", HTTPStatus.BAD_REQUEST)

    name = str(data.get("name", "")).strip()
    if not name:
        return api_error("Profile name is required", HTTPStatus.BAD_REQUEST)

    is_child = bool(data.get("is_child", False))
    avatar_url = data.get("avatar_url") or None
    if avatar_url:
        avatar_url = str(avatar_url).strip()

    profile = Profile(
        user_id=current_user.id,
        name=name,
        is_child=is_child,
        avatar_url=avatar_url,
    )
    db.session.add(profile)
    db.session.flush()

    # Apply initial library ACL rules if provided
    rules = data.get("library_access")
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            lib_id = rule.get("library_id")
            if lib_id and db.session.get(Library, lib_id):
                acl = ProfileLibraryAccess(
                    profile_id=profile.id,
                    library_id=lib_id,
                    can_read=rule.get("can_read", True),
                    can_download=rule.get("can_download", not profile.is_child),
                )
                db.session.add(acl)

    db.session.commit()
    p_dict = profile.to_dict()
    return (
        jsonify(
            {
                "status": "success",
                "data": p_dict,
                "profile": p_dict,
            }
        ),
        HTTPStatus.CREATED,
    )


@api_v1_bp.route("/profiles/<int:profile_id>", methods=["GET"])
def get_profile_v1(profile_id: int) -> ResponseReturnValue:
    """Retrieve details and ACL rules for a specific profile."""
    profile = db.session.get(Profile, profile_id)
    if not profile:
        return api_error("Profile not found", HTTPStatus.NOT_FOUND)

    if profile.user_id != current_user.id and not current_user.is_admin:
        return api_error(
            "Forbidden: cannot access another user's profile", HTTPStatus.FORBIDDEN
        )

    p_dict = profile.to_dict()
    return jsonify(
        {
            "status": "success",
            "data": p_dict,
            "profile": p_dict,
        }
    )


@api_v1_bp.route("/profiles/<int:profile_id>", methods=["PATCH", "PUT"])
def update_profile_v1(profile_id: int) -> ResponseReturnValue:
    """Update attributes and ACL permissions for a specific profile."""
    profile = db.session.get(Profile, profile_id)
    if not profile:
        return api_error("Profile not found", HTTPStatus.NOT_FOUND)

    if profile.user_id != current_user.id and not current_user.is_admin:
        return api_error(
            "Forbidden: cannot modify another user's profile", HTTPStatus.FORBIDDEN
        )

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return api_error("Invalid JSON payload", HTTPStatus.BAD_REQUEST)

    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            return api_error("Profile name cannot be empty", HTTPStatus.BAD_REQUEST)
        profile.name = name

    if "is_child" in data:
        profile.is_child = bool(data["is_child"])

    if "avatar_url" in data:
        profile.avatar_url = (
            str(data["avatar_url"]).strip() if data["avatar_url"] else None
        )

    if "library_access" in data and isinstance(data["library_access"], list):
        # Delete existing and replace with newly specified ACL rules
        db.session.execute(
            delete(ProfileLibraryAccess).where(
                ProfileLibraryAccess.profile_id == profile.id
            )
        )
        for rule in data["library_access"]:
            if not isinstance(rule, dict):
                continue
            lib_id = rule.get("library_id")
            if lib_id and db.session.get(Library, lib_id):
                db.session.add(
                    ProfileLibraryAccess(
                        profile_id=profile.id,
                        library_id=lib_id,
                        can_read=rule.get("can_read", True),
                        can_download=rule.get("can_download", not profile.is_child),
                    )
                )

    db.session.commit()
    p_dict = profile.to_dict()
    return jsonify(
        {
            "status": "success",
            "data": p_dict,
            "profile": p_dict,
        }
    )


@api_v1_bp.route("/profiles/<int:profile_id>", methods=["DELETE"])
def delete_profile_v1(profile_id: int) -> ResponseReturnValue:
    """Delete a profile (protecting the default profile and sole account profiles)."""
    profile = db.session.get(Profile, profile_id)
    if not profile:
        return api_error("Profile not found", HTTPStatus.NOT_FOUND)

    if profile.user_id != current_user.id and not current_user.is_admin:
        return api_error(
            "Forbidden: cannot delete another user's profile", HTTPStatus.FORBIDDEN
        )

    if profile.name.lower() == "default":
        return api_error("Cannot delete the default profile", HTTPStatus.BAD_REQUEST)

    user_profiles = db.session.scalars(
        select(Profile).where(Profile.user_id == profile.user_id)
    ).all()
    if len(user_profiles) <= 1:
        return api_error(
            "Cannot delete the only profile on an account", HTTPStatus.BAD_REQUEST
        )

    db.session.execute(
        delete(ProfileLibraryAccess).where(
            ProfileLibraryAccess.profile_id == profile.id
        )
    )
    db.session.delete(profile)
    db.session.commit()

    return jsonify({"status": "success", "message": "Profile deleted successfully"})


# ---------------------------------------------------------------------------
# Background Job Inspection & Cancellation
# ---------------------------------------------------------------------------


@api_v1_bp.route("/jobs", methods=["GET"])
def list_jobs_v1() -> ResponseReturnValue:
    """List recent background tasks and their execution states."""
    limit = min(request.args.get("limit", 20, type=int), 100)
    status = request.args.get("status", "").strip() or None
    jobs = job_manager.list_jobs(limit=limit, status=status)
    jobs_dict = [j.to_dict() for j in jobs]
    return jsonify(
        {
            "status": "success",
            "data": jobs_dict,
            "jobs": jobs_dict,
            "total": len(jobs_dict),
        }
    )


@api_v1_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job_v1(job_id: str) -> ResponseReturnValue:
    """Retrieve details, progress, and results for a specific background job."""
    job = job_manager.get_job(job_id)
    if not job:
        return api_error("Job not found", HTTPStatus.NOT_FOUND)
    job_dict = job.to_dict()
    return jsonify(
        {
            "status": "success",
            "data": job_dict,
            "job": job_dict,
        }
    )


@api_v1_bp.route("/jobs/<job_id>/cancel", methods=["POST"])
@api_admin_required
def cancel_job_v1(job_id: str) -> ResponseReturnValue:
    """Cancel a queued or running background job."""
    res = job_manager.cancel_job(job_id, app=current_app._get_current_object())
    if not res:
        return api_error("Job not found", HTTPStatus.NOT_FOUND)
    if not res.get("cancelled"):
        return api_error(
            res.get("message", "Job could not be cancelled"), HTTPStatus.BAD_REQUEST
        )
    return jsonify(
        {
            "status": "success",
            "data": res,
            "job": res,
        }
    )


@api_v1_bp.route("/jobs/<job_id>/retry", methods=["POST"])
@api_admin_required
def retry_job_v1(job_id: str) -> ResponseReturnValue:
    """Retry a failed or cancelled background job."""
    app_obj = current_app._get_current_object()
    job = job_manager.retry_job(job_id, app=app_obj)
    if not job:
        return api_error("Job not found or cannot be retried", HTTPStatus.BAD_REQUEST)
    job_dict = job.to_dict()
    return (
        jsonify(
            {
                "status": "accepted",
                "data": job_dict,
                "job": job_dict,
            }
        ),
        HTTPStatus.ACCEPTED,
    )


# ---------------------------------------------------------------------------
# Mount-Safe Library Reconciliation & Details
# ---------------------------------------------------------------------------


@api_v1_bp.route("/libraries/<identifier>/reconcile", methods=["POST"])
@api_admin_required
def reconcile_library_v1(identifier: str) -> ResponseReturnValue:
    """Trigger mount-safe library reconciliation with storage availability validation."""
    library = resolve_library(identifier)
    if not library:
        return api_error("Library not found", HTTPStatus.NOT_FOUND)

    force_prune = False
    sync_mode = request.args.get("sync", "").lower() in ("true", "1", "yes")
    if request.is_json and request.json:
        force_prune = bool(request.json.get("force_prune", False))
        if "sync" in request.json:
            sync_mode = bool(request.json["sync"])

    # Mount-safe availability guard: abort if path does not exist, is unreadable,
    # or empty unmounted mount point is suspected
    if not validate_library_availability(library, force_prune=force_prune):
        return (
            jsonify(
                {
                    "status": "error",
                    "error": (
                        f"Library '{library.name}' failed availability validation: path does not exist, "
                        "is unreadable, or unmounted mount point suspected. Reconciliation aborted to prevent data loss."
                    ),
                    "status_code": HTTPStatus.CONFLICT,
                }
            ),
            HTTPStatus.CONFLICT,
        )

    if sync_mode:
        res = scan_library(
            current_app,
            library_id=str(library.id),
            force_prune=force_prune,
        )
        return jsonify(
            {
                "status": "success",
                "data": res,
                "reconciliation": res,
            }
        )

    job = job_manager.submit_job(
        "library_reconcile",
        scan_library,
        current_app._get_current_object(),
        library_id=str(library.id),
        force_prune=force_prune,
    )
    return (
        jsonify(
            {
                "status": "accepted",
                "data": {
                    "job_id": job.id,
                    "job_url": f"/api/v1/jobs/{job.id}",
                    "library_id": library.id,
                },
                "job_id": job.id,
                "job_url": f"/api/v1/jobs/{job.id}",
            }
        ),
        HTTPStatus.ACCEPTED,
    )


@api_v1_bp.route("/libraries", methods=["GET"])
def list_libraries_v1() -> ResponseReturnValue:
    """List all libraries accessible to the active profile."""
    active_prof = authorization.get_active_profile(current_user)
    libraries = db.session.scalars(select(Library).order_by(Library.name.asc())).all()

    accessible = []
    for lib in libraries:
        if authorization.can(active_prof or current_user, "library.read", lib.id):
            accessible.append(
                {
                    "id": lib.id,
                    "name": lib.name,
                    "slug": lib.slug,
                    "path": lib.path,
                    "media_type": lib.media_type,
                    "media_count": count_media_in_library(lib),
                }
            )

    return jsonify(
        {
            "status": "success",
            "data": accessible,
            "libraries": accessible,
        }
    )


@api_v1_bp.route("/libraries/<identifier>", methods=["GET"])
def get_library_v1(identifier: str) -> ResponseReturnValue:
    """Retrieve details for a single library if accessible."""
    library = resolve_library(identifier)
    if not library:
        return api_error("Library not found", HTTPStatus.NOT_FOUND)

    active_prof = authorization.get_active_profile(current_user)
    if not authorization.can(active_prof or current_user, "library.read", library.id):
        return api_error(
            "Access denied by library access control", HTTPStatus.FORBIDDEN
        )

    lib_dict = {
        "id": library.id,
        "name": library.name,
        "slug": library.slug,
        "path": library.path,
        "media_type": library.media_type,
        "media_count": count_media_in_library(library),
    }

    return jsonify(
        {
            "status": "success",
            "data": lib_dict,
            "library": lib_dict,
        }
    )
