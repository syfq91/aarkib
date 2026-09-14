"""Subsonic REST API 1.16.1 compatible protocol plugin for mobile media streaming."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    request,
    send_file,
)
from flask_login import current_user
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from aarkib.extensions import db
from aarkib.models import Author, Collection, Library, MediaItem, User, UserProgress
from aarkib.plugins.base import ProtocolPlugin
from aarkib.services.media_service import AUDIO_EXTENSIONS

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)

subsonic_bp = Blueprint("subsonic", __name__)

SUBSONIC_VERSION = "1.16.1"
SERVER_VERSION = "0.1.0"


@subsonic_bp.before_request
def _guard_subsonic_enabled():
    """Guards Subsonic routes when Subsonic protocol plugin is disabled."""
    from aarkib.plugins.base import plugin_registry

    plugin = plugin_registry.get_plugin("subsonic")
    if (plugin and not plugin.enabled) or not current_app.config.get(
        "ENABLE_SUBSONIC", True
    ):
        return subsonic_response(
            error_code=0,
            error_msg="Subsonic API is disabled on this server.",
        )


def subsonic_response(
    data: dict[str, Any] | None = None,
    error_code: int | None = None,
    error_msg: str | None = None,
) -> Response:
    """Wraps output in standard Subsonic response format (JSON or XML)."""
    fmt = request.values.get("f", "json").lower()
    is_ok = error_code is None

    payload: dict[str, Any] = {
        "status": "ok" if is_ok else "failed",
        "version": SUBSONIC_VERSION,
        "type": "aarkib",
        "serverVersion": SERVER_VERSION,
        "openSubsonic": True,
    }

    if not is_ok:
        payload["error"] = {
            "code": error_code,
            "message": error_msg or "Unknown error",
        }
    elif data:
        payload.update(data)

    if fmt == "xml":
        # Minimal XML format if specifically requested by older clients
        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<subsonic-response xmlns="http://subsonic.org/restapi" status="{payload["status"]}" version="{SUBSONIC_VERSION}">',
        ]
        if not is_ok:
            xml_lines.append(
                f'  <error code="{error_code}" message="{error_msg or ""}"/>'
            )
        xml_lines.append("</subsonic-response>")
        return Response("\n".join(xml_lines), mimetype="application/xml")

    return jsonify({"subsonic-response": payload})


def get_authenticated_user() -> User | None:
    """Authenticates Subsonic client via user credentials, tokens, or session."""
    username = request.values.get("u")
    password = request.values.get("p")
    token = request.values.get("t")
    salt = request.values.get("s")

    # Check HTTP Basic Auth fallback
    auth = request.authorization
    if not username and auth:
        username = auth.username
        password = auth.password

    # If credentials were provided, always validate them
    if username:
        user = db.session.scalar(select(User).where(User.username == username))
        if not user:
            return None

        # 1. Plain password authentication
        if password is not None:
            clean_pw = password
            if clean_pw.startswith("enc:"):
                try:
                    clean_pw = bytes.fromhex(clean_pw[4:]).decode("utf-8")
                except Exception as exc:
                    logger.debug("Failed decoding hex password: %s", exc)
            if user.check_password(clean_pw):
                return user
            return None

        # 2. Token auth: token = md5(password + salt)
        if token and salt:
            # Check against user's stored plaintext/API token or test token if configured
            api_token = getattr(user, "api_token", None)
            if api_token:
                expected = hashlib.md5(
                    (api_token + salt).encode("utf-8"), usedforsecurity=False
                ).hexdigest()
                if token.lower() == expected.lower():
                    return user
            # Allow checking if token matches direct MD5
            if user.check_password(token):
                return user
            return None

        # 3. Passwordless user without credentials parameter
        if not user.has_password:
            return user

        return None

    if current_user.is_authenticated:
        return current_user

    return None


def subsonic_auth(f):
    """Decorator ensuring valid Subsonic authentication."""

    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_authenticated_user()
        if not user:
            return subsonic_response(
                error_code=40,
                error_msg="Wrong username or password",
            )
        return f(*args, user=user, **kwargs)

    return decorated


def _format_song(item: MediaItem) -> dict[str, Any]:
    """Formats a MediaItem into a Subsonic Child/Song structure."""
    album_name = (
        item.collection.name if item.collection else (item.album or "Unknown Album")
    )
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

    track_no = item.track_number
    if track_no is None and item.series_index is not None:
        try:
            track_no = int(item.series_index)
        except ValueError, TypeError:
            pass

    mime, _ = mimetypes.guess_type(item.original_file_path)
    if not mime:
        mime = "audio/mpeg" if item.file_format == "mp3" else "audio/mp4"

    return {
        "id": str(item.id),
        "parent": str(item.collection_id or item.library_id or "1"),
        "isDir": False,
        "title": item.title,
        "album": album_name,
        "artist": item.creators_display,
        "track": track_no or 1,
        "year": year,
        "genre": item.genre or (item.tags[0].name if item.tags else "Audio"),
        "coverArt": str(item.id),
        "size": item.file_size,
        "contentType": mime,
        "suffix": item.file_format,
        "duration": int(item.duration or 0),
        "bitRate": item.bitrate or 320,
        "path": item.original_file_path,
        "playCount": 0,
        "created": item.created_at.isoformat() if item.created_at else None,
        "albumId": str(item.collection_id) if item.collection_id else None,
        "artistId": str(item.creators[0].id) if item.creators else None,
        "type": item.media_type or "music",
    }


def _format_album(col: Collection) -> dict[str, Any]:
    """Formats a Collection into a Subsonic AlbumID3 / Album structure."""
    items = col.media_items
    artist_name = items[0].creators_display if items else "Various Artists"
    artist_id = str(items[0].creators[0].id) if (items and items[0].creators) else None
    cover_art_id = str(items[0].id) if items else str(col.id)
    total_dur = int(sum(m.duration or 0 for m in items))

    return {
        "id": str(col.id),
        "name": col.name,
        "artist": artist_name,
        "artistId": artist_id,
        "coverArt": cover_art_id,
        "songCount": len(items),
        "duration": total_dur,
        "created": None,
    }


# ==============================================================================
# Endpoints (support both action and action.view)
# ==============================================================================


@subsonic_bp.route("/ping", methods=["GET", "POST"])
@subsonic_bp.route("/ping.view", methods=["GET", "POST"])
@subsonic_auth
def ping(user: User | None = None):
    return subsonic_response({})


@subsonic_bp.route("/getLicense", methods=["GET", "POST"])
@subsonic_bp.route("/getLicense.view", methods=["GET", "POST"])
@subsonic_auth
def get_license(user: User | None = None):
    return subsonic_response(
        {
            "license": {
                "valid": True,
                "email": "aarkib@local",
                "licenseExpires": "2099-01-01T00:00:00Z",
            }
        }
    )


@subsonic_bp.route("/getMusicFolders", methods=["GET", "POST"])
@subsonic_bp.route("/getMusicFolders.view", methods=["GET", "POST"])
@subsonic_auth
def get_music_folders(user: User | None = None):
    libs = db.session.scalars(
        select(Library).where(
            or_(
                Library.media_type.in_(
                    ["audio", "music", "audiobook", "podcast", "all"]
                ),
                Library.media_type.is_(None),
            )
        )
    ).all()
    folders = [{"id": lib.id, "name": lib.name} for lib in libs]
    return subsonic_response({"musicFolders": {"musicFolder": folders}})


@subsonic_bp.route("/getArtists", methods=["GET", "POST"])
@subsonic_bp.route("/getArtists.view", methods=["GET", "POST"])
@subsonic_bp.route("/getIndexes", methods=["GET", "POST"])
@subsonic_bp.route("/getIndexes.view", methods=["GET", "POST"])
@subsonic_auth
def get_artists(user: User | None = None):
    authors = db.session.scalars(
        select(Author)
        .options(
            selectinload(Author.media_items).selectinload(MediaItem.collection),
        )
        .order_by(Author.name.asc())
    ).all()
    index_map: dict[str, list[dict[str, Any]]] = {}

    for auth in authors:
        # Verify author has audio/music/podcast items
        audio_items = [
            m
            for m in auth.media_items
            if m.is_audio or m.file_format in AUDIO_EXTENSIONS
        ]
        if not audio_items:
            continue

        first_char = auth.name[0].upper() if auth.name else "#"
        if not first_char.isalpha():
            first_char = "#"

        artist_obj = {
            "id": str(auth.id),
            "name": auth.name,
            "albumCount": len(
                {m.collection_id for m in audio_items if m.collection_id}
            ),
        }
        index_map.setdefault(first_char, []).append(artist_obj)

    indexes = [{"name": k, "artist": v} for k, v in sorted(index_map.items())]
    return subsonic_response({"artists": {"index": indexes}})


@subsonic_bp.route("/getArtist", methods=["GET", "POST"])
@subsonic_bp.route("/getArtist.view", methods=["GET", "POST"])
@subsonic_auth
def get_artist(user: User | None = None):
    artist_id = request.values.get("id")
    if not artist_id:
        return subsonic_response(error_code=10, error_msg="Missing artist id")

    auth = db.session.scalar(
        select(Author)
        .options(
            selectinload(Author.media_items).selectinload(MediaItem.collection),
        )
        .where(Author.id == int(artist_id))
    )
    if not auth:
        return subsonic_response(error_code=70, error_msg="Artist not found")

    # Collect distinct collections/albums for this artist
    col_ids = {
        m.collection_id
        for m in auth.media_items
        if m.collection_id and (m.is_audio or m.file_format in AUDIO_EXTENSIONS)
    }
    albums = []
    if col_ids:
        cols = db.session.scalars(
            select(Collection)
            .options(
                selectinload(Collection.media_items).selectinload(MediaItem.creators),
                selectinload(Collection.media_items).selectinload(MediaItem.tags),
            )
            .where(Collection.id.in_(col_ids))
        ).all()
        albums = [_format_album(c) for c in cols]

    artist_data = {
        "id": str(auth.id),
        "name": auth.name,
        "albumCount": len(albums),
        "album": albums,
    }
    return subsonic_response({"artist": artist_data})


@subsonic_bp.route("/getAlbum", methods=["GET", "POST"])
@subsonic_bp.route("/getAlbum.view", methods=["GET", "POST"])
@subsonic_auth
def get_album(user: User | None = None):
    album_id = request.values.get("id")
    if not album_id:
        return subsonic_response(error_code=10, error_msg="Missing album id")

    col = db.session.scalar(
        select(Collection)
        .options(
            selectinload(Collection.media_items).selectinload(MediaItem.creators),
            selectinload(Collection.media_items).selectinload(MediaItem.tags),
        )
        .where(Collection.id == int(album_id))
    )
    if not col:
        return subsonic_response(error_code=70, error_msg="Album not found")

    songs = [
        _format_song(item)
        for item in col.media_items
        if item.is_audio or item.file_format in AUDIO_EXTENSIONS
    ]

    album_data = _format_album(col)
    album_data["song"] = songs
    return subsonic_response({"album": album_data})


@subsonic_bp.route("/getSong", methods=["GET", "POST"])
@subsonic_bp.route("/getSong.view", methods=["GET", "POST"])
@subsonic_auth
def get_song(user: User | None = None):
    song_id = request.values.get("id")
    if not song_id:
        return subsonic_response(error_code=10, error_msg="Missing song id")

    item = db.session.get(MediaItem, int(song_id))
    if not item:
        return subsonic_response(error_code=70, error_msg="Song not found")

    return subsonic_response({"song": _format_song(item)})


@subsonic_bp.route("/stream", methods=["GET", "POST"])
@subsonic_bp.route("/stream.view", methods=["GET", "POST"])
@subsonic_auth
def stream_media(user: User | None = None):
    """Streams audio track with HTTP 206 Partial Content byte range support."""
    item_id = request.values.get("id")
    if not item_id:
        abort(400, description="Missing media id")

    try:
        clean_item_id = int(item_id)
    except ValueError, TypeError:
        abort(404, description="Invalid media id")

    item = db.session.get(MediaItem, clean_item_id)
    if not item:
        abort(404, description="Media item not found")

    from aarkib.routes.api import is_safe_media_path

    file_path = Path(item.original_file_path).resolve()
    if not is_safe_media_path(file_path):
        abort(
            403,
            description="Access denied: file resides outside configured library roots",
        )
    if not file_path.is_file():
        abort(404, description="File missing on disk")

    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "audio/mpeg" if item.file_format == "mp3" else "audio/mp4"

    db.session.close()
    return send_file(file_path, mimetype=mime, conditional=True)


@subsonic_bp.route("/getCoverArt", methods=["GET", "POST"])
@subsonic_bp.route("/getCoverArt.view", methods=["GET", "POST"])
@subsonic_auth
def get_cover_art(user: User | None = None):
    """Returns cover image for a media item or collection."""
    cover_id = request.values.get("id")
    if not cover_id:
        abort(400, description="Missing cover id")

    try:
        clean_cover_id = int(cover_id)
    except ValueError, TypeError:
        abort(404, description="Invalid cover id")

    item = db.session.get(MediaItem, clean_cover_id)
    if item and item.cover_image_path:
        covers_dir = Path(current_app.config.get("COVERS_DIR", "data/covers")).resolve()
        cover_file = (covers_dir / item.cover_image_path).resolve()
        if cover_file.is_file() and cover_file.is_relative_to(covers_dir):
            db.session.close()
            return send_file(cover_file, mimetype="image/webp", conditional=True)

    # Fallback default 1x1 png or 404
    abort(404, description="Cover art not found")


@subsonic_bp.route("/search3", methods=["GET", "POST"])
@subsonic_bp.route("/search3.view", methods=["GET", "POST"])
@subsonic_auth
def search3(user: User | None = None):
    """Unified search matching artists, albums, and tracks."""
    query = request.values.get("query", "").strip()
    if not query:
        return subsonic_response({"searchResult3": {}})

    # Songs matching title
    matching_songs = db.session.scalars(
        select(MediaItem)
        .options(
            selectinload(MediaItem.creators),
            selectinload(MediaItem.tags),
            selectinload(MediaItem.collection),
        )
        .where(
            MediaItem.title.ilike(f"%{query}%"),
            or_(
                MediaItem.media_type.in_(["music", "audio", "audiobook", "podcast"]),
                MediaItem.file_format.in_(AUDIO_EXTENSIONS),
            ),
        )
        .limit(20)
    ).all()

    # Albums matching name
    matching_albums = db.session.scalars(
        select(Collection)
        .options(
            selectinload(Collection.media_items).selectinload(MediaItem.creators),
            selectinload(Collection.media_items).selectinload(MediaItem.tags),
        )
        .where(Collection.name.ilike(f"%{query}%"))
        .limit(10)
    ).all()

    # Artists matching name
    matching_artists = db.session.scalars(
        select(Author).where(Author.name.ilike(f"%{query}%")).limit(10)
    ).all()

    return subsonic_response(
        {
            "searchResult3": {
                "song": [_format_song(s) for s in matching_songs],
                "album": [_format_album(a) for a in matching_albums],
                "artist": [
                    {"id": str(ar.id), "name": ar.name} for ar in matching_artists
                ],
            }
        }
    )


@subsonic_bp.route("/scrobble", methods=["GET", "POST"])
@subsonic_bp.route("/scrobble.view", methods=["GET", "POST"])
@subsonic_auth
def scrobble(user: User | None = None):
    """Updates play progress and scrobbles played status to UserProgress."""
    item_id = request.values.get("id")
    submission = request.values.get("submission", "true").lower() == "true"
    time_ms = request.values.get("time")

    if not item_id:
        return subsonic_response(error_code=10, error_msg="Missing track id")

    item = db.session.get(MediaItem, int(item_id))
    if not item:
        return subsonic_response(error_code=70, error_msg="Media item not found")

    user_id = user.id if user else None
    prog = db.session.scalar(
        select(UserProgress).where(
            UserProgress.user_id == user_id,
            UserProgress.media_item_id == item.id,
        )
    )
    if not prog:
        prog = UserProgress(
            user_id=user_id,
            media_item_id=item.id,
        )
        db.session.add(prog)

    if submission:
        prog.is_completed = True
        prog.percentage = 100.0
    elif time_ms:
        try:
            sec = float(time_ms) / 1000.0
            prog.progress_location = str(round(sec, 2))
            if item.duration and item.duration > 0:
                prog.percentage = min(100.0, round((sec / item.duration) * 100.0, 2))
        except ValueError, TypeError:
            pass

    db.session.commit()
    return subsonic_response({})


class SubsonicProtocolPlugin(ProtocolPlugin):
    """Subsonic v1.16.1 protocol plugin providing REST streaming and catalog API."""

    name = "subsonic"
    display_name = "Subsonic / OpenSubsonic API"
    description = (
        "Subsonic v1.16.1 REST streaming & catalog API for third-party mobile clients"
    )
    protocol_version = SUBSONIC_VERSION
    csrf_exempt = True
    blueprint_options: ClassVar[dict[str, Any]] = {"url_prefix": "/rest"}

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        """Returns the Subsonic REST API blueprint."""
        return subsonic_bp

    def check_health(self) -> dict[str, Any]:
        """Performs health check for the Subsonic protocol plugin."""
        health = super().check_health()
        health.update(
            {
                "status": "ok",
                "protocol_version": self.protocol_version,
                "url_prefix": self.blueprint_options.get("url_prefix", "/rest"),
            }
        )
        return health


__all__ = [
    "subsonic_bp",
    "SUBSONIC_VERSION",
    "SERVER_VERSION",
    "subsonic_response",
    "subsonic_auth",
    "SubsonicProtocolPlugin",
]
