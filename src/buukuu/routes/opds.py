from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, Response, current_app, render_template, request
from flask_login import current_user
from sqlalchemy import or_, select

from buukuu.extensions import db
from buukuu.models import Author, Book, Series, Tag, User, UserProgress

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
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if current_app.config.get("AUTH_REQUIRED", False):
            user = get_opds_user()
            if not user:
                return Response(
                    "Authentication required for Buukuu OPDS Catalog",
                    401,
                    {"WWW-Authenticate": 'Basic realm="Buukuu OPDS"'},
                )
        return f(*args, **kwargs)

    wrapper.__name__ = f.__name__
    return wrapper


def make_opds_auth_document() -> dict[str, Any]:
    """Generates an OPDS Authentication Document compliant with OPDS 2.0 / Progression 1.0."""
    base_url = request.host_url.rstrip("/")
    return {
        "id": f"{base_url}/opds/authentication.json",
        "title": "Buukuu Authentication",
        "description": "Please provide your Buukuu credentials to access your library and synchronize reading progress.",
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
        f"urn:buukuu:user:{user.id}" if user else f"urn:buukuu:progress:{progress.id}"
    )
    device_name = progress.device_name or "Buukuu Reader"

    doc: dict[str, Any] = {
        "modified": last_read.isoformat(),
        "device": {"id": device_id, "name": device_name},
        "progression": round(progression_val, 7),
    }

    if progress.chapter_title:
        doc["title"] = progress.chapter_title

    if progress.references_json:
        try:
            refs = json.loads(progress.references_json)
            if isinstance(refs, list) and refs:
                doc["references"] = refs
        except Exception:
            if progress.progress_location and progress.progress_location not in (
                "0",
                "completed",
            ):
                doc["references"] = [progress.progress_location]
    elif progress.progress_location and progress.progress_location not in (
        "0",
        "completed",
    ):
        doc["references"] = [progress.progress_location]

    return doc


# ==========================================
# OPDS Progression 1.0 Endpoints
# ==========================================


@opds_bp.route("/authentication.json", methods=["GET"])
def opds_authentication_document():
    """Returns the OPDS Authentication Document."""
    doc = make_opds_auth_document()
    return Response(json.dumps(doc, indent=2), status=200, mimetype=OPDS_AUTH_TYPE)


@opds_bp.route("/books/<int:book_id>/progression", methods=["GET", "PUT", "POST"])
@opds_bp.route("/v2/books/<int:book_id>/progression", methods=["GET", "PUT", "POST"])
def opds_book_progression(book_id: int):
    """OPDS Progression 1.0 Fetch and Update endpoint.

    GET: Returns the last-known progression document for this publication.
    PUT/POST: Updates the last-known progression document for this publication.
    """
    user = get_opds_user()
    if current_app.config.get("AUTH_REQUIRED", False) and not user:
        auth_doc = make_opds_auth_document()
        return Response(
            json.dumps(auth_doc),
            status=401,
            mimetype=OPDS_AUTH_TYPE,
            headers={"WWW-Authenticate": 'Basic realm="Buukuu OPDS"'},
        )

    book = db.session.get(Book, book_id)
    if not book:
        problem = {
            "type": "https://registry.opds.io/error#publication-not-found",
            "title": "Publication not found in Buukuu catalog.",
        }
        return Response(json.dumps(problem), status=404, mimetype=PROBLEM_JSON_TYPE)

    user_id = user.id if user else None

    # --- GET: Fetch Progression ---
    if request.method == "GET":
        progress = db.session.scalar(
            select(UserProgress).where(
                UserProgress.book_id == book_id,
                UserProgress.user_id == user_id,
            )
        )
        if not progress or (
            progress.percentage == 0 and progress.progress_location in ("0", "")
        ):
            # When no progression recorded yet, return empty object with 200 OK
            return Response("{}", status=200, mimetype=OPDS_PROGRESSION_TYPE)

        doc = format_progression_document(progress, user)
        return Response(
            json.dumps(doc, indent=2), status=200, mimetype=OPDS_PROGRESSION_TYPE
        )

    # --- PUT / POST: Update Progression ---
    payload = request.get_json(silent=True)
    if not payload or not isinstance(payload, dict):
        problem = {
            "type": "https://registry.opds.io/error#progression-invalid-payload",
            "title": "Progression could not be updated due to an invalid payload.",
        }
        return Response(json.dumps(problem), status=400, mimetype=PROBLEM_JSON_TYPE)

    if "progression" not in payload or "device" not in payload:
        problem = {
            "type": "https://registry.opds.io/error#progression-invalid-payload",
            "title": "Missing required fields: 'progression' and 'device' are mandatory.",
        }
        return Response(json.dumps(problem), status=400, mimetype=PROBLEM_JSON_TYPE)

    try:
        prog_raw = float(payload["progression"])
        # Support both 0.0-1.0 float and 0-100 percentage
        if prog_raw <= 1.0 and prog_raw >= 0.0:
            percentage = prog_raw * 100.0
        else:
            percentage = max(0.0, min(100.0, prog_raw))
    except ValueError, TypeError:
        problem = {
            "type": "https://registry.opds.io/error#progression-invalid-payload",
            "title": "Invalid 'progression' value.",
        }
        return Response(json.dumps(problem), status=400, mimetype=PROBLEM_JSON_TYPE)

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
        return Response(json.dumps(problem), status=400, mimetype=PROBLEM_JSON_TYPE)

    device_id = str(device_info.get("id"))
    device_name = str(device_info.get("name"))
    chapter_title = payload.get("title")

    refs = payload.get("references")
    references_json = json.dumps(refs) if isinstance(refs, list) and refs else None
    primary_location = refs[0] if isinstance(refs, list) and refs else str(percentage)

    # Parse modified timestamp
    modified_str = payload.get("modified")
    modified_dt = datetime.now(UTC)
    if modified_str:
        try:
            # Handle ISO formats with or without Z / offset
            if modified_str.endswith("Z"):
                modified_str = modified_str[:-1] + "+00:00"
            parsed_dt = datetime.fromisoformat(modified_str)
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=UTC)
            modified_dt = parsed_dt
        except Exception:
            pass

    progress = db.session.scalar(
        select(UserProgress).where(
            UserProgress.book_id == book_id,
            UserProgress.user_id == user_id,
        )
    )

    # Check for conflict if an existing progression is strictly newer
    if progress and progress.last_read_at:
        existing_dt = progress.last_read_at
        if existing_dt.tzinfo is None:
            existing_dt = existing_dt.replace(tzinfo=UTC)
        if existing_dt > modified_dt:
            problem = {
                "type": "https://registry.opds.io/error#progression-date",
                "title": "A more recent progression point is already available.",
            }
            return Response(json.dumps(problem), status=409, mimetype=PROBLEM_JSON_TYPE)

    is_created = False
    if not progress:
        progress = UserProgress(user_id=user_id, book_id=book_id)
        db.session.add(progress)
        is_created = True

    progress.percentage = percentage
    progress.progress_location = primary_location
    progress.is_completed = percentage >= 99.5
    progress.device_id = device_id
    progress.device_name = device_name
    progress.chapter_title = str(chapter_title) if chapter_title else None
    progress.references_json = references_json
    progress.last_read_at = modified_dt

    db.session.commit()

    doc = format_progression_document(progress, user)
    return Response(
        json.dumps(doc, indent=2),
        status=201 if is_created else 200,
        mimetype=OPDS_PROGRESSION_TYPE,
    )


# ==========================================
# OPDS 2.0 JSON Catalog & Feeds
# ==========================================


@opds_bp.route("/v2/catalog.json", methods=["GET"])
@opds_auth_required
def opds2_catalog():
    """Returns OPDS 2.0 Navigation Catalog."""
    base_url = request.host_url.rstrip("/")
    catalog = {
        "metadata": {
            "title": "Buukuu OPDS 2.0 Catalog",
            "modified": datetime.now(UTC).isoformat(),
        },
        "links": [
            {
                "rel": "self",
                "href": f"{base_url}/opds/v2/catalog.json",
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
                "href": f"{base_url}/opds/v2/recent.json",
                "type": OPDS_JSON_TYPE,
            }
        ],
    }
    return Response(json.dumps(catalog, indent=2), status=200, mimetype=OPDS_JSON_TYPE)


@opds_bp.route("/v2/recent.json", methods=["GET"])
@opds_auth_required
def opds2_recent():
    """Returns OPDS 2.0 Recent Publications Feed with Progression 1.0 links."""
    base_url = request.host_url.rstrip("/")
    books = db.session.scalars(
        select(Book).order_by(Book.created_at.desc()).limit(50)
    ).all()

    publications = []
    for b in books:
        pub: dict[str, Any] = {
            "metadata": {
                "@type": "http://schema.org/Book",
                "title": b.title,
                "identifier": f"urn:buukuu:book:{b.id}",
                "modified": (b.updated_at or datetime.now(UTC)).isoformat(),
                "author": [{"name": a.name} for a in b.authors]
                if b.authors
                else [{"name": "Unknown Author"}],
            },
            "links": [
                {
                    "rel": "http://opds-spec.org/image",
                    "href": f"{base_url}/api/books/{b.id}/cover",
                    "type": "image/webp",
                },
                {
                    "rel": "http://opds-spec.org/acquisition",
                    "href": f"{base_url}/api/books/{b.id}/download",
                    "type": "application/epub+zip"
                    if b.file_format == "epub"
                    else "application/vnd.comicbook+zip",
                },
                {
                    "rel": "http://opds-spec.org/progression",
                    "href": f"{base_url}/opds/books/{b.id}/progression",
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

    feed = {
        "metadata": {
            "title": "Recent Additions",
            "modified": datetime.now(UTC).isoformat(),
        },
        "links": [
            {
                "rel": "self",
                "href": f"{base_url}/opds/v2/recent.json",
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
@opds_auth_required
def root_catalog():
    now_iso = datetime.now(UTC).isoformat()
    return (
        render_template(
            "opds/root.xml.jinja",
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
        ),
        200,
        {"Content-Type": OPDS_NAV_TYPE},
    )


@opds_bp.route("/recent", methods=["GET"])
@opds_auth_required
def recent_feed():
    page = request.args.get("page", 1, type=int)
    per_page = 30
    query = select(Book).order_by(Book.created_at.desc())
    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)

    now_iso = datetime.now(UTC).isoformat()
    return (
        render_template(
            "opds/feed.xml.jinja",
            feed_id="urn:buukuu:feed:recent",
            feed_title="Recent Additions",
            feed_subtitle="Recently added books in Buukuu library",
            books=pagination.items,
            pagination=pagination,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            self_url=request.url,
        ),
        200,
        {"Content-Type": OPDS_ACQ_TYPE},
    )


@opds_bp.route("/authors", methods=["GET"])
@opds_auth_required
def authors_index():
    authors = db.session.scalars(select(Author).order_by(Author.name.asc())).all()
    now_iso = datetime.now(UTC).isoformat()
    return (
        render_template(
            "opds/authors.xml.jinja",
            authors=authors,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
        ),
        200,
        {"Content-Type": OPDS_NAV_TYPE},
    )


@opds_bp.route("/authors/<int:author_id>", methods=["GET"])
@opds_auth_required
def author_books(author_id: int):
    author = db.session.get(Author, author_id)
    if not author:
        return Response("Author not found", 404)

    page = request.args.get("page", 1, type=int)
    query = (
        select(Book)
        .filter(Book.authors.any(Author.id == author_id))
        .order_by(Book.title.asc())
    )
    pagination = db.paginate(query, page=page, per_page=30, error_out=False)
    now_iso = datetime.now(UTC).isoformat()

    return (
        render_template(
            "opds/feed.xml.jinja",
            feed_id=f"urn:buukuu:author:{author.id}",
            feed_title=f"Books by {author.name}",
            books=pagination.items,
            pagination=pagination,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            self_url=request.url,
        ),
        200,
        {"Content-Type": OPDS_ACQ_TYPE},
    )


@opds_bp.route("/series", methods=["GET"])
@opds_auth_required
def series_index():
    series_list = db.session.scalars(select(Series).order_by(Series.name.asc())).all()
    now_iso = datetime.now(UTC).isoformat()
    return (
        render_template(
            "opds/series.xml.jinja",
            series_list=series_list,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
        ),
        200,
        {"Content-Type": OPDS_NAV_TYPE},
    )


@opds_bp.route("/series/<int:series_id>", methods=["GET"])
@opds_auth_required
def series_books(series_id: int):
    series_obj = db.session.get(Series, series_id)
    if not series_obj:
        return Response("Series not found", 404)

    page = request.args.get("page", 1, type=int)
    query = (
        select(Book)
        .filter(Book.series_id == series_id)
        .order_by(Book.series_index.asc(), Book.title.asc())
    )
    pagination = db.paginate(query, page=page, per_page=30, error_out=False)
    now_iso = datetime.now(UTC).isoformat()

    return (
        render_template(
            "opds/feed.xml.jinja",
            feed_id=f"urn:buukuu:series:{series_obj.id}",
            feed_title=f"Series: {series_obj.name}",
            books=pagination.items,
            pagination=pagination,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            self_url=request.url,
        ),
        200,
        {"Content-Type": OPDS_ACQ_TYPE},
    )


@opds_bp.route("/tags", methods=["GET"])
@opds_auth_required
def tags_index():
    tags = db.session.scalars(select(Tag).order_by(Tag.name.asc())).all()
    now_iso = datetime.now(UTC).isoformat()
    return (
        render_template(
            "opds/tags.xml.jinja",
            tags=tags,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
        ),
        200,
        {"Content-Type": OPDS_NAV_TYPE},
    )


@opds_bp.route("/tags/<int:tag_id>", methods=["GET"])
@opds_auth_required
def tag_books(tag_id: int):
    tag_obj = db.session.get(Tag, tag_id)
    if not tag_obj:
        return Response("Tag not found", 404)

    page = request.args.get("page", 1, type=int)
    query = (
        select(Book).filter(Book.tags.any(Tag.id == tag_id)).order_by(Book.title.asc())
    )
    pagination = db.paginate(query, page=page, per_page=30, error_out=False)
    now_iso = datetime.now(UTC).isoformat()

    return (
        render_template(
            "opds/feed.xml.jinja",
            feed_id=f"urn:buukuu:tag:{tag_obj.id}",
            feed_title=f"Tag: {tag_obj.name}",
            books=pagination.items,
            pagination=pagination,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            self_url=request.url,
        ),
        200,
        {"Content-Type": OPDS_ACQ_TYPE},
    )


@opds_bp.route("/search/opensearch.xml", methods=["GET"])
def opensearch_description():
    return (
        render_template(
            "opds/opensearch.xml.jinja",
            base_url=request.host_url.rstrip("/"),
        ),
        200,
        {"Content-Type": OPENSEARCH_TYPE},
    )


@opds_bp.route("/search", methods=["GET"])
@opds_auth_required
def search_feed():
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 30

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

    query = query.order_by(Book.title.asc())
    pagination = db.paginate(query, page=page, per_page=per_page, error_out=False)
    now_iso = datetime.now(UTC).isoformat()

    return (
        render_template(
            "opds/feed.xml.jinja",
            feed_id="urn:buukuu:search",
            feed_title=f"Search: {q}" if q else "Search Catalog",
            books=pagination.items,
            pagination=pagination,
            now_iso=now_iso,
            base_url=request.host_url.rstrip("/"),
            self_url=request.url,
        ),
        200,
        {"Content-Type": OPDS_ACQ_TYPE},
    )
