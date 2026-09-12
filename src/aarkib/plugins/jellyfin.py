"""Jellyfin REST API protocol plugin for mobile, TV, and desktop media streaming."""

from __future__ import annotations

import hashlib
import hmac
import logging
import mimetypes
import re
import time
import uuid
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    request,
    send_file,
)
from flask_login import current_user
from sqlalchemy import or_, select

from aarkib.extensions import db
from aarkib.models import Author, Collection, Library, MediaItem, User, UserProgress
from aarkib.models.playlist import UserFavorite
from aarkib.plugins.base import ProtocolPlugin
from aarkib.services.media_service import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)

jellyfin_bp = Blueprint("jellyfin", __name__)

JELLYFIN_SERVER_VERSION = "10.9.11"
SERVER_NAME = "Aarkib"


def get_server_id() -> str:
    """Returns a deterministic server UUID based on the server SECRET_KEY."""
    try:
        secret = current_app.config.get("SECRET_KEY", "aarkib-default-secret")
    except Exception:
        secret = "aarkib-default-secret"
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"aarkib-server-{secret}").hex


@jellyfin_bp.before_request
def _guard_jellyfin_enabled():
    """Guards Jellyfin routes when Jellyfin protocol plugin is disabled."""
    from aarkib.plugins.base import plugin_registry

    plugin = plugin_registry.get_plugin("jellyfin")
    if (plugin and not plugin.enabled) or not current_app.config.get(
        "ENABLE_JELLYFIN", True
    ):
        return jsonify({"message": "Jellyfin API is disabled on this server."}), 503


# ==============================================================================
# ID Formatters & Parsers (Support 32-char hex, hyphenated GUID, or int)
# ==============================================================================


def to_jellyfin_id(db_id: int | str) -> str:
    """Encodes an integer DB id into a standard 32-character hex Jellyfin ID."""
    if isinstance(db_id, int):
        return f"{db_id:032x}"
    try:
        val = int(db_id)
        return f"{val:032x}"
    except ValueError:
        return str(db_id)


def from_jellyfin_id(jellyfin_id: str | None) -> int | None:
    """Decodes a Jellyfin ID (hex string, GUID, or integer) back to integer DB id."""
    if not jellyfin_id:
        return None
    cleaned = jellyfin_id.replace("-", "").strip()
    if not cleaned:
        return None

    # If it's a 32-character GUID hex string or contains hex letters a-f, parse as hex
    if len(cleaned) == 32 or any(c in "abcdefABCDEF" for c in cleaned):
        try:
            return int(cleaned, 16)
        except ValueError:
            pass

    # Otherwise parse as decimal
    try:
        return int(cleaned, 10)
    except ValueError:
        try:
            return int(cleaned, 16)
        except ValueError:
            return None


# ==============================================================================
# Authentication & Token Helpers
# ==============================================================================


def generate_jellyfin_token(user_id: int) -> str:
    """Generates an HMAC-SHA256 signed stateless token for a user."""
    ts = int(time.time())
    payload = f"{user_id}:{ts}"
    try:
        secret = current_app.config.get("SECRET_KEY", "aarkib-default-secret")
    except Exception:
        secret = "aarkib-default-secret"
    sig = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return f"{user_id}.{ts}.{sig}"


def verify_jellyfin_token(token: str) -> User | None:
    """Verifies a signed token and returns the corresponding User."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    user_id_str, ts_str, sig = parts
    try:
        user_id = int(user_id_str)
        ts = int(ts_str)
    except ValueError:
        return None

    # Token valid for 90 days
    if time.time() - ts > 90 * 86400:
        return None

    payload = f"{user_id}:{ts}"
    try:
        secret = current_app.config.get("SECRET_KEY", "aarkib-default-secret")
    except Exception:
        secret = "aarkib-default-secret"
    expected_sig = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return None

    return db.session.get(User, user_id)


def extract_jellyfin_token() -> str | None:
    """Extracts authentication token from headers or query parameters."""
    token = (
        request.args.get("api_key")
        or request.args.get("token")
        or request.values.get("api_key")
        or request.values.get("token")
        or request.headers.get("X-Emby-Token")
        or request.headers.get("X-MediaBrowser-Token")
    )
    if token:
        return token.strip().strip("'\"")

    auth_hdr = (
        request.headers.get("X-Emby-Authorization")
        or request.headers.get("Authorization")
        or ""
    )
    if "Token=" in auth_hdr:
        m = re.search(r'Token=["\']?([^"\',]+)["\']?', auth_hdr)
        if m:
            return m.group(1).strip()
    return None


def get_authenticated_user() -> User | None:
    """Authenticates Jellyfin request via token, session, or Basic Auth."""
    token = extract_jellyfin_token()
    if token:
        user = verify_jellyfin_token(token)
        if user:
            return user

    if current_user.is_authenticated:
        return current_user

    auth = request.authorization
    if auth and auth.username:
        user = db.session.scalar(select(User).where(User.username == auth.username))
        if user and user.check_password(auth.password):
            return user

    return None


def jellyfin_auth(f):
    """Decorator ensuring valid Jellyfin authentication."""

    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_authenticated_user()
        if not user:
            return jsonify({"message": "Unauthorized"}), 401
        return f(*args, user=user, **kwargs)

    return decorated


def parse_client_info() -> dict[str, str]:
    """Parses client metadata from Jellyfin authorization headers."""
    hdr = (
        request.headers.get("X-Emby-Authorization")
        or request.headers.get("Authorization")
        or ""
    )
    info = {
        "Client": "Jellyfin Client",
        "Device": "Generic Device",
        "DeviceId": "generic-device",
        "Version": "1.0.0",
    }
    for key in ["Client", "Device", "DeviceId", "Version"]:
        m = re.search(rf'{key}=["\']?([^"\',]+)["\']?', hdr, re.IGNORECASE)
        if m:
            info[key] = m.group(1).strip()
    return info


# ==============================================================================
# DTO Serializers
# ==============================================================================


def _format_user(user: User) -> dict[str, Any]:
    """Formats an Aarkib User into a Jellyfin UserDto."""
    server_id = get_server_id()
    return {
        "Name": user.username,
        "ServerId": server_id,
        "ServerName": SERVER_NAME,
        "Id": to_jellyfin_id(user.id),
        "HasPassword": user.has_password,
        "HasConfiguredPassword": user.has_password,
        "HasConfiguredEasyPassword": False,
        "EnableAutoLogin": True,
        "LastLoginDate": (user.created_at.isoformat() if user.created_at else None),
        "LastActivityDate": None,
        "Configuration": {
            "PlayDefaultAudioTrack": True,
            "SubtitleLanguagePreference": "eng",
            "DisplayMissingEpisodes": False,
        },
        "Policy": {
            "IsAdministrator": user.is_admin,
            "IsHidden": False,
            "IsEnabled": True,
            "EnableContentDownloading": True,
            "EnableMediaPlayback": True,
            "EnableAudioPlaybackTranscoding": True,
            "EnableVideoPlaybackTranscoding": True,
            "EnablePlaybackRemuxing": True,
        },
    }


def _format_view(lib: Library) -> dict[str, Any]:
    """Formats an Aarkib Library into a Jellyfin CollectionFolder view."""
    server_id = get_server_id()
    col_type = "mixed"
    if lib.media_type in ("video", "movies", "movie"):
        col_type = "movies"
    elif lib.media_type in ("tv", "series", "shows"):
        col_type = "tvshows"
    elif lib.media_type in ("music", "audio"):
        col_type = "music"
    elif lib.media_type in ("book", "comic", "books"):
        col_type = "books"

    return {
        "Name": lib.name,
        "ServerId": server_id,
        "Id": to_jellyfin_id(lib.id),
        "Type": "CollectionFolder",
        "CollectionType": col_type,
        "IsFolder": True,
        "Path": lib.path,
        "UserData": {
            "PlaybackPositionTicks": 0,
            "PlayCount": 0,
            "Played": False,
            "IsFavorite": False,
        },
        "ImageTags": {},
    }


def _format_media_streams(item: MediaItem) -> list[dict[str, Any]]:
    """Formats media streams for an item."""
    streams = []
    if item.is_video:
        streams.append(
            {
                "Codec": item.codec or "h264",
                "Type": "Video",
                "Width": item.resolution_width or 1920,
                "Height": item.resolution_height or 1080,
                "Index": 0,
                "IsDefault": True,
                "BitRate": (item.bitrate * 1000) if item.bitrate else None,
            }
        )
        streams.append(
            {
                "Codec": "aac",
                "Type": "Audio",
                "Index": 1,
                "IsDefault": True,
                "Channels": 2,
            }
        )
    elif item.is_audio:
        streams.append(
            {
                "Codec": item.file_format or "mp3",
                "Type": "Audio",
                "Index": 0,
                "IsDefault": True,
                "BitRate": (item.bitrate * 1000) if item.bitrate else 320000,
                "Channels": 2,
            }
        )
    return streams


def _format_item(item: MediaItem, user_id: int | None = None) -> dict[str, Any]:
    """Formats an Aarkib MediaItem into a standard Jellyfin BaseItemDto."""
    server_id = get_server_id()
    run_time_ticks = int((item.duration or 0) * 10_000_000)
    year = None
    if item.release_year:
        try:
            year = int(item.release_year[:4])
        except ValueError, TypeError:
            pass
    elif item.publication_date:
        try:
            year = int(item.publication_date[:4])
        except ValueError, TypeError:
            pass

    # Type resolution
    if item.is_video:
        media_type = "Video"
        if item.season is not None and item.episode is not None:
            item_type = "Episode"
        else:
            item_type = "Movie"
    elif item.is_audio:
        media_type = "Audio"
        item_type = "Audio"
    elif item.is_book or item.is_comic:
        media_type = "Book"
        item_type = "Book"
    else:
        media_type = "Video"
        item_type = "Movie"

    # Playback & UserData
    user_data = {
        "PlaybackPositionTicks": 0,
        "PlayCount": 0,
        "Played": False,
        "PlayedPercentage": 0.0,
        "IsFavorite": False,
    }
    if user_id:
        prog = next((p for p in item.progress_records if p.user_id == user_id), None)
        if prog:
            pos_sec = 0.0
            try:
                pos_sec = float(prog.progress_location)
            except ValueError, TypeError:
                pass
            user_data["PlaybackPositionTicks"] = int(pos_sec * 10_000_000)
            user_data["Played"] = prog.is_completed
            user_data["PlayedPercentage"] = prog.percentage
            user_data["PlayCount"] = 1 if prog.is_completed else 0

        is_fav = any(f.user_id == user_id for f in item.favorited_by)
        user_data["IsFavorite"] = is_fav

    dto: dict[str, Any] = {
        "Name": item.title,
        "ServerId": server_id,
        "Id": to_jellyfin_id(item.id),
        "RunTimeTicks": run_time_ticks,
        "ProductionYear": year,
        "IndexNumber": item.episode or item.track_number,
        "ParentIndexNumber": item.season or item.disc_number,
        "Type": item_type,
        "MediaType": media_type,
        "Overview": item.description,
        "Container": item.file_format,
        "Width": item.resolution_width,
        "Height": item.resolution_height,
        "CommunityRating": None,
        "Genres": [t.name for t in item.tags] if item.tags else [],
        "UserData": user_data,
        "IsFolder": False,
        "Path": item.original_file_path,
        "MediaStreams": _format_media_streams(item),
    }

    if item.collection:
        dto["SeriesName"] = item.collection.name
        dto["SeriesId"] = to_jellyfin_id(item.collection.id)
    if item.library_id:
        dto["ParentId"] = to_jellyfin_id(item.library_id)

    if item.cover_image_path:
        dto["ImageTags"] = {"Primary": item.file_hash or "cover"}
    else:
        dto["ImageTags"] = {}

    return dto


def _format_playback_info(item: MediaItem) -> dict[str, Any]:
    """Builds MediaSources for a given media item."""
    run_time_ticks = int((item.duration or 0) * 10_000_000)
    source_id = to_jellyfin_id(item.id)
    prefix = "/Videos" if item.is_video else "/Audio"
    stream_url = f"{prefix}/{source_id}/stream?static=true"

    return {
        "MediaSources": [
            {
                "Id": source_id,
                "Name": item.title,
                "Path": item.original_file_path,
                "Container": item.file_format,
                "Protocol": "Http",
                "RunTimeTicks": run_time_ticks,
                "SupportsDirectPlay": True,
                "SupportsDirectStream": True,
                "SupportsTranscoding": True,
                "DirectStreamUrl": stream_url,
                "MediaStreams": _format_media_streams(item),
            }
        ],
        "PlaySessionId": uuid.uuid4().hex,
    }


# ==============================================================================
# Discovery & System Endpoints
# ==============================================================================


@jellyfin_bp.route("/System/Info/Public", methods=["GET"])
@jellyfin_bp.route("/system/info/public", methods=["GET"])
def get_system_info_public():
    """Returns basic unauthenticated public server info for discovery handshake."""
    return jsonify(
        {
            "LocalAddress": request.host_url.rstrip("/"),
            "ServerName": SERVER_NAME,
            "Version": JELLYFIN_SERVER_VERSION,
            "ProductName": "Aarkib Jellyfin Server",
            "OperatingSystem": "Linux",
            "Id": get_server_id(),
            "StartupWizardCompleted": True,
        }
    )


@jellyfin_bp.route("/System/Info", methods=["GET"])
@jellyfin_bp.route("/system/info", methods=["GET"])
@jellyfin_auth
def get_system_info(user: User | None = None):
    """Returns full server system info."""
    return jsonify(
        {
            "SystemUpdateLevel": "Release",
            "OperatingSystem": "Linux",
            "Id": get_server_id(),
            "ServerName": SERVER_NAME,
            "Version": JELLYFIN_SERVER_VERSION,
            "LocalAddress": request.host_url.rstrip("/"),
            "CanSelfRestart": False,
            "CanLaunchWebBrowser": False,
            "ProgramDataPath": str(current_app.config.get("DATA_DIR", "/app/data")),
            "WebPath": "/web",
            "ItemsByNamePath": str(current_app.config.get("DATA_DIR", "/app/data")),
            "TranscodingTempPath": str(current_app.config.get("TRANSCODE_DIR", "/tmp")),
            "HasUpdateAvailable": False,
            "SupportsLibraryMonitor": True,
            "EncoderLocationType": "System",
            "SystemArchitecture": "X64",
        }
    )


@jellyfin_bp.route("/System/Configuration", methods=["GET"])
@jellyfin_bp.route("/system/configuration", methods=["GET"])
def get_system_config():
    """Returns system configuration object."""
    return jsonify(
        {
            "IsStartupWizardCompleted": True,
            "ServerName": SERVER_NAME,
            "EnableMetrics": False,
        }
    )


@jellyfin_bp.route("/System/Endpoint", methods=["GET"])
@jellyfin_bp.route("/system/endpoint", methods=["GET"])
def get_system_endpoint():
    """Returns endpoint connectivity status."""
    return jsonify({"IsLocal": True, "IsInNetwork": True})


# ==============================================================================
# Authentication & Users Endpoints
# ==============================================================================


@jellyfin_bp.route("/Users/AuthenticateByName", methods=["POST"])
@jellyfin_bp.route("/users/authenticatebyname", methods=["POST"])
def authenticate_by_name():
    """Authenticates a user and returns UserDto, SessionInfo, and AccessToken."""
    data = request.get_json(silent=True) or request.form
    username = data.get("Username", "").strip()
    password = data.get("Pw", "")

    if not username:
        return jsonify({"message": "Invalid username or password"}), 401

    user = db.session.scalar(select(User).where(User.username == username))
    if not user or not user.check_password(password):
        return jsonify({"message": "Invalid username or password"}), 401

    token = generate_jellyfin_token(user.id)
    server_id = get_server_id()
    client_info = parse_client_info()

    user_dto = _format_user(user)
    session_info = {
        "Id": uuid.uuid4().hex,
        "UserId": to_jellyfin_id(user.id),
        "UserName": user.username,
        "Client": client_info["Client"],
        "DeviceName": client_info["Device"],
        "DeviceId": client_info["DeviceId"],
        "ApplicationVersion": client_info["Version"],
        "ServerId": server_id,
        "UserPrimaryImageTag": None,
    }

    return jsonify(
        {
            "User": user_dto,
            "SessionInfo": session_info,
            "AccessToken": token,
            "ServerId": server_id,
        }
    )


@jellyfin_bp.route("/Users/Public", methods=["GET"])
@jellyfin_bp.route("/users/public", methods=["GET"])
def get_public_users():
    """Returns public users list."""
    users = db.session.scalars(select(User)).all()
    # If users have passwords, return list for easy selection
    return jsonify([_format_user(u) for u in users])


@jellyfin_bp.route("/Users", methods=["GET"])
@jellyfin_auth
def get_users(user: User | None = None):
    """Returns list of users for client user switcher."""
    users = db.session.scalars(select(User)).all()
    return jsonify([_format_user(u) for u in users])


@jellyfin_bp.route("/Users/<user_id>", methods=["GET"])
@jellyfin_bp.route("/users/<user_id>", methods=["GET"])
@jellyfin_auth
def get_user_by_id(user_id: str, user: User | None = None):
    """Returns profile for a specific user ID."""
    db_id = from_jellyfin_id(user_id)
    target_user = db.session.get(User, db_id) if db_id else None
    if not target_user:
        target_user = user
    return jsonify(_format_user(target_user))


@jellyfin_bp.route("/Users/Me", methods=["GET"])
@jellyfin_bp.route("/users/me", methods=["GET"])
@jellyfin_auth
def get_user_me(user: User | None = None):
    """Returns current authenticated user profile."""
    return jsonify(_format_user(user))


@jellyfin_bp.route("/Users/<user_id>/Configuration", methods=["POST"])
@jellyfin_bp.route("/users/<user_id>/configuration", methods=["POST"])
@jellyfin_auth
def set_user_configuration(user_id: str, user: User | None = None):
    """Updates user configuration."""
    return "", 204


# ==============================================================================
# Views & Libraries Endpoints
# ==============================================================================


@jellyfin_bp.route("/Users/<user_id>/Views", methods=["GET"])
@jellyfin_bp.route("/users/<user_id>/views", methods=["GET"])
@jellyfin_auth
def get_user_views(user_id: str, user: User | None = None):
    """Returns root library collection folders for browsing."""
    libraries = db.session.scalars(select(Library)).all()
    items = [_format_view(lib) for lib in libraries]
    return jsonify({"Items": items, "TotalRecordCount": len(items)})


@jellyfin_bp.route("/Library/MediaFolders", methods=["GET"])
@jellyfin_bp.route("/library/mediafolders", methods=["GET"])
@jellyfin_auth
def get_media_folders(user: User | None = None):
    """Returns list of media folders."""
    libraries = db.session.scalars(select(Library)).all()
    items = [_format_view(lib) for lib in libraries]
    return jsonify({"Items": items, "TotalRecordCount": len(items)})


# ==============================================================================
# Catalog & Items Endpoints
# ==============================================================================


@jellyfin_bp.route("/Items", methods=["GET"])
@jellyfin_bp.route("/items", methods=["GET"])
@jellyfin_bp.route("/Users/<user_id>/Items", methods=["GET"])
@jellyfin_bp.route("/users/<user_id>/items", methods=["GET"])
@jellyfin_auth
def get_items(user_id: str | None = None, user: User | None = None):
    """Browses media items with filtering by parent, type, search, and pagination."""
    parent_id_raw = request.args.get("ParentId")
    include_types_raw = request.args.get("IncludeItemTypes", "")
    media_types_raw = request.args.get("MediaTypes", "")
    search_term = request.args.get("SearchTerm", "").strip()
    start_index = int(request.args.get("StartIndex", 0))
    limit = int(request.args.get("Limit", 50))

    uid = user.id if user else (from_jellyfin_id(user_id) if user_id else None)

    query = select(MediaItem)

    # 1. Filter by ParentId (Library ID or Collection ID)
    if parent_id_raw:
        parent_id = from_jellyfin_id(parent_id_raw)
        if parent_id:
            query = query.where(
                or_(
                    MediaItem.library_id == parent_id,
                    MediaItem.collection_id == parent_id,
                )
            )

    # 2. Filter by SearchTerm
    if search_term:
        query = query.where(MediaItem.title.ilike(f"%{search_term}%"))

    # 3. Filter by MediaTypes
    if media_types_raw:
        mtypes = [m.strip().lower() for m in media_types_raw.split(",")]
        conditions = []
        if "video" in mtypes:
            conditions.append(
                or_(
                    MediaItem.media_type.in_(["video", "movie", "tv"]),
                    MediaItem.file_format.in_(VIDEO_EXTENSIONS),
                )
            )
        if "audio" in mtypes:
            conditions.append(
                or_(
                    MediaItem.media_type.in_(
                        ["audio", "music", "audiobook", "podcast"]
                    ),
                    MediaItem.file_format.in_(AUDIO_EXTENSIONS),
                )
            )
        if "book" in mtypes:
            conditions.append(MediaItem.media_type.in_(["book", "comic", "all"]))
        if conditions:
            query = query.where(or_(*conditions))

    # 4. Filter by ItemTypes
    if include_types_raw:
        itypes = [t.strip().lower() for t in include_types_raw.split(",")]
        conditions = []
        if "movie" in itypes:
            conditions.append(
                MediaItem.media_type.in_(["video", "movie"])
                & (MediaItem.season.is_(None) | MediaItem.episode.is_(None))
            )
        if "episode" in itypes:
            conditions.append(
                MediaItem.media_type.in_(["video", "tv", "movie"])
                & MediaItem.season.is_not(None)
                & MediaItem.episode.is_not(None)
            )
        if "audio" in itypes:
            conditions.append(
                or_(
                    MediaItem.media_type.in_(
                        ["audio", "music", "audiobook", "podcast"]
                    ),
                    MediaItem.file_format.in_(AUDIO_EXTENSIONS),
                )
            )
        if "book" in itypes:
            conditions.append(MediaItem.media_type.in_(["book", "comic", "all"]))
        if conditions:
            query = query.where(or_(*conditions))

    # Sorting
    sort_by = request.args.get("SortBy", "SortName")
    sort_order = request.args.get("SortOrder", "Ascending")
    if sort_by == "DateCreated":
        order_col = (
            MediaItem.created_at.desc()
            if sort_order == "Descending"
            else MediaItem.created_at.asc()
        )
    else:
        order_col = (
            MediaItem.title.desc()
            if sort_order == "Descending"
            else MediaItem.title.asc()
        )

    # Count total
    total_count = db.session.scalar(
        select(db.func.count()).select_from(query.subquery())
    )

    # Paginate
    items = db.session.scalars(
        query.order_by(order_col).offset(start_index).limit(limit)
    ).all()

    formatted_items = [_format_item(m, user_id=uid) for m in items]
    return jsonify(
        {
            "Items": formatted_items,
            "TotalRecordCount": total_count or len(formatted_items),
            "StartIndex": start_index,
        }
    )


@jellyfin_bp.route("/Users/<user_id>/Items/Resume", methods=["GET"])
@jellyfin_bp.route("/users/<user_id>/items/resume", methods=["GET"])
@jellyfin_auth
def get_resume_items(user_id: str, user: User | None = None):
    """Returns in-progress items for Continue Watching."""
    uid = user.id if user else from_jellyfin_id(user_id)
    if not uid:
        return jsonify({"Items": [], "TotalRecordCount": 0})

    progress_records = db.session.scalars(
        select(UserProgress)
        .where(
            UserProgress.user_id == uid,
            UserProgress.is_completed.is_(False),
            UserProgress.percentage > 0.0,
        )
        .order_by(UserProgress.last_accessed_at.desc())
        .limit(20)
    ).all()

    items = [
        _format_item(p.media_item, user_id=uid)
        for p in progress_records
        if p.media_item
    ]
    return jsonify({"Items": items, "TotalRecordCount": len(items)})


@jellyfin_bp.route("/Users/<user_id>/Items/Latest", methods=["GET"])
@jellyfin_bp.route("/users/<user_id>/items/latest", methods=["GET"])
@jellyfin_auth
def get_latest_items(user_id: str, user: User | None = None):
    """Returns recently added items for the library or home screen."""
    uid = user.id if user else from_jellyfin_id(user_id)
    limit = int(request.args.get("Limit", 20))
    parent_id_raw = request.args.get("ParentId")

    query = select(MediaItem)
    if parent_id_raw:
        pid = from_jellyfin_id(parent_id_raw)
        if pid:
            query = query.where(
                or_(MediaItem.library_id == pid, MediaItem.collection_id == pid)
            )

    items = db.session.scalars(
        query.order_by(MediaItem.created_at.desc()).limit(limit)
    ).all()
    return jsonify([_format_item(m, user_id=uid) for m in items])


@jellyfin_bp.route("/Items/<item_id>", methods=["GET"])
@jellyfin_bp.route("/items/<item_id>", methods=["GET"])
@jellyfin_bp.route("/Users/<user_id>/Items/<item_id>", methods=["GET"])
@jellyfin_bp.route("/users/<user_id>/items/<item_id>", methods=["GET"])
@jellyfin_auth
def get_item_detail(item_id: str, user_id: str | None = None, user: User | None = None):
    """Returns full item metadata for an individual item."""
    db_id = from_jellyfin_id(item_id)
    if not db_id:
        abort(404, description="Invalid item ID")

    item = db.session.get(MediaItem, db_id)
    if not item:
        # Check if item_id corresponds to a Library or Collection folder
        lib = db.session.get(Library, db_id)
        if lib:
            return jsonify(_format_view(lib))
        col = db.session.get(Collection, db_id)
        if col:
            server_id = get_server_id()
            return jsonify(
                {
                    "Name": col.name,
                    "ServerId": server_id,
                    "Id": to_jellyfin_id(col.id),
                    "Type": "Series",
                    "IsFolder": True,
                }
            )
        abort(404, description="Item not found")

    uid = user.id if user else (from_jellyfin_id(user_id) if user_id else None)
    return jsonify(_format_item(item, user_id=uid))


@jellyfin_bp.route("/Shows/<series_id>/Seasons", methods=["GET"])
@jellyfin_bp.route("/shows/<series_id>/seasons", methods=["GET"])
@jellyfin_auth
def get_seasons(series_id: str, user: User | None = None):
    """Returns seasons for a TV series / collection."""
    db_id = from_jellyfin_id(series_id)
    server_id = get_server_id()
    if not db_id:
        return jsonify({"Items": [], "TotalRecordCount": 0})

    col = db.session.get(Collection, db_id)
    if not col:
        return jsonify({"Items": [], "TotalRecordCount": 0})

    season_numbers = sorted({m.season for m in col.media_items if m.season is not None})
    if not season_numbers:
        season_numbers = [1]

    seasons = [
        {
            "Name": f"Season {s}",
            "ServerId": server_id,
            "Id": f"{db_id:016x}{s:016x}",
            "IndexNumber": s,
            "Type": "Season",
            "SeriesId": to_jellyfin_id(col.id),
            "SeriesName": col.name,
            "IsFolder": True,
        }
        for s in season_numbers
    ]
    return jsonify({"Items": seasons, "TotalRecordCount": len(seasons)})


@jellyfin_bp.route("/Shows/<series_id>/Episodes", methods=["GET"])
@jellyfin_bp.route("/shows/<series_id>/episodes", methods=["GET"])
@jellyfin_auth
def get_episodes(series_id: str, user: User | None = None):
    """Returns episodes for a series."""
    db_id = from_jellyfin_id(series_id)
    if not db_id:
        return jsonify({"Items": [], "TotalRecordCount": 0})

    season_num = request.args.get("Season")
    query = select(MediaItem).where(MediaItem.collection_id == db_id)
    if season_num:
        try:
            query = query.where(MediaItem.season == int(season_num))
        except ValueError:
            pass

    episodes = db.session.scalars(
        query.order_by(MediaItem.season.asc(), MediaItem.episode.asc())
    ).all()
    uid = user.id if user else None
    return jsonify(
        {
            "Items": [_format_item(e, user_id=uid) for e in episodes],
            "TotalRecordCount": len(episodes),
        }
    )


@jellyfin_bp.route("/Artists", methods=["GET"])
@jellyfin_bp.route("/artists", methods=["GET"])
@jellyfin_bp.route("/Artists/AlbumArtists", methods=["GET"])
@jellyfin_bp.route("/artists/albumartists", methods=["GET"])
@jellyfin_auth
def get_artists(user: User | None = None):
    """Returns music artists."""
    server_id = get_server_id()
    authors = db.session.scalars(select(Author).order_by(Author.name.asc())).all()
    artists = []
    for auth in authors:
        audio_items = [
            m
            for m in auth.media_items
            if m.is_audio or m.file_format in AUDIO_EXTENSIONS
        ]
        if not audio_items:
            continue
        artists.append(
            {
                "Name": auth.name,
                "ServerId": server_id,
                "Id": to_jellyfin_id(auth.id),
                "Type": "MusicArtist",
                "IsFolder": True,
            }
        )
    return jsonify({"Items": artists, "TotalRecordCount": len(artists)})


@jellyfin_bp.route("/Genres", methods=["GET"])
@jellyfin_bp.route("/genres", methods=["GET"])
@jellyfin_auth
def get_genres(user: User | None = None):
    """Returns media genres."""
    server_id = get_server_id()
    genres_res = db.session.scalars(
        select(MediaItem.genre).where(MediaItem.genre.is_not(None)).distinct()
    ).all()
    items = [
        {
            "Name": g,
            "ServerId": server_id,
            "Id": to_jellyfin_id(idx + 1),
            "Type": "Genre",
        }
        for idx, g in enumerate(genres_res)
        if g
    ]
    return jsonify({"Items": items, "TotalRecordCount": len(items)})


# ==============================================================================
# Images & Artwork Endpoints
# ==============================================================================


@jellyfin_bp.route("/Items/<item_id>/Images/Primary", methods=["GET"])
@jellyfin_bp.route("/items/<item_id>/images/primary", methods=["GET"])
@jellyfin_bp.route("/Items/<item_id>/Images/Primary/<int:image_index>", methods=["GET"])
@jellyfin_bp.route("/items/<item_id>/images/primary/<int:image_index>", methods=["GET"])
@jellyfin_bp.route("/Items/<item_id>/Images/Backdrop", methods=["GET"])
@jellyfin_bp.route("/items/<item_id>/images/backdrop", methods=["GET"])
@jellyfin_bp.route(
    "/Items/<item_id>/Images/Backdrop/<int:image_index>", methods=["GET"]
)
@jellyfin_bp.route(
    "/items/<item_id>/images/backdrop/<int:image_index>", methods=["GET"]
)
@jellyfin_bp.route("/Items/<item_id>/Images/Thumb", methods=["GET"])
@jellyfin_bp.route("/items/<item_id>/images/thumb", methods=["GET"])
def get_item_image(item_id: str, image_index: int = 0):
    """Returns primary cover art, thumb, or backdrop image for an item."""
    db_id = from_jellyfin_id(item_id)
    if not db_id:
        abort(404, description="Invalid image id")

    item = db.session.get(MediaItem, db_id)
    if item and item.cover_image_path:
        covers_dir = Path(current_app.config.get("COVERS_DIR", "data/covers")).resolve()
        cover_file = (covers_dir / item.cover_image_path).resolve()
        if cover_file.is_file() and cover_file.is_relative_to(covers_dir):
            mime, _ = mimetypes.guess_type(str(cover_file))
            return send_file(
                cover_file, mimetype=mime or "image/webp", conditional=True
            )

    abort(404, description="Artwork not found")


@jellyfin_bp.route("/Users/<user_id>/Images/Primary", methods=["GET"])
@jellyfin_bp.route("/users/<user_id>/images/primary", methods=["GET"])
def get_user_avatar(user_id: str):
    """Returns user avatar or 404."""
    abort(404, description="No avatar configured")


# ==============================================================================
# Playback & Streaming Endpoints
# ==============================================================================


@jellyfin_bp.route("/Items/<item_id>/PlaybackInfo", methods=["GET", "POST"])
@jellyfin_bp.route("/items/<item_id>/playbackinfo", methods=["GET", "POST"])
@jellyfin_auth
def get_playback_info(item_id: str, user: User | None = None):
    """Returns MediaSources and playback capabilities for a media item."""
    db_id = from_jellyfin_id(item_id)
    if not db_id:
        abort(404, description="Invalid item id")

    item = db.session.get(MediaItem, db_id)
    if not item:
        abort(404, description="Media item not found")

    return jsonify(_format_playback_info(item))


@jellyfin_bp.route("/Videos/<item_id>/stream", methods=["GET"])
@jellyfin_bp.route("/videos/<item_id>/stream", methods=["GET"])
@jellyfin_bp.route("/Videos/<item_id>/stream.<ext>", methods=["GET"])
@jellyfin_bp.route("/videos/<item_id>/stream.<ext>", methods=["GET"])
@jellyfin_bp.route("/Audio/<item_id>/stream", methods=["GET"])
@jellyfin_bp.route("/audio/<item_id>/stream", methods=["GET"])
@jellyfin_bp.route("/Audio/<item_id>/stream.<ext>", methods=["GET"])
@jellyfin_bp.route("/audio/<item_id>/stream.<ext>", methods=["GET"])
@jellyfin_bp.route("/Audio/<item_id>/universal", methods=["GET"])
@jellyfin_bp.route("/audio/<item_id>/universal", methods=["GET"])
@jellyfin_bp.route("/Items/<item_id>/Download", methods=["GET"])
@jellyfin_bp.route("/items/<item_id>/download", methods=["GET"])
def stream_jellyfin_media(item_id: str, ext: str | None = None):
    """Direct media streaming with HTTP 206 Partial Content byte range support."""
    db_id = from_jellyfin_id(item_id)
    if not db_id:
        abort(404, description="Invalid media id")

    item = db.session.get(MediaItem, db_id)
    if not item:
        abort(404, description="Media item not found")

    file_path = Path(item.original_file_path).resolve()
    if not file_path.is_file():
        abort(404, description="File missing on disk")

    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        if item.file_format == "mp4":
            mime = "video/mp4"
        elif item.file_format == "mkv":
            mime = "video/x-matroska"
        elif item.file_format == "webm":
            mime = "video/webm"
        elif item.file_format == "mp3":
            mime = "audio/mpeg"
        else:
            mime = "application/octet-stream"

    return send_file(file_path, mimetype=mime, conditional=True)


# ==============================================================================
# Playback Progress & Session Reporting (Scrobbling)
# ==============================================================================


@jellyfin_bp.route("/Sessions/Capabilities/Full", methods=["POST"])
@jellyfin_bp.route("/sessions/capabilities/full", methods=["POST"])
@jellyfin_bp.route("/Sessions/Capabilities", methods=["POST"])
@jellyfin_bp.route("/sessions/capabilities", methods=["POST"])
@jellyfin_auth
def post_capabilities(user: User | None = None):
    """Registers client device capabilities."""
    return "", 204


@jellyfin_bp.route("/Sessions", methods=["GET"])
@jellyfin_bp.route("/sessions", methods=["GET"])
@jellyfin_auth
def get_sessions(user: User | None = None):
    """Returns active client sessions."""
    return jsonify([])


@jellyfin_bp.route("/Sessions/Playing", methods=["POST"])
@jellyfin_bp.route("/sessions/playing", methods=["POST"])
@jellyfin_auth
def session_playing(user: User | None = None):
    """Notifies server that media playback has started."""
    return "", 204


@jellyfin_bp.route("/Sessions/Playing/Progress", methods=["POST"])
@jellyfin_bp.route("/sessions/playing/progress", methods=["POST"])
@jellyfin_auth
def session_playing_progress(user: User | None = None):
    """Updates playback position in UserProgress."""
    data = request.get_json(silent=True) or request.form
    item_id_raw = data.get("ItemId")
    ticks = data.get("PositionTicks")

    if not item_id_raw or ticks is None:
        return "", 204

    db_id = from_jellyfin_id(item_id_raw)
    if not db_id:
        return "", 204

    item = db.session.get(MediaItem, db_id)
    if not item or not user:
        return "", 204

    try:
        sec = float(ticks) / 10_000_000.0
    except ValueError, TypeError:
        return "", 204

    prog = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user.id,
            UserProgress.media_item_id == item.id,
        )
    )
    if not prog:
        prog = UserProgress(user_id=user.id, media_item_id=item.id)
        db.session.add(prog)

    prog.progress_location = str(round(sec, 2))
    if item.duration and item.duration > 0:
        prog.percentage = min(100.0, round((sec / item.duration) * 100.0, 2))
    db.session.commit()
    return "", 204


@jellyfin_bp.route("/Sessions/Playing/Stopped", methods=["POST"])
@jellyfin_bp.route("/sessions/playing/stopped", methods=["POST"])
@jellyfin_auth
def session_playing_stopped(user: User | None = None):
    """Stops playback and updates watched status if threshold is reached."""
    data = request.get_json(silent=True) or request.form
    item_id_raw = data.get("ItemId")
    ticks = data.get("PositionTicks")

    if not item_id_raw or not user:
        return "", 204

    db_id = from_jellyfin_id(item_id_raw)
    if not db_id:
        return "", 204

    item = db.session.get(MediaItem, db_id)
    if not item:
        return "", 204

    prog = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user.id,
            UserProgress.media_item_id == item.id,
        )
    )
    if not prog:
        prog = UserProgress(user_id=user.id, media_item_id=item.id)
        db.session.add(prog)

    if ticks is not None:
        try:
            sec = float(ticks) / 10_000_000.0
            prog.progress_location = str(round(sec, 2))
            if item.duration and item.duration > 0:
                pct = (sec / item.duration) * 100.0
                prog.percentage = min(100.0, round(pct, 2))
                if pct >= 90.0:
                    prog.is_completed = True
        except ValueError, TypeError:
            pass

    db.session.commit()
    return "", 204


@jellyfin_bp.route("/Users/<user_id>/PlayedItems/<item_id>", methods=["POST", "DELETE"])
@jellyfin_bp.route("/users/<user_id>/playeditems/<item_id>", methods=["POST", "DELETE"])
@jellyfin_auth
def toggle_played_item(user_id: str, item_id: str, user: User | None = None):
    """Marks an item as played or unplayed."""
    uid = user.id if user else from_jellyfin_id(user_id)
    db_id = from_jellyfin_id(item_id)
    if not uid or not db_id:
        abort(400, description="Invalid user or item id")

    item = db.session.get(MediaItem, db_id)
    if not item:
        abort(404, description="Item not found")

    prog = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == uid,
            UserProgress.media_item_id == item.id,
        )
    )
    if not prog:
        prog = UserProgress(user_id=uid, media_item_id=item.id)
        db.session.add(prog)

    if request.method == "POST":
        prog.is_completed = True
        prog.percentage = 100.0
        if item.duration:
            prog.progress_location = str(round(item.duration, 2))
    else:
        prog.is_completed = False
        prog.percentage = 0.0
        prog.progress_location = "0"

    db.session.commit()
    return jsonify(_format_item(item, user_id=uid))


@jellyfin_bp.route(
    "/Users/<user_id>/FavoriteItems/<item_id>", methods=["POST", "DELETE"]
)
@jellyfin_bp.route(
    "/users/<user_id>/favoriteitems/<item_id>", methods=["POST", "DELETE"]
)
@jellyfin_auth
def toggle_favorite_item(user_id: str, item_id: str, user: User | None = None):
    """Favorites or unfavorites an item."""
    uid = user.id if user else from_jellyfin_id(user_id)
    db_id = from_jellyfin_id(item_id)
    if not uid or not db_id:
        abort(400, description="Invalid user or item id")

    item = db.session.get(MediaItem, db_id)
    if not item:
        abort(404, description="Item not found")

    fav = db.session.scalar(
        select(UserFavorite).where(
            UserFavorite.user_id == uid,
            UserFavorite.media_item_id == item.id,
        )
    )
    if request.method == "POST" and not fav:
        fav = UserFavorite(user_id=uid, media_item_id=item.id)
        db.session.add(fav)
    elif request.method == "DELETE" and fav:
        db.session.delete(fav)

    db.session.commit()
    return jsonify(_format_item(item, user_id=uid))


@jellyfin_bp.route("/Search/Hints", methods=["GET"])
@jellyfin_bp.route("/search/hints", methods=["GET"])
@jellyfin_auth
def get_search_hints(user: User | None = None):
    """Provides quick search suggestions."""
    term = request.args.get("searchTerm", "").strip()
    if not term:
        return jsonify({"SearchHints": [], "TotalRecordCount": 0})

    items = db.session.scalars(
        select(MediaItem).where(MediaItem.title.ilike(f"%{term}%")).limit(15)
    ).all()
    hints = [
        {
            "ItemId": to_jellyfin_id(m.id),
            "Id": to_jellyfin_id(m.id),
            "Name": m.title,
            "Type": "Movie" if m.is_video else ("Audio" if m.is_audio else "Book"),
        }
        for m in items
    ]
    return jsonify({"SearchHints": hints, "TotalRecordCount": len(hints)})


# ==============================================================================
# Protocol Plugin Definition
# ==============================================================================


class JellyfinProtocolPlugin(ProtocolPlugin):
    """Jellyfin REST streaming and catalog protocol plugin."""

    name = "jellyfin"
    display_name = "Jellyfin Client API"
    description = "Jellyfin REST streaming & catalog API for Jellyfin mobile, TV, and desktop apps"
    protocol_version = JELLYFIN_SERVER_VERSION
    csrf_exempt = True
    blueprint_options: ClassVar[dict[str, Any]] = {"url_prefix": ""}

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        """Returns the Jellyfin REST API blueprint."""
        return jellyfin_bp

    def check_health(self) -> dict[str, Any]:
        """Performs health check for the Jellyfin protocol plugin."""
        health = super().check_health()
        health.update(
            {
                "status": "ok",
                "protocol_version": self.protocol_version,
                "server_id": get_server_id(),
            }
        )
        return health


__all__ = [
    "jellyfin_bp",
    "JELLYFIN_SERVER_VERSION",
    "SERVER_NAME",
    "get_server_id",
    "to_jellyfin_id",
    "from_jellyfin_id",
    "generate_jellyfin_token",
    "verify_jellyfin_token",
    "extract_jellyfin_token",
    "get_authenticated_user",
    "jellyfin_auth",
    "JellyfinProtocolPlugin",
]
