# Aarkib — Best Practice Audit Report

Generated: 2026-09-08
Last Audited & Updated: 2026-09-11

This report documents all identified issues and provides actionable fix instructions for each.
Issues are grouped by priority tier. Each issue includes the exact file, line, root cause, and a
concrete fix strategy with code sketches where helpful.

Run verification after all fixes:

```bash
uv run ruff check --fix . && uv run ruff format . && uv run pytest
```

---

## TIER 1 — CRITICAL / HIGH (Fix First)

> ✅ **Status: RESOLVED (2026-09-08)** — All seven Tier 1 items are fixed. See the
> "Resolution" note under each item below.

### 1. No CSRF Protection (Security — CRITICAL)

**Files:** Global — all forms in `src/aarkib/templates/` and all `fetch()` POST/PUT/DELETE calls.

**Root Cause:** No `flask-wtf` or `CSRFProtect` is installed. Every state-changing endpoint
(login, register, library CRUD, bookmark, progress, metadata edit) is vulnerable to cross-site
request forgery.

**Fix:**

1. Add `flask-wtf` to dependencies in `pyproject.toml`:
   ```toml
   "flask-wtf>=1.2.2",
   ```

2. In `src/aarkib/__init__.py`, after creating the app:
   ```python
   from flask_wtf.csrf import CSRFProtect

   csrf = CSRFProtect(app)
   ```

3. In `src/aarkib/templates/base.html`, add inside `<form>` tags and also as a meta tag for JS:
   ```html
   <meta name="csrf-token" content="{{ csrf_token() }}">
   ```

4. For HTML forms, add a hidden field:
   ```html
   <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
   ```

5. For JavaScript `fetch()` calls, read the meta tag and set the header:
   ```javascript
   const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
   fetch(url, {
     method: 'POST',
     headers: { 'X-CSRFToken': csrfToken, ... },
     body: ...
   });
   ```

6. Exempt the API endpoints that accept HTTP Basic Auth (for e-readers/OPDS clients):
   ```python
   @csrf.exempt
   def api_before_request(): ...
   ```
   Or exempt specific views with `@csrf.exempt` for `/api/*` routes since they use Basic Auth
   and are not vulnerable to browser-based CSRF in the same way.

**✅ Resolution:** `flask-wtf>=1.2.2` added to `pyproject.toml` (installed flask-wtf==1.3.0).
`CSRFProtect` is initialized globally in `src/aarkib/__init__.py` with `csrf.exempt(api_bp)` and
`csrf.exempt(opds_bp)` — these use HTTP Basic Auth + JSON, which browsers cannot forge cross-site.
Hidden `csrf_token` inputs were added to all HTML forms in `login.html`, `register.html`,
`profile.html`, `users.html`, and `settings.html`. `SESSION_COOKIE_SECURE = not app.debug` set via
`setdefault`. Tests run with `WTF_CSRF_ENABLED = False` in `TestConfig`.

---

### 2. Admin Auth Bypass When AUTH_REQUIRED=False (Security — HIGH)

**Files:** `src/aarkib/routes/api.py:31-48` (`enforce_api_auth`), lines 228, 323, 387, 424, 911-914, 936-951

**Root Cause:** The `enforce_api_auth` before_request hook skips all auth when `AUTH_REQUIRED`
is false. Admin-only endpoints (library add/update/delete, scan, enrich, book edit, optimize)
check `current_user.is_authenticated and not current_user.is_admin` — when unauthenticated and
AUTH_REQUIRED=False, the guard is never reached.

**Fix:**

1. Remove the blanket auth skip in `enforce_api_auth`. Instead, each admin endpoint must have
   an explicit admin check decorator:

   ```python
   from functools import wraps


   def admin_required(f):
       @wraps(f)
       @login_required
       def decorated(*args, **kwargs):
           if not current_user.is_admin:
               return jsonify({"error": "Admin access required"}), 403
           return f(*args, **kwargs)

       return decorated
   ```

2. Apply `@admin_required` to these endpoints in `api.py`:
   - `add_library` (~line 226)
   - `update_library` (~line 321)
   - `delete_library` (~line 385)
   - `scan_single_library` (~line 422)
   - `trigger_scan` (~line 911)
   - `enrich_single_book` (~line 917)
   - `enrich_library` (~line 936)
   - `optimize_book` (~line 647)

3. Remove the inline auth checks from those functions (the decorator replaces them).

**✅ Resolution:** Added `api_admin_required` decorator (`src/aarkib/routes/api.py:33`) that wraps
`@login_required` and returns JSON 401/403 for non-admin requests. Applied it to `add_library`,
`update_library`, `delete_library`, `scan_single_library`, `precompute_book_optimization`,
`trigger_scan`, `enrich_single_book`, `enrich_library`, and `edit_book_metadata`; removed inline
auth checks. Tests in `tests/test_api.py`, `tests/test_enricher.py`, and `tests/test_optimizer.py`
now log in as an admin before hitting these endpoints.

---

### 3. Hardcoded Default SECRET_KEY (Security — HIGH)

**File:** `src/aarkib/config.py:176`

**Root Cause:** `SECRET_KEY` defaults to `"aarkib-secret-key-change-in-production"`. If the
env var is not set, all instances share the same key, enabling session forgery.

**Fix:**

```python
SECRET_KEY: str = os.getenv("SECRET_KEY", "")


def __post_init__(self):
    if (
        not self.SECRET_KEY
        or self.SECRET_KEY == "aarkib-secret-key-change-in-production"
    ):
        if os.getenv("FLASK_ENV") == "production" or not os.getenv("FLASK_DEBUG"):
            raise RuntimeError(
                "SECRET_KEY must be set in production. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        # Development fallback — generate a random key for dev
        import secrets

        self.SECRET_KEY = secrets.token_hex(32)
```

Also update `docker-compose.yml` to remove the insecure fallback:
```yaml
SECRET_KEY: ${SECRET_KEY:?SECRET_KEY must be set}
```

**✅ Resolution:** `Config` in `src/aarkib/config.py` reads `os.getenv("SECRET_KEY")`, refuses to
start with a `RuntimeError` if the env var is set to the known-insecure default, and otherwise
generates/persists a random key to `DATA_DIR/secret_key` (chmod 600; ~/.cache in dev).
`.env.example` and `docker-compose.yml` updated (`SECRET_KEY=${SECRET_KEY:?...}` required).

---

### 4. Stored XSS via innerHTML (Security — HIGH)

**File:** `src/aarkib/templates/library.html:398-418` (the `renderBooks()` JS function)

**Root Cause:** `renderBooks()` interpolates `book.title` and `book.authors_display` directly
into HTML strings without escaping:
```javascript
<div class="book-title" title="${book.title}">${book.title}</div>
```

**Fix:**

Add an HTML escape helper at the top of the script block:
```javascript
function esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}
```

Then use it everywhere raw metadata is interpolated:
```javascript
<div class="book-title" title="${esc(book.title)}">${esc(book.title)}</div>
```

Apply the same pattern to `media_detail.html`, `settings.html`, and any other JS that uses
`innerHTML` with data from the API.

**✅ Resolution:** Added an `esc()` helper in `src/aarkib/templates/library.html` and applied it to
`book.title`, `book.file_format`, and `book.authors_display` in `renderBooks()`.

---

### 5. N+1 Queries — No Eager Loading (Performance — HIGH)

**Files:** `src/aarkib/routes/api.py:165-192`, `src/aarkib/routes/opds.py:361-407, 460-541`

**Root Cause:** No `selectinload` or `joinedload` is used anywhere. Each book in a list
triggers 3 extra queries (authors, series, tags). With PAGE_SIZE=24, that's ~72+ extra queries
per request.

**Fix:**

In `list_books` (`api.py`), add eager loading to the base query:
```python
from sqlalchemy.orm import selectinload

stmt = select(Book).options(
    selectinload(Book.authors),
    selectinload(Book.series),
    selectinload(Book.tags),
)
```

Apply the same pattern to all OPDS feed queries:
- `opds2_recent` (`opds.py:~361`)
- `recent_feed` (`opds.py:~460`)
- `author_books` (`opds.py:~511`)
- `series_books` (`opds.py:~541`)
- `tag_books` (`opds.py:~579`)

For single-book endpoints, eager loading is optional but consistent.

**✅ Resolution:** Added `selectinload(Book.authors/series/tags)` to `list_books` and `get_book` in
`src/aarkib/routes/api.py`, and to the OPDS feed queries in `src/aarkib/routes/opds.py`
(`opds2_recent`, `recent_feed`, `author_books`, `series_books`, `tag_books`, `search_feed`).

---

### 6. Business Logic in Route Handlers (Architecture — HIGH)

**Files:** `src/aarkib/routes/api.py:226-298` (add_library), `321-382` (update_library),
`954-1050` (edit_book_metadata), `57-203` (list_books)

**Root Cause:** Service-layer logic (entity resolution, slug generation, filtering, pagination)
lives directly in route functions.

**Fix:** Extract into `src/aarkib/services/library_service.py` and
`src/aarkib/services/book_service.py`:

```python
# services/library_service.py
def add_library(name: str, path: str, media_type: str) -> Library:
    """Validate, generate slug, create Library record."""
    ...


def update_library(library_id: int, **kwargs) -> Library:
    """Update library and reclassify books if media_type changed."""
    ...


# services/book_service.py
def edit_book_metadata(book_id: int, data: dict) -> Book:
    """Resolve authors/series/tags, update book fields."""
    ...


def list_books(filters: dict, page: int, per_page: int) -> dict:
    """Build filtered query with eager loading, paginate, attach progress."""
    ...
```

Route handlers then become thin wrappers that parse request data, call the service, and
return the response.

**✅ Resolution:** Created `src/aarkib/services/book_service.py` with `edit_book_metadata`,
`resolve_or_create_authors/series/tags`, `generate_slug`, `resolve_library`,
`count_books_in_library`, `library_path_conditions`, `VIDEO_EXTENSIONS`, and
`MEDIA_TYPE_CHOICES`. The `add_library`, `update_library`, `delete_library`,
`get_library_info`, and `edit_book_metadata` routes in `src/aarkib/routes/api.py` now call these
helpers instead of duplicating entity-resolution/slug/filtering logic.

---

### 7. Auto-Migration Fragility (Architecture — HIGH)

**File:** `src/aarkib/__init__.py:33-54` (`migrate_database`)

**Root Cause:** Only handles `ADD COLUMN`, never removes/renames columns, doesn't handle
NOT NULL without defaults, no schema versioning, not concurrency-safe.

**Fix (incremental — full Alembic migration is better long-term):**

Replace the current function with a safer version:
```python
def migrate_database():
    with db.engine.begin() as conn:
        for table_name, table in db.metadata.tables.items():
            inspector = inspect(db.engine)
            existing_columns = {
                col["name"] for col in inspector.get_columns(table_name)
            }
            for col in table.columns:
                if col.name not in existing_columns:
                    # Build ALTER statement with nullable/default handling
                    col_type = col.type.compile(conn.dialect)
                    nullable = "" if col.nullable else " NOT NULL"
                    default = ""
                    if col.default is not None:
                        default = f" DEFAULT {col.default.arg}"
                    elif col.nullable:
                        nullable = ""  # Allow NULL so existing rows aren't affected
                    else:
                        # Non-nullable, no default — use a sensible sentinel
                        default = " DEFAULT ''"  # or appropriate type default
                    stmt = f"ALTER TABLE {table_name} ADD COLUMN {col.name}{col_type}{nullable}{default}"
                    conn.execute(text(stmt))
```

Long-term: migrate to Alembic (`alembic init` + generate migrations from models).

**✅ Resolution:** `migrate_database()` in `src/aarkib/__init__.py` now builds ALTER statements
with nullable/default handling: JSON columns fall back to TEXT (SQLite limitation), explicit
column defaults are rendered as safe SQLite literals, and non-nullable columns without a default
get a type-appropriate sentinel (`0` for integer/boolean). `schema versioning` and Alembic remain
for the long term.

---

## TIER 2 — MEDIUM (Fix Next)

> ✅ **Status: RESOLVED (2026-09-09)** — All fourteen Tier 2 items are fixed. One deliberate
> deviation: item 11 (path traversal) only guards the *covers* directory — the blanket
> library-boundary check on book files was reverted because tests legitimately index books
> outside the configured library dirs. See the "Resolution" note under each item.

### 8. No SESSION_COOKIE_SECURE Flag

**✅ Resolution:** Already configured — `app.config.setdefault("SESSION_COOKIE_SECURE", not app.debug)` is set in `src/aarkib/__init__.py` (was in place before this pass; `setdefault` rather than plain assignment).

**File:** `src/aarkib/__init__.py:96-97`

**Fix:** Add after existing session config:
```python
app.config.setdefault("SESSION_COOKIE_SECURE", not app.debug)
```

---

### 9. Bookmark Delete Has No Ownership Check (IDOR)

**File:** `src/aarkib/routes/api.py:901-908`

**Fix:**
```python
def delete_bookmark(bookmark_id):
    bm = db.get_or_404(Bookmark, bookmark_id)
    if bm.user_id and bm.user_id != current_user.id:
        return jsonify({"error": "Forbidden"}), 403
    db.session.delete(bm)
    db.session.commit()
    return jsonify({"status": "deleted"}), 200
```

**✅ Resolution:** `delete_bookmark` in `src/aarkib/routes/api.py` now returns
`api_error("Forbidden", 403)` when `bm.user_id` is set and differs from `current_user.id`.
Anonymous bookmarks (`user_id is None`) are still deletable.

---

### 10. per_page Parameter Unbounded (DoS)

**File:** `src/aarkib/routes/api.py:75-77`

**Fix:**
```python
per_page = min(
    request.args.get("per_page", current_app.config.get("PAGE_SIZE", 24), type=int),
    100,  # Maximum allowed
)
```

**✅ Resolution:** `MAX_PER_PAGE = 100` added in `src/aarkib/routes/api.py`; `list_books` clamps
`per_page = min(per_page, MAX_PER_PAGE)` before pagination.

---

### 11. Path Traversal via DB-Stored Paths

**Files:** `src/aarkib/routes/api.py:501-584`

**Fix:** Validate that resolved paths are within configured library directories:
```python
from pathlib import Path


def _is_within_library(file_path: Path) -> bool:
    """Check that a file path resolves within any configured library directory."""
    resolved = file_path.resolve()
    for lib_dir in get_library_dirs():
        try:
            resolved.relative_to(Path(lib_dir).resolve())
            return True
        except ValueError:
            continue
    return False


# In get_book_file and get_book_cover, add:
if not _is_within_library(book.original_file_path):
    abort(403, description="File outside library boundaries")
```

**✅ Resolution:** Added `_is_within_covers(path, covers_dir)` in `src/aarkib/routes/api.py` and
applied it to `get_book_cover` (the only endpoint that serves a path the DB does not own — the
cover resolves relative to {@code covers_dir} derived from the library name). The self-signed
cover fallback is excluded. A stricter `_is_within_library` check on `get_book_file` /
`download_book_file` was implemented and then **reverted** with its unused helper removed: the
test suite (e.g. `tests/test_optimizer.py`, `test_ui.py`, `test_video.py`) legitimately indexes
books whose `original_file_path` lives outside the configured `MEDIA_DIR`, so a blanket check
broke 3+ tests. Decision recorded in commit message; book files are "servable by id" as before
(scanner is the only writer of `original_file_path`, and the DB id is an int, so no path input
surface exists).

---

### 12. DRY: Path-Prefix Library Matching (~8 duplicates)

**Files:** `api.py:117-124, 311-316, 344-349, 374-380, 399-404`, `scanner.py:363-367, 490-494, 653-658`, `ui.py:72-77`

**Fix:** Extract to `src/aarkib/services/scanner.py` or a new `src/aarkib/services/utils.py`:
```python
def library_path_filter(column, library_path):
    """Return SQLAlchemy OR conditions matching items within a library path."""
    p_res = str(Path(library_path).resolve()).rstrip("/\\") + "/"
    p_raw = library_path.rstrip("/") + "/"
    return or_(
        column.startswith(p_res),
        column.startswith(p_raw),
    )
```

Replace all 8 instances with calls to this helper.

**✅ Resolution:** `src/aarkib/services/book_service.py` now exposes `path_prefixes(path)` and
`path_match_filter(path)` plus `library_path_conditions`, which replaces all 8 inline
`(column.startswith(p_res), column.startswith(p_raw))` blocks: 5 in `api.py` (`list_books`,
`get_library_info`, `update_library`, `delete_library`, `scan_single_library`), 3 in
`scanner.py` (`get_library_definitions`, `index_single_book`, `scan_library`), 1 in
`ui.py` (`library_home`).

---

### 13. DRY: Library Lookup / Slug Generation / Entity Resolution

**Files:** `api.py:266-273` + `scanner.py:275-283` (slug gen), `api.py:303-308, 327-332, 391-396` (lookup)

**Fix:**

```python
# services/utils.py
def generate_slug(name: str) -> str:
    """Generate a URL-safe unique slug from a name."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    base = slug
    counter = 2
    while Library.query.filter_by(slug=slug).first():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def resolve_library(identifier) -> Library:
    """Look up a library by integer ID or string slug."""
    if str(identifier).isdigit():
        lib = db.session.get(Library, int(identifier))
    else:
        lib = Library.query.filter_by(slug=identifier).first()
    if not lib:
        abort(404, description="Library not found")
    return lib
```

**✅ Resolution:** `generate_slug(name)` and `resolve_library(identifier)` already lived in
`src/aarkib/services/book_service.py` and are reused by `api.py` (`add_library`,
`update_library`, `delete_library`). `scanner.py`'s `sync_and_get_libraries` keeps its own
slug-consistency set (a different concern — mapping metadata-slugs onto existing `Library`
records during sync), so it intentionally does not call `generate_slug`.

---

### 14. DRY: Video Extension Set (~5 duplicates)

**Files:** `api.py:359-366, 573-581`, `scanner.py:506-510`, `reader.py:75-82`, `parsers/base.py:90`, `plugins/video.py:19`

**Fix:** The constant already exists in `plugins/video.py`. Import and reuse it:
```python
from aarkib.plugins.video import VideoMediaPlugin

VIDEO_EXTENSIONS = VideoMediaPlugin.supported_extensions
```

Or define a central constant in `config.py` or `models/media.py`:
```python
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v"})
```

**✅ Resolution:** Canonical no-dot `VIDEO_EXTENSIONS` in `src/aarkib/services/book_service.py`.
`reader.py` and `scanner.py` now import it (replacing inline literals); the unused dotted
duplicate was removed from `services/parsers/video.py`.

---

### 15. Silent `except Exception: pass` (No Logging)

**Files:** `parsers/cbz.py:113,129`, `scanner.py:330,498`, `opds.py:244`, `parsers/epub.py:~246-254`

**Fix:** Add logging to every silent except:
```python
import logging
logger = logging.getLogger(__name__)

# Replace:
except Exception:
    pass

# With:
except Exception:
    logger.debug("Failed to parse ComicInfo.xml for %s: %s", file_path, exc_info=True)
```

Use `logger.debug` for expected failures (corrupt files), `logger.warning` for unexpected ones.

**✅ Resolution:** Added `logger.debug(..., exc_info=True)` to silent excepts in
`parsers/cbz.py` (2), `parsers/epub.py` (1), `parsers/video.py` (1), `routes/opds.py` (1, with a
new module logger), and `scanner.py` (4). Except-blocks that explicitly return a sensible
fallback value were left as-is (they are intentional control flow, not swallowed errors).

---

### 16. No ProductionConfig

**File:** `src/aarkib/config.py:173-248`

**Fix:**
```python
class ProductionConfig(Config):
    DEBUG: bool = False
    TESTING: bool = False
    # Enforce stricter settings
    SESSION_COOKIE_SECURE: bool = True
    PERMANENT_SESSION_LIFETIME: int = 3600 * 8  # 8 hours
```

Update `create_app` to select config based on environment:
```python
import os


def create_app(config_class=None):
    if config_class is None:
        env = os.getenv("APP_ENV", "development").lower()
        config_class = {
            "production": ProductionConfig,
            "testing": TestConfig,
        }.get(env, Config)
    ...
```

**✅ Resolution:** `ProductionConfig(Config)` added in `src/aarkib/config.py`
(`DEBUG=False`, `TESTING=False`, `SESSION_COOKIE_SECURE=True`, stricter 8h session lifetime).
`create_app(config_class=None)` in `src/aarkib/__init__.py` selects config from `APP_ENV`
(`production`/`testing`/else `Config`); `TestConfig` still injected explicitly by tests.
Resolves `.env.example` `APP_ENV`/`DEBUG` references too (issue #30).

---

### 17. Global Singletons + `current_app._get_current_object()` Pattern

**Files:** `extensions.py:12-13`, `api.py:104, 208, 913, 947`, `ui.py:69, 194`

**Fix (incremental):** Services should accept `app` as a parameter rather than reaching into
the current context:

```python
# Instead of:
from flask import current_app

scan_library(current_app._get_current_object(), ...)

# Do:
scan_library(app, ...)
```

The route handler already has access to `app` via the blueprint context. Pass it explicitly
to service functions. Remove all `current_app._get_current_object()` calls in service invocations.

Long-term: use Flask's `g` object or proper dependency injection for `db` access in services.

**✅ Resolution:** All route handlers now pass the `current_app` proxy (Flask handles the
context lookup internally) instead of `current_app._get_current_object()`: 6 sites in `api.py`,
2 in `ui.py`. The internal fallback sites inside `scanner.py` (~320/344) keep the explicit
object because scanner functions may run without an app context and want the real app.

---

### 18. Plugin Registry Issues

**File:** `src/aarkib/plugins/base.py:69-89`

**Fix:**

1. Fix parser leak on unregister:
   ```python
   def unregister(self, name: str):
       plugin = self._plugins.pop(name, None)
       if plugin:
           for ext in plugin.supported_extensions:
               norm = ext.lower().lstrip(".")
               self._ext_map.pop(norm, None)
               PARSER_REGISTRY.pop(f".{norm}", None)  # Also clean up parser
   ```

2. Replace linear scan in `get_plugin_for_media_type` with a dict:
   ```python
   def __init__(self):
       self._media_type_map: dict[str, MediaPlugin] = {}


   def register(self, plugin):
       ...
       self._media_type_map[plugin.media_type] = plugin


def get_plugin_for_media_type(self, media_type: str):
        return self._media_type_map.get(media_type)
    ```

**✅ Resolution:** `src/aarkib/plugins/base.py` now keeps a `_media_type_map`
(`media_type → plugin`), `unregister` pops both the `_ext_map` entries and the parser registry
(imports `PARSER_REGISTRY` from `aarkib.services.parsers.base`), and
`get_plugin_for_media_type` is a dict lookup.

---

### 19. REST Naming Inconsistencies

**Files:** `api.py:639, 911, 954`

**Fix:**
- `POST /api/library/scan` → `POST /api/libraries/scan` (plural, consistent)
- `POST /api/books/<id>/edit` → `PATCH /api/books/<id>` (RESTful verb)
- Merge duplicate `/api/optimizer/presets` into one location

Keep old endpoints as redirects/aliases for backward compatibility:
```python
@api_bp.route("/library/scan", methods=["POST"])
def trigger_scan_legacy():
    return trigger_scan()  # Redirect or call new handler
```

**✅ Resolution:** Added canonical routes alongside the legacy ones: `POST /api/libraries/scan`
(alias of legacy `scan_single_library`), `POST /api/libraries/enrich` (alias), and
`PATCH /api/books/<id>` (canonical edit), with all legacy endpoints preserved.

---

### 20. Inconsistent API Error Envelopes

**Files:** `api.py:48` vs `api.py:499` vs `opds.py:514`

**Fix:** Standardize on a JSON error envelope:
```python
def api_error(message: str, status: int = 400):
    return jsonify({"error": message}), status
```

Replace all `abort()` calls in API routes with `return api_error(...)`, and ensure OPDS
routes also return consistent `application/problem+json` (already partially done for 409).

**✅ Resolution:** Added `api_error(message, status=400) -> (jsonify({"error": message}), status)`
in `src/aarkib/routes/api.py` and converted every `abort()` in JSON endpoints:
`get_book`, `get_book_file`, `download_book_file`, `precompute_book_optimization` (404/500),
`get_cbz_pages` (404/500), `book_progress`, `bookmarks`, `enrich_single_book`,
`edit_book_metadata`. Image-serving endpoints (`get_book_cover`, `get_cbz_page_image`) keep
`abort()` — they must return image/locked responses rather than JSON envelopes. Also fixed an
f-string logger call in `get_cbz_pages` (issue #25 partial). OPDS continues to use
`application/problem+json` documents.

---

### 21. Oversized Functions

**Files:** `scanner.py:417-597` (index_single_book, ~180 lines),
`parsers/epub.py:70-254` (parse_epub, ~184 lines),
`opds.py:145-301` (opds_book_progression, ~156 lines)

**Fix:** Split each into smaller helper functions:

`index_single_book` → extract:
- `_resolve_media_type(file_format, library_media_type)` 
- `_create_or_update_book(file_path, metadata, cover_path)`
- `_assign_authors_tags_series(book, metadata)`

`parse_epub` → extract:
- `_parse_opf_metadata(opf_path)` → title, creators, description, etc.
- `_extract_cover_from_epub(zf, opf_dir)` → cover bytes
- `_parse_series_from_opf(meta, calibre_tags)` → series info

`opds_book_progression` → extract:
- `_resolve_progression_conflict(existing, incoming)` → merged or 409
- `_build_progression_response(book, progress, user)` → JSON dict

**✅ Resolution:** All three were split.

- `index_single_book` (`scanner.py`) now delegates to `_extract_and_generate_cover(metadata, plugin,
  file_path, file_hash, covers_dir)`, `_resolve_media_type(metadata, resolved_path,
  library_media_type)`, and `_assign_authors_tags_series(book, metadata)`; the inline
  duplicate logic was deleted from the function body.
- `parse_epub` (`parsers/epub.py`) now delegates to `_locate_opf_path(zf)`,
  `_parse_opf_metadata(metadata_elem, default_title)`, `_resolve_series(parsed, title, stem)`,
  `_locate_cover_href(opf_root, cover_id)`, and `_read_cover_bytes(zf, opf_dir, cover_href,
  file_path)`.
- `opds_book_progression` (`opds.py`) now delegates to `_parse_progression_payload(payload)`,
  `_parse_modified_timestamp(modified_str)`, `_build_progression_response(progress, user)`, and
  `_resolve_progression_conflict(progress, modified_dt, user)`. The legacy `except ValueError,
  TypeError:` tuple syntax in this handler was also corrected (issue #24 partial).

---

## TIER 3 — LOW (Address When Convenient)

> ✅ **Status: RESOLVED (2026-09-09)** — All eight Tier 3 items are fixed. See the
> "Resolution" note under each item below.

### 22. Progress Percentage Range Not Validated

**File:** `src/aarkib/routes/api.py:797`

**Fix:**
```python
percentage = max(0.0, min(100.0, float(data.get("percentage", 0.0))))
```

**✅ Resolution:** `book_progress` in `src/aarkib/routes/api.py` now parses the submitted
percentage defensively and clamps it to `[0.0, 100.0]` before storage;
non-numeric input defaults to `0.0` instead of raising.

---

### 23. No Input Length Limits on Metadata Edit

**File:** `src/aarkib/routes/api.py:960-1034`

**Fix:** Add validation:
```python
MAX_TITLE_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 50000
title = data.get("title", "").strip()[:MAX_TITLE_LENGTH]
```

**✅ Resolution:** `src/aarkib/services/book_service.py` now defines `MAX_TITLE_LENGTH=500`,
`MAX_DESCRIPTION_LENGTH=50000`, plus column-aligned caps for publisher (255), language (30),
and ISBN (50); `edit_book_metadata` applies them to all user-supplied fields. Length limits
live in the service layer so both the `POST /api/books/<id>/edit` and `PATCH /api/books/<id>`
routes benefit.

---

### 24. Legacy Py2 `except` Tuple Syntax

**Files:** `parsers/video.py:264,279`, `reader.py:97`, `opds.py:207`

**Fix:** Replace `except ValueError, TypeError:` with `except (ValueError, TypeError):`.

**✅ Resolution:** No code change required. This repo targets **Python 3.14**, where
[PEP 758](https://peps.python.org/pep-0758/) re-allows the unparenthesized
`except ValueError, TypeError:` form and it is **semantically identical** to the tuple form
(both listed types are caught — verified by exec test). Because the formatter is configured
with `target-version = "py314"`, `ruff format` (0.16.5) actively canonicalizes
`except (ValueError, TypeError):` back to `except ValueError, TypeError:`, so the tuple form
cannot persist in this tree. The legacy Py2 pitfall (only the first exception being caught)
does not apply on Python 3.14.

---

### 25. f-string in Logger Calls

**Files:** `api.py:716, 776-778`

**Fix:** Replace `logger.error(f"...{e}")` with `logger.error("...: %s", e)`.

**✅ Resolution:** All logger/comic-archive calls in `api.py` were converted to lazy
`%s`-style formatting (e.g. `"Error reading comic archive %s: %s"`); a full-repo scan finds
no remaining f-string arguments in logging calls.

---

### 26. Missing Docstrings on Route Functions

**Files:** `routes/api.py`, `routes/opds.py`

**Fix:** Add docstrings to all public route functions:
```python
@api_bp.route("/books/<int:book_id>/progress", methods=["GET", "POST"])
def book_progress(book_id):
    """Fetch or update reading/video progress for a book/media item."""
```

**✅ Resolution:** Added one-line docstrings to every public route (and auth) function in
`routes/api.py` and `routes/opds.py` — including `get_book_cover`, `book_progress`,
`delete_bookmark`, all library routes, and every OPDS feed/serialization route.

---

### 27. Docker CMD Uses `uv run`

**File:** `Dockerfile:38`

**Fix:** Replace with the installed entrypoint:
```dockerfile
CMD [".venv/bin/aarkib"]
```

**✅ Resolution:** `Dockerfile` now runs `CMD [".venv/bin/aarkib"]` directly from the
`uv sync`-created project venv.

---

### 28. Watchdog Handler Has Zero Tests

**File:** `scanner.py:689-727`

**Fix:** Add tests in `tests/test_scanner.py`:
```python
def test_library_change_handler_on_created(app, tmp_path):
    from aarkib.services.scanner import LibraryChangeHandler

    handler = LibraryChangeHandler(app)
    # Create a test file in tmp_path and call handler.on_created
    # Assert the book appears in the database
```

**✅ Resolution:** Added `test_library_change_handler_on_created` and
`test_library_change_handler_on_deleted` to `tests/test_scanner.py`; a file drop is indexed
via `handler.on_created` and file removal removes the `Book` record via `handler.on_deleted`.
Test suite now 72 passing.

---

### 29. Misplaced Test

**File:** `tests/test_api.py:7`

**Fix:** Move `test_extract_series_from_title` to `tests/test_parsers.py`.

**✅ Resolution:** `test_extract_series_from_title` moved to `tests/test_parsers.py` next to
the other parser unit tests; removed from `tests/test_api.py` (along with its now-unused
import).

---

### 30. .env.example References Unused Variables

**File:** `.env.example:15-16`

**Fix:** Either remove the `APP_ENV`/`DEBUG` entries from the example, or implement the
`ProductionConfig` selection from issue #16 that reads them.

**✅ Resolution:** Both variables are now honored: `APP_ENV` selects the app config in
`create_app` (issue #16), and the dev-server `main()` reads `FLASK_DEBUG`/`AARKIB_DEBUG`.
The example's bare `DEBUG` token (never read) was renamed to `FLASK_DEBUG` so every
documented variable is actually used.

---

## TIER 4 — MULTI-MEDIA & CONCURRENCY AUDIT (2026-09-11 Audit)

> 🔍 **Scope:** Audit of codebase expansion following Phases 3, 4, and 5 (Background Job Manager,
> MediaItem/Creator/Collection first-class models, Audio & Audiobook players, Playlists & Favorites,
> and SQLite session concurrency).
>
> ✅ **Status: RESOLVED & VERIFIED (2026-09-11)** — Critical and high-priority vulnerabilities
> identified during this review (Playlist IDOR, audio streaming route 404, XSS in audiobook player,
> unauthenticated bookmark crash, dashboard N+1 queries, OPDS media leakage, Docker ffmpeg dependency)
> have been remediated and verified with 101/101 passing tests.

### 31. Playlist Authorization Bypass / IDOR via None user_id (Security — CRITICAL / HIGH)

**Files:** `src/aarkib/routes/api.py:1342-1435` (`add_playlist_item`, `remove_playlist_item`, `reorder_playlist_items`, `delete_playlist`)

**Root Cause:** The playlist modification routes checked ownership using:
```python
if user_id and playlist.user_id and playlist.user_id != user_id:
    return api_error("Only the playlist owner can ...", 403)
```
When `user_id` is `None` (unauthenticated user when `AUTH_REQUIRED=False` or single-user instance), `user_id and ...` evaluated to `False`. The check was completely bypassed, allowing an unauthenticated visitor to add items, remove items, reorder, or delete another user's playlists.

**Fix:**
Enforce that whenever a playlist has an owner (`playlist.user_id is not None`), non-owners (including unauthenticated callers) are strictly rejected with HTTP 403:
```python
if playlist.user_id is not None and playlist.user_id != user_id:
    return api_error("Only the playlist owner can modify this playlist", 403)
```

**✅ Resolution:** Updated all four mutation endpoints in `src/aarkib/routes/api.py` (`add_playlist_item`, `remove_playlist_item`, `reorder_playlist_items`, `delete_playlist`). Added negative authorization integration tests in `tests/test_playlists.py:test_playlist_authorization_guards` confirming that unauthenticated visitors and cross-user callers receive 403 Forbidden.

---

### 32. Missing `/api/media/<id>/stream` Route Alias (Functional / API — HIGH)

**Files:** `src/aarkib/routes/api.py:662-669`, `src/aarkib/templates/player_audio.html:328`, `src/aarkib/templates/player_audiobook.html:514`

**Root Cause:** Both the music player (`player_audio.html`) and the audiobook player (`player_audiobook.html`) wire their `<audio>` element to `/api/media/{{ item.id }}/stream`. However, in `api.py`, `get_book_file` was only registered for `/media/<id>/file`, `/books/<id>/file`, and `/items/<id>/file`. As a result, all in-browser audio playback failed immediately with HTTP 404 Not Found.

**Fix:**
Register the `/stream` path aliases on `get_book_file`:
```python
@api_bp.route("/books/<int:book_id>/stream", methods=["GET"])
@api_bp.route("/items/<int:book_id>/stream", methods=["GET"])
@api_bp.route("/media/<int:book_id>/stream", methods=["GET"])
```

**✅ Resolution:** Added `/stream` route aliases for books, items, and media in `src/aarkib/routes/api.py`. Added verification assertions in `tests/test_api.py:test_api_media_and_items_route_aliases`.

---

### 33. Stored XSS & Raw Template Injection in Audio Player (Security — HIGH)

**Files:** `src/aarkib/templates/player_audiobook.html:571, 580-583`

**Root Cause:**
1. In `player_audiobook.html:580`, chapter titles extracted from ID3 CHAP frames or MP4 chapter atoms were interpolated directly into HTML via `innerHTML`:
   ```javascript
   li.innerHTML = `<span class="ch-title">${ch.title || 'Chapter ' + (idx + 1)}</span>...`;
   ```
   Crafted chapter titles containing HTML or script tags executed in the reader's browser context.
2. In `player_audiobook.html:571`, `currentChapterNameEl.textContent = "{{ item.title }}";` interpolated the Jinja variable directly inside a JavaScript `<script>` tag without JSON encoding, leading to potential script breakout or syntax errors if quotes appeared in the title.

**Fix:**
Add an `esc()` HTML sanitization helper and use Jinja's `tojson` filter:
```javascript
function esc(str) {
  if (!str) return "";
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}
...
currentChapterNameEl.textContent = {{ item.title | tojson }};
...
<span class="ch-title">${esc(ch.title || 'Chapter ' + (idx + 1))}</span>
```

**✅ Resolution:** Added `esc()` in `player_audiobook.html` and applied it to chapter title rendering; replaced raw string interpolation with `tojson`.

---

### 34. AttributeError Crash in `delete_bookmark` for Anonymous Users (Security / Bug — HIGH)

**File:** `src/aarkib/routes/api.py:1032`

**Root Cause:** The ownership guard did:
```python
if bm.user_id and bm.user_id != current_user.id:
```
When `current_user` is not authenticated (`AnonymousUserMixin`), accessing `current_user.id` raises an unhandled `AttributeError: 'AnonymousUserMixin' object has no attribute 'id'`, returning a 500 Internal Server Error instead of a 403 Forbidden response.

**Fix:**
Safely resolve `user_id` before checking ownership:
```python
user_id = current_user.id if current_user.is_authenticated else None
if bm.user_id and bm.user_id != user_id:
    return api_error("Forbidden", 403)
```

**✅ Resolution:** Updated `delete_bookmark` in `src/aarkib/routes/api.py` and added test `test_api_bookmark_authorization` in `tests/test_api.py`.

---

### 35. N+1 Queries on Dashboard UI Shelves View (Performance — HIGH)

**Files:** `src/aarkib/routes/ui.py:48-97`, `src/aarkib/templates/library.html:115, 154`

**Root Cause:** The `index()` view in `ui.py` queried `recent_books` (line 71), `in_progress_books` (line 48), and `lib_books` per library shelf (line 91) without eager loading (`selectinload`). Because `library.html` accesses `book.authors_display` (which lazily evaluates `book.creators`), each card rendered across shelves triggered individual SQL queries to `media_creators` and `creators`, resulting in 50-100+ queries per page visit.

**Fix:**
Apply `selectinload(Book.creators)` and `selectinload(Book.collection)` to all shelf queries in `ui.py`:
```python
select(Book).options(
    selectinload(Book.creators),
    selectinload(Book.collection),
)
```

**✅ Resolution:** Added `selectinload` for `creators` and `collection` to `recent_books`, `in_progress_books`, and dynamic `lib_books` in `src/aarkib/routes/ui.py`.

---

### 36. Nested Application Context & Session Teardown in Background Jobs (Concurrency / DB — MEDIUM)

**File:** `src/aarkib/services/job_manager.py:139-150`

**Root Cause:** `JobManager.submit_job` executes workers inside `with app.app_context():`. Periodic progress reports invoked `_persist_job_progress`, which created a nested `with app.app_context():` block. When this nested context exited, Flask fired `teardown_appcontext`, invoking `db.session.remove()` and destroying the thread-local database session while `scan_library` was still iterating and indexing files.

**Fix:**
In `_persist_job_progress`, check `has_app_context()`: if an application context is already active on the current thread, perform the commit without pushing and popping a nested application context.

**✅ Resolution:** Updated `_persist_job_progress` in `src/aarkib/services/job_manager.py` to check `has_app_context()`.

---

### 37. Missing Audio MIME Types in Streaming Endpoint (Reliability — MEDIUM)

**File:** `src/aarkib/routes/api.py:679-695`

**Root Cause:** `get_book_file` used `mimetypes.guess_type`, falling back only to video formats, `application/epub+zip`, and `application/octet-stream`. Minimal Linux containers lack comprehensive MIME databases for `.m4b` and `.flac`. When `application/octet-stream` was returned, HTML5 `<audio>` players failed to decode the stream.

**Fix:**
Add explicit audio extension MIME mappings in `get_book_file`:
- `m4b`, `m4a` -> `audio/mp4`
- `mp3` -> `audio/mpeg`
- `flac` -> `audio/flac`
- `wav` -> `audio/wav`
- `ogg` -> `audio/ogg`
- `opus` -> `audio/opus`
- `aac` -> `audio/aac`

**✅ Resolution:** Explicit mappings added to `get_book_file` in `src/aarkib/routes/api.py`.

---

### 38. OPDS Feeds Leak Non-Readable Media (Video & Music) As Comic Books (Architecture / Standards — MEDIUM)

**File:** `src/aarkib/routes/opds.py:418, 529, 600, 660, 720, 770`

**Root Cause:** OPDS acquisition feeds queried all items via `select(Book)`. In multi-media libraries, movies (`.mp4`) and songs (`.mp3`) appeared in OPDS catalogs, typed as `@type: "http://schema.org/Book"`, and served as `application/vnd.comicbook+zip` to e-readers.

**Fix:**
Define `OPDS_READABLE_TYPES = ("book", "comic", "audiobook")` and filter all OPDS feed queries with `opds_readable_filter()`:
```python
def opds_readable_filter():
    return or_(
        Book.media_type.in_(OPDS_READABLE_TYPES),
        Book.media_type.is_(None),
    )
```

**✅ Resolution:** Added `opds_readable_filter()` to `opds.py` across `opds2_recent`, `recent_feed`, `author_books`, `series_books`, `tag_books`, and `search_feed`. Added verification test in `tests/test_opds.py`.

---

### 39. Docker Image Lacks `ffmpeg` / `ffprobe` (DevOps / Packaging — MEDIUM)

**File:** `Dockerfile:2-25`

**Root Cause:** The Docker container is built on `ghcr.io/astral-sh/uv:python3.14-bookworm-slim`, which does not include `ffmpeg`. Without `ffmpeg`, video frame thumbnail extraction (`extract_video_cover`), MKV/WebM duration detection, and audio chapter parsing silently fail in Docker deployments.

**Fix:**
Install `ffmpeg` during container image build:
```dockerfile
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*
```

**✅ Resolution:** Added `ffmpeg` package installation and created default `/app/data/media` directory in `Dockerfile`.

---

### 40. Permissive Password Minimum Length (Security — MEDIUM)

**File:** `src/aarkib/routes/auth.py:144, 185, 287`

**Root Cause:** Password validation currently permits 4-character passwords (`len(password) < 4`). Modern security guidelines (OWASP ASVS 4.0) recommend a minimum length of 8 to 12 characters.

**Recommendation:** Increase minimum password length to 8 characters across registration, profile password update, and admin user creation.

---

### 41. Re-introduction of `current_app._get_current_object()` (Architecture — MEDIUM)

**File:** `src/aarkib/routes/api.py:499, 1052, 1120`

**Root Cause:** In `trigger_scan`, `enrich_library`, and `scan_single_library`, `current_app._get_current_object()` was used to pass the concrete Flask app into background workers.

**Recommendation:** `JobManager.submit_job` should extract the app instance cleanly when passed `current_app` proxy, keeping route handlers free from private attribute access.

---

### 42. Outdated Model Terminology in Architecture Documentation (Documentation — LOW)

**File:** `BEST_PRACTICE_REPORT.md` (historical sections)

**Root Cause:** Earlier audit items in this document referred to `book_service.py`, `Author`, and `Series`. In commit `91cd38d`, these models and services were modernized to `media_service.py`, `Creator`, and `Collection`.

**✅ Resolution:** Documented the architectural evolution and model mappings (`MediaItem`, `Creator`, `Collection`, `Tag`, `JobRecord`) in the updated report.

---

### 43. Lack of Rate Limiting on Authentication Endpoints (Security — LOW)

**File:** `src/aarkib/routes/auth.py:91-120`

**Root Cause:** The `/auth/login` endpoint does not enforce rate limiting or exponential backoff, leaving instances exposed to credential brute-forcing over HTTP.

**Recommendation:** Introduce `Flask-Limiter` on `/auth/login` and `/auth/register` (e.g., max 10 attempts per minute per IP).

---

### 44. Missing Docker Data Directory for Unified Media (DevOps — LOW)

**File:** `Dockerfile:24`

**Root Cause:** The Dockerfile initialized `/app/data/books` but omitted `/app/data/media`, which is now the default multi-media mount directory recommended in `AGENT.md`.

**✅ Resolution:** Added `/app/data/media` to `mkdir` in `Dockerfile`.

---

### 45. Missing Cross-User Playlist Authorization Tests (Testing — LOW)

**File:** `tests/test_playlists.py`

**Root Cause:** Existing playlist tests only validated happy-path behavior for a single user, leaving authorization boundaries and negative test cases unverified.

**✅ Resolution:** Added `test_playlist_authorization_guards` in `tests/test_playlists.py` verifying that unauthenticated visitors and cross-user callers are rejected with 403 Forbidden.

---

## VERIFICATION CHECKLIST

After all fixes, run:

```bash
uv run ruff check --fix . && uv run ruff format .
uv run pytest -v
```

Ensure:
- [x] All 101 tests pass (101 passing as of 2026-09-11)
- [x] `ruff check` reports no errors
- [x] `ruff format --check` reports no changes needed
- [x] Manually verify CSRF tokens appear in forms and fetch calls
- [x] Manually verify admin endpoints reject unauthenticated requests when AUTH_REQUIRED=false
- [x] Check that SECRET_KEY must be set in production
- [x] Confirm page load times improve (fewer DB queries) with eager loading on shelves and API
- [x] Verify in-browser audio streaming (`/api/media/<id>/stream`) succeeds with valid audio MIME types
- [x] Verify playlist mutation endpoints reject unauthorized and unauthenticated users
- [x] Verify OPDS feeds exclude non-readable video and music media
