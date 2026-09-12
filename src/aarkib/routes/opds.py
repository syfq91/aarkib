from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from aarkib.extensions import db
from aarkib.models import Author, MediaItem, Series, Tag, User, UserProgress

logger = logging.getLogger(__name__)

opds_bp = Blueprint("opds", __name__, url_prefix="/opds")

OPDS_NAV_TYPE = (
    "application/atom+xml;profile=opds-catalog;kind=navigation;charset=utf-8"
)
OPDS_ACQ_TYPE = (
    "application/atom+xml;profile=opds-catalog;kind=acquisition;charset=utf-8"
)
OPENSEARCH_TYPE = "application/opensearchdescription+xml;charset=utf-8"

# OPDS 2.0 & OPDS Progression 1.0 MIME types
OPDS_PROGRESSION_TYPE = "application/opds-progression+json"
OPDS_AUTH_TYPE = "application/opds-authentication+json"
OPDS_JSON_TYPE = "application/opds+json"
PROBLEM_JSON_TYPE = "application/problem+json"

PRESET_RULE = "<any(x3,x4,kindle,kobo,eink,generic):preset>"

OPDS_READABLE_TYPES: tuple[str, ...] = ("book", "comic", "audiobook")


def opds_readable_filter():
    """Filter condition restricting OPDS catalog items to readable media."""
    return or_(
        MediaItem.media_type.in_(OPDS_READABLE_TYPES),
        MediaItem.media_type.is_(None),
    )


def get_opds_user() -> User | None:
    """Returns the authenticated user via session or HTTP Basic Auth."""
    if current_user.is_authenticated:
        return current_user  # type: ignore

    auth = request.authorization
    if auth and auth.username and auth.password:
        user = db.session.scalar(select(User).where(User.username == auth.username))
        if user and user.check_password(auth.password):
            return user
    return None


def opds_auth_required(f: Any) -> Any:
    """Enforce HTTP Basic auth on OPDS feeds when AUTH_REQUIRED is enabled."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        """Authenticate the request or return a 401 Basic-auth challenge."""
        if current_app.config.get("AUTH_REQUIRED", False):
            user = get_opds_user()
            if not user:
                return Response(
                    "Authentication required for Aarkib OPDS Catalog",
                    401,
                    {"WWW-Authenticate": 'Basic realm="Aarkib OPDS"'},
                )
        return f(*args, **kwargs)

    wrapper.__name__ = f.__name__
    return wrapper


def make_opds_auth_document() -> dict[str, Any]:
    """Generates an OPDS Authentication Document compliant with OPDS 2.0 / Progression 1.0."""
    base_url = request.host_url.rstrip("/")
    return {
        "id": f"{base_url}/opds/authentication.json",
        "title": "Aarkib Authentication",
        "description": "Please provide your Aarkib credentials to access your library and synchronize reading progress.",
        "links": [
            {
                "rel": "authenticate",
                "type": OPDS_AUTH_TYPE,
                "href": f"{base_url}/opds/authentication.json",
            }
        ],
        "authentication": [
            {
                "type": "http://opds-spec.org/auth/basic",
                "labels": {"login": "Username", "password": "Password"},
            }
        ],
    }


def format_progression_document(
    progress: UserProgress, user: User | None = None
) -> dict[str, Any]:
    """Formats a UserProgress record into an OPDS Progression 1.0 Document."""
    last_read = progress.last_read_at
    if last_read.tzinfo is None:
        last_read = last_read.replace(tzinfo=UTC)

    # Progression is a float between 0.0 and 1.0
    progression_val = max(0.0, min(1.0, float(progress.percentage) / 100.0))

    device_id = progress.device_id or (
        f"urn:aarkib:user:{user.id}" if user else f"urn:aarkib:progress:{progress.id}"
    )
    device_name = progress.device_name or "Aarkib Reader"

    doc: dict[str, Any] = {
        "modified": last_read.isoformat(),
        "device": {
            "id": device_id,
            "name": device_name,
        },
        "progression": round(progression_val, 4),
    }

    if progress.progress_location:
        if progress.references_json:
            try:
                refs = json.loads(progress.references_json)
                if isinstance(refs, list) and refs:
                    doc["references"] = refs
                else:
                    doc["references"] = [progress.progress_location]
            except Exception:
                doc["references"] = [progress.progress_location]
        else:
            doc["references"] = [progress.progress_location]

    if progress.chapter_title:
        doc["title"] = progress.chapter_title

    return doc


@opds_bp.route("/authentication.json", methods=["GET"])
def opds_authentication_doc():
    """Serve the OPDS Authentication Document (application/opds-authentication+json)."""
    doc = make_opds_auth_document()
    return Response(json.dumps(doc, indent=2), status=200, mimetype=OPDS_AUTH_TYPE)


@opds_bp.route("/presets", methods=["GET"])
def list_presets():
    """Returns list of supported e-ink optimization presets."""
    from aarkib.services.optimizer import DEVICE_PRESETS

    return jsonify(DEVICE_PRESETS)


def _parse_progression_payload(payload) -> tuple[dict | None, Response | None]:
    """Validates an OPDS Progression update payload.

    Returns (values_dict, error_response). On failure values_dict is None and
    error_response carries a problem+json document.
    """
    if not isinstance(payload, dict):
        problem = {
            "type": "https://registry.opds.io/error#progression-invalid-payload",
            "title": "Progression could not be updated due to an invalid payload.",
        }
        return None, Response(
            json.dumps(problem), status=400, mimetype=PROBLEM_JSON_TYPE
        )

    if "progression" not in payload or "device" not in payload:
        problem = {
            "type": "https://registry.opds.io/error#progression-invalid-payload",
            "title": "Missing required fields: 'progression' and 'device' are mandatory.",
        }
        return None, Response(
            json.dumps(problem), status=400, mimetype=PROBLEM_JSON_TYPE
        )

    try:
        prog_raw = float(payload["progression"])
        if prog_raw <= 1.0 and prog_raw >= 0.0:
            percentage = prog_raw * 100.0
        else:
            percentage = max(0.0, min(100.0, prog_raw))
    except ValueError, TypeError:
        problem = {
            "type": "https://registry.opds.io/error#progression-invalid-payload",
            "title": "Invalid 'progression' value.",
        }
        return None, Response(
            json.dumps(problem), status=400, mimetype=PROBLEM_JSON_TYPE
        )

    device_info = payload.get("device", {})
    if (
        not isinstance(device_info, dict)
        or not device_info.get("id")
        or not device_info.get("name")
    ):
        problem = {
            "type": "https://registry.opds.io/error#progression-invalid-payload",
            "title": "Invalid 'device' object. 'id' and 'name' are required.",
        }
        return None, Response(
            json.dumps(problem), status=400, mimetype=PROBLEM_JSON_TYPE
        )

    refs = payload.get("references")
    references_json = json.dumps(refs) if isinstance(refs, list) and refs else None
    primary_location = refs[0] if isinstance(refs, list) and refs else str(percentage)

    return (
        {
            "percentage": percentage,
            "device_id": str(device_info.get("id")),
            "device_name": str(device_info.get("name")),
            "chapter_title": payload.get("title"),
            "references_json": references_json,
            "primary_location": primary_location,
            "modified_dt": _parse_modified_timestamp(payload.get("modified")),
        },
        None,
    )


def _parse_modified_timestamp(modified_str) -> datetime:
    """Parses an RFC3339-ish modified timestamp, defaulting to now."""
    if not modified_str:
        return datetime.now(UTC)
    try:
        if modified_str.endswith("Z"):
            modified_str = modified_str[:-1] + "+00:00"
        parsed_dt = datetime.fromisoformat(modified_str)
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=UTC)
        return parsed_dt
    except Exception:
        logger.debug("Invalid 'modified' timestamp %r: %s", modified_str, exc_info=True)
        return datetime.now(UTC)


def _build_progression_response(progress, user) -> Response:
    """Serializes a UserProgress record as an OPDS Progression document."""
    doc = format_progression_document(progress, user)
    return Response(
        json.dumps(doc, indent=2), status=200, mimetype=OPDS_PROGRESSION_TYPE
    )


def _resolve_progression_conflict(progress, modified_dt, user) -> Response | None:
    """Returns a 409 problem response if an incoming update is older than the stored one."""
    if progress and progress.last_read_at:
        curr_dt = progress.last_read_at
        if curr_dt.tzinfo is None:
            curr_dt = curr_dt.replace(tzinfo=UTC)
        if modified_dt < curr_dt:
            problem = {
                "type": "https://registry.opds.io/error#progression-date",
                "title": "A newer progression timestamp has already been registered.",
                "current": format_progression_document(progress, user),
            }
            return Response(
                json.dumps(problem, indent=2),
                status=409,
                mimetype=PROBLEM_JSON_TYPE,
            )
    return None


@opds_bp.route("/media/<int:item_id>/progression", methods=["GET", "PUT", "POST"])
@opds_bp.route("/v2/media/<int:item_id>/progression", methods=["GET", "PUT", "POST"])
def opds_media_progression(item_id: int):
    """OPDS Progression 1.0 Fetch and Update endpoint."""
    user = get_opds_user()
    if current_app.config.get("AUTH_REQUIRED", False) and not user:
        auth_doc = make_opds_auth_document()
        return Response(
            json.dumps(auth_doc),
            status=401,
            mimetype=OPDS_AUTH_TYPE,
            headers={"WWW-Authenticate": 'Basic realm="Aarkib OPDS"'},
        )

    item = db.session.get(MediaItem, item_id)
    if not item:
        problem = {
            "type": "https://registry.opds.io/error#publication-not-found",
            "title": "Publication not found in Aarkib catalog.",
        }
        return Response(json.dumps(problem), status=404, mimetype=PROBLEM_JSON_TYPE)

    user_id = user.id if user else None

    # --- GET: Fetch Progression ---
    if request.method == "GET":
        progress = db.session.scalar(
            select(UserProgress).where(
                UserProgress.media_item_id == item_id,
                UserProgress.user_id == user_id,
            )
        )
        if not progress or (
            progress.percentage == 0 and progress.progress_location in ("0", "")
        ):
            return Response("{}", status=200, mimetype=OPDS_PROGRESSION_TYPE)

        return _build_progression_response(progress, user)

    # --- PUT / POST: Update Progression ---
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        problem = {
            "type": "https://registry.opds.io/error#progression-invalid-payload",
            "title": "Progression could not be updated due to an invalid payload.",
        }
        return Response(json.dumps(problem), status=400, mimetype=PROBLEM_JSON_TYPE)

    vals, error_response = _parse_progression_payload(payload)
    if error_response is not None:
        return error_response
    assert vals is not None

    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.media_item_id == item_id,
            UserProgress.user_id == user_id,
        )
    )

    conflict = _resolve_progression_conflict(progress, vals["modified_dt"], user)
    if conflict is not None:
        return conflict

    is_new = progress is None
    if not progress:
        progress = UserProgress(
            media_item_id=item_id,
            user_id=user_id,
            progress_location=vals["primary_location"],
            percentage=vals["percentage"],
            is_completed=(vals["percentage"] >= 100.0),
            last_read_at=vals["modified_dt"],
            device_id=vals["device_id"],
            device_name=vals["device_name"],
            chapter_title=vals["chapter_title"],
            references_json=vals["references_json"],
        )
        db.session.add(progress)
    else:
        progress.progress_location = vals["primary_location"]
        progress.percentage = vals["percentage"]
        progress.is_completed = vals["percentage"] >= 100.0
        progress.last_read_at = vals["modified_dt"]
        progress.device_id = vals["device_id"]
        progress.device_name = vals["device_name"]
        progress.chapter_title = vals["chapter_title"]
        progress.references_json = vals["references_json"]

    db.session.commit()
    updated_doc = format_progression_document(progress, user)
    return Response(
        json.dumps(updated_doc, indent=2),
        status=201 if is_new else 200,
        mimetype=OPDS_PROGRESSION_TYPE,
    )


# ==========================================
# OPDS 2.0 JSON Catalog & Feeds
# ==========================================


@opds_bp.route("/v2/catalog.json", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/v2/catalog.json", methods=["GET"])
@opds_auth_required
def opds2_catalog(preset: str | None = None):
    """Returns OPDS 2.0 Navigation Catalog."""
    base_url = request.host_url.rstrip("/")
    opds_prefix = f"/opds/{preset}" if preset else "/opds"
    catalog_title = (
        f"Aarkib ({preset.upper()} Optimized) OPDS 2.0 Catalog"
        if preset
        else "Aarkib OPDS 2.0 Catalog"
    )

    catalog = {
        "metadata": {
            "title": catalog_title,
            "modified": datetime.now(UTC).isoformat(),
        },
        "links": [
            {
                "rel": "self",
                "href": f"{base_url}{opds_prefix}/v2/catalog.json",
                "type": OPDS_JSON_TYPE,
            },
            {
                "rel": "http://opds-spec.org/auth/basic",
                "href": f"{base_url}/opds/authentication.json",
                "type": OPDS_AUTH_TYPE,
            },
        ],
        "navigation": [
            {
                "title": "Recent Additions",
                "href": f"{base_url}{opds_prefix}/v2/recent.json",
                "type": OPDS_JSON_TYPE,
            }
        ],
    }
    return Response(json.dumps(catalog, indent=2), status=200, mimetype=OPDS_JSON_TYPE)


@opds_bp.route("/v2/recent.json", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/v2/recent.json", methods=["GET"])
@opds_auth_required
def opds2_recent(preset: str | None = None):
    """Returns OPDS 2.0 Recent Publications Feed with Progression 1.0 links."""
    base_url = request.host_url.rstrip("/")
    opds_prefix = f"/opds/{preset}" if preset else "/opds"
    items = db.session.scalars(
        select(MediaItem)
        .options(selectinload(MediaItem.authors), selectinload(MediaItem.series))
        .where(opds_readable_filter())
        .order_by(MediaItem.created_at.desc())
        .limit(50)
    ).all()

    publications = []
    for b in items:
        if preset and b.file_format == "epub":
            acq_href = f"{base_url}/api/media/{b.id}/download/optimized/{preset}"
        else:
            acq_href = f"{base_url}/api/media/{b.id}/download"

        pub: dict[str, Any] = {
            "metadata": {
                "@type": "http://schema.org/Book",
                "title": b.title,
                "identifier": f"urn:aarkib:media:{b.id}",
                "modified": (b.updated_at or datetime.now(UTC)).isoformat(),
                "author": [{"name": a.name} for a in b.authors]
                if b.authors
                else [{"name": "Unknown Author"}],
            },
            "links": [
                {
                    "rel": "http://opds-spec.org/image",
                    "href": f"{base_url}/api/media/{b.id}/cover",
                    "type": "image/webp",
                },
                {
                    "rel": "http://opds-spec.org/acquisition",
                    "href": acq_href,
                    "type": "application/epub+zip"
                    if b.file_format == "epub"
                    else "application/vnd.comicbook+zip",
                },
                {
                    "rel": "http://opds-spec.org/progression",
                    "href": f"{base_url}/opds/media/{b.id}/progression",
                    "type": OPDS_PROGRESSION_TYPE,
                },
            ],
        }
        if b.description:
            pub["metadata"]["description"] = b.description
        if b.series:
            pub["metadata"]["belongsTo"] = {
                "series": {
                    "name": b.series.name,
                    "position": b.series_index or 1,
                }
            }
        publications.append(pub)

    feed_title = (
        f"Recent Additions ({preset.upper()} Optimized)"
        if preset
        else "Recent Additions"
    )
    feed = {
        "metadata": {
            "title": feed_title,
            "modified": datetime.now(UTC).isoformat(),
        },
        "links": [
            {
                "rel": "self",
                "href": f"{base_url}{opds_prefix}/v2/recent.json",
                "type": OPDS_JSON_TYPE,
            }
        ],
        "publications": publications,
    }
    return Response(json.dumps(feed, indent=2), status=200, mimetype=OPDS_JSON_TYPE)


# ==========================================
# OPDS 1.2 XML Navigation & Acquisition Feeds
# ==========================================


@opds_bp.route("", methods=["GET"])
@opds_bp.route("/", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/", methods=["GET"])
@opds_auth_required
def root_catalog(preset: str | None = None):
    """Serve the OPDS 1.2 navigation/root catalog feed."""
    now_iso = datetime.now(UTC).isoformat()
    opds_prefix = f"/opds/{preset}" if preset else "/opds"
    return (
        render_template(
            "opds/root.xml.jinja",
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            opds_prefix=opds_prefix,
            preset=preset,
        ),
        200,
        {"Content-Type": OPDS_NAV_TYPE},
    )


@opds_bp.route("/recent", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/recent", methods=["GET"])
@opds_auth_required
def recent_feed(preset: str | None = None):
    """Serve an OPDS 1.2 acquisition feed of recently added items."""
    page = request.args.get("page", 1, type=int)
    per_page = 30
    query = (
        select(MediaItem)
        .options(selectinload(MediaItem.authors), selectinload(MediaItem.tags))
        .where(opds_readable_filter())
        .order_by(MediaItem.created_at.desc())
    )
    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)

    now_iso = datetime.now(UTC).isoformat()
    opds_prefix = f"/opds/{preset}" if preset else "/opds"
    return (
        render_template(
            "opds/feed.xml.jinja",
            feed_id=f"urn:aarkib:feed:recent{f':{preset}' if preset else ''}",
            feed_title="Recent Additions",
            feed_subtitle="Recently added books in Aarkib library",
            books=pagination.items,
            pagination=pagination,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            opds_prefix=opds_prefix,
            self_url=request.url,
            preset=preset,
        ),
        200,
        {"Content-Type": OPDS_ACQ_TYPE},
    )


@opds_bp.route("/authors", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/authors", methods=["GET"])
@opds_auth_required
def authors_index(preset: str | None = None):
    """Serve an OPDS 1.2 navigation feed of all authors."""
    authors = db.session.scalars(select(Author).order_by(Author.name.asc())).all()
    now_iso = datetime.now(UTC).isoformat()
    opds_prefix = f"/opds/{preset}" if preset else "/opds"
    return (
        render_template(
            "opds/authors.xml.jinja",
            authors=authors,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            opds_prefix=opds_prefix,
            preset=preset,
        ),
        200,
        {"Content-Type": OPDS_NAV_TYPE},
    )


@opds_bp.route("/authors/<int:author_id>", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/authors/<int:author_id>", methods=["GET"])
@opds_auth_required
def author_books(author_id: int, preset: str | None = None):
    """Serve an OPDS 1.2 acquisition feed of a single author's items."""
    author = db.session.get(Author, author_id)
    if not author:
        return Response("Author not found", 404)

    page = request.args.get("page", 1, type=int)
    query = (
        select(MediaItem)
        .options(selectinload(MediaItem.authors), selectinload(MediaItem.tags))
        .filter(MediaItem.authors.any(Author.id == author_id), opds_readable_filter())
        .order_by(MediaItem.title.asc())
    )
    pagination = db.paginate(query, page=page, per_page=30, error_out=False)
    now_iso = datetime.now(UTC).isoformat()
    opds_prefix = f"/opds/{preset}" if preset else "/opds"

    return (
        render_template(
            "opds/feed.xml.jinja",
            feed_id=f"urn:aarkib:author:{author.id}{f':{preset}' if preset else ''}",
            feed_title=f"Books by {author.name}",
            books=pagination.items,
            pagination=pagination,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            opds_prefix=opds_prefix,
            self_url=request.url,
            preset=preset,
        ),
        200,
        {"Content-Type": OPDS_ACQ_TYPE},
    )


@opds_bp.route("/series", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/series", methods=["GET"])
@opds_auth_required
def series_index(preset: str | None = None):
    """Serve an OPDS 1.2 navigation feed of all series/collections."""
    series_list = db.session.scalars(select(Series).order_by(Series.name.asc())).all()
    now_iso = datetime.now(UTC).isoformat()
    opds_prefix = f"/opds/{preset}" if preset else "/opds"
    return (
        render_template(
            "opds/series.xml.jinja",
            series_list=series_list,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            opds_prefix=opds_prefix,
            preset=preset,
        ),
        200,
        {"Content-Type": OPDS_NAV_TYPE},
    )


@opds_bp.route("/series/<int:series_id>", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/series/<int:series_id>", methods=["GET"])
@opds_auth_required
def series_books(series_id: int, preset: str | None = None):
    """Serve an OPDS 1.2 acquisition feed of a single series' items."""
    series_obj = db.session.get(Series, series_id)
    if not series_obj:
        return Response("Series not found", 404)

    page = request.args.get("page", 1, type=int)
    query = (
        select(MediaItem)
        .options(selectinload(MediaItem.authors), selectinload(MediaItem.tags))
        .filter(MediaItem.series_id == series_id, opds_readable_filter())
        .order_by(MediaItem.series_index.asc(), MediaItem.title.asc())
    )
    pagination = db.paginate(query, page=page, per_page=30, error_out=False)
    now_iso = datetime.now(UTC).isoformat()
    opds_prefix = f"/opds/{preset}" if preset else "/opds"

    return (
        render_template(
            "opds/feed.xml.jinja",
            feed_id=f"urn:aarkib:series:{series_obj.id}{f':{preset}' if preset else ''}",
            feed_title=f"Series: {series_obj.name}",
            books=pagination.items,
            pagination=pagination,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            opds_prefix=opds_prefix,
            self_url=request.url,
            preset=preset,
        ),
        200,
        {"Content-Type": OPDS_ACQ_TYPE},
    )


@opds_bp.route("/tags", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/tags", methods=["GET"])
@opds_auth_required
def tags_index(preset: str | None = None):
    """Serve an OPDS 1.2 navigation feed of all tags/categories."""
    tags = db.session.scalars(select(Tag).order_by(Tag.name.asc())).all()
    now_iso = datetime.now(UTC).isoformat()
    opds_prefix = f"/opds/{preset}" if preset else "/opds"
    return (
        render_template(
            "opds/tags.xml.jinja",
            tags=tags,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            opds_prefix=opds_prefix,
            preset=preset,
        ),
        200,
        {"Content-Type": OPDS_NAV_TYPE},
    )


@opds_bp.route("/tags/<int:tag_id>", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/tags/<int:tag_id>", methods=["GET"])
@opds_auth_required
def tag_books(tag_id: int, preset: str | None = None):
    """Serve an OPDS 1.2 acquisition feed of a single tag's items."""
    tag_obj = db.session.get(Tag, tag_id)
    if not tag_obj:
        return Response("Tag not found", 404)

    page = request.args.get("page", 1, type=int)
    query = (
        select(MediaItem)
        .options(selectinload(MediaItem.authors), selectinload(MediaItem.tags))
        .filter(MediaItem.tags.any(Tag.id == tag_id), opds_readable_filter())
        .order_by(MediaItem.title.asc())
    )
    pagination = db.paginate(query, page=page, per_page=30, error_out=False)
    now_iso = datetime.now(UTC).isoformat()
    opds_prefix = f"/opds/{preset}" if preset else "/opds"

    return (
        render_template(
            "opds/feed.xml.jinja",
            feed_id=f"urn:aarkib:tag:{tag_obj.id}{f':{preset}' if preset else ''}",
            feed_title=f"Tag: {tag_obj.name}",
            books=pagination.items,
            pagination=pagination,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            opds_prefix=opds_prefix,
            self_url=request.url,
            preset=preset,
        ),
        200,
        {"Content-Type": OPDS_ACQ_TYPE},
    )


@opds_bp.route("/search/opensearch.xml", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/search/opensearch.xml", methods=["GET"])
def opensearch_description(preset: str | None = None):
    """Serve the OpenSearch description document for OPDS search."""
    opds_prefix = f"/opds/{preset}" if preset else "/opds"
    return (
        render_template(
            "opds/opensearch.xml.jinja",
            base_url=request.host_url.rstrip("/"),
            opds_prefix=opds_prefix,
            preset=preset,
        ),
        200,
        {"Content-Type": OPENSEARCH_TYPE},
    )


@opds_bp.route("/search", methods=["GET"])
@opds_bp.route(f"/{PRESET_RULE}/search", methods=["GET"])
@opds_auth_required
def search_feed(preset: str | None = None):
    """Serve an OPDS 1.2 acquisition feed of search results (q) across the catalog."""
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 30

    query = (
        select(MediaItem)
        .options(selectinload(MediaItem.authors), selectinload(MediaItem.tags))
        .where(opds_readable_filter())
    )
    relevance_order = False
    if q:
        from aarkib.services.search import search_media_ids

        matching_ids = search_media_ids(q, limit=1000)
        if matching_ids:
            query = query.filter(MediaItem.id.in_(matching_ids))
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
                MediaItem.authors.any(Author.name.ilike(f"%{q}%")),
                MediaItem.tags.any(Tag.name.ilike(f"%{q}%")),
                MediaItem.series.has(Series.name.ilike(f"%{q}%")),
            )
            query = query.filter(search_filter)

    if not relevance_order:
        query = query.order_by(MediaItem.title.asc())
    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
    now_iso = datetime.now(UTC).isoformat()
    opds_prefix = f"/opds/{preset}" if preset else "/opds"

    return (
        render_template(
            "opds/feed.xml.jinja",
            feed_id=f"urn:aarkib:search{f':{preset}' if preset else ''}",
            feed_title=f"Search: {q}" if q else "Search Catalog",
            books=pagination.items,
            pagination=pagination,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            opds_prefix=opds_prefix,
            self_url=request.url,
            preset=preset,
        ),
        200,
        {"Content-Type": OPDS_ACQ_TYPE},
    )
