# 🏛️ Aarkib System Architecture & Technical Design

**Aarkib** is a modern, lightweight, self-hosted media server engineered with Python 3.14, Flask, and SQLite. It provides catalog management, in-browser reading, on-demand e-ink device optimization, OPDS catalog feeds, multi-client reading and playback progress synchronization, and an extensible plugin system with native support for books, comics, and video, alongside in-progress audio media capabilities.

---

## 1. Architectural Principles & Goals

1. **Lightweight & Self-Contained**: Minimal runtime dependencies (`Flask`, `SQLAlchemy`, `Pillow`, `defusedxml`, `watchdog`). Operates seamlessly on low-powered hardware such as Raspberry Pi, home servers, or NAS appliances without requiring heavy services (like Redis or Celery).
2. **Standard-First Interoperability**: Implements established open standards:
   - **OPDS 1.2** (Atom XML) & **OPDS 2.0** (JSON-LD) for universal e-reader compatibility (KOReader, Moon+ Reader, Thorium, Cantook).
   - **OPDS Progression 1.0** for reading position synchronization with strict conflict resolution.
   - **OPDS Authentication Specification** (`application/opds-authentication+json`) alongside HTTP Basic Auth.
   - **HTTP 206 Partial Content** for native video/audio range streaming and seeking.
3. **E-Ink Native Experience**: Hardware-tailored processing pipeline that optimizes EPUB files on-demand (font stripping, CSS sanitization, image resizing/dithering) specifically for e-paper devices (Xteink, Kindle, Kobo).
4. **Non-Destructive Storage**: Original media archives (`.epub`, `.cbz`, `.mp3`, `.mp4`) are strictly read-only and never modified. Extracted covers, thumbnails, and optimized device variants are cached separately.
5. **Zero-Friction Web Reading & Media Access**: Built-in, responsive web readers for EPUB and CBZ, and an HTML5 video player with client-side progress tracking and offline asset caching via PWA Service Workers, with persistent audio player in progress for audiobooks and music.
6. **Extensible Multi-Media Plugin Architecture**: Core data models and scanner pipeline decoupled from file types through abstract `MediaPlugin` handlers and declarative mixins.

---

## 2. High-Level System Architecture

```mermaid
graph TD
    subgraph Clients
        Web[Web Browser / PWA]
        EReader[E-Readers / Apps: KOReader, Moon+, Thorium]
        EInk[E-Ink Devices: Xteink, Kindle, Kobo]
        MediaPlayer[Media Players: Web Video & Audio Players]
    end

    subgraph Presentation & Routing Layer
        AuthFilter[Auth Guard & Basic Auth Interceptor]
        UIRoutes[UI Blueprint: /media/:id, /authors, /series]
        APIRoutes[REST API Blueprint: /api/media, /api/libraries]
        OPDSRoutes[OPDS Blueprint: /opds, /opds/v2, /opds/:device]
        ReaderRoutes[Reader & Player: /reader/epub/:id, /reader/cbz/:id, /reader/video/:id]
    end

    subgraph Service & Plugin Layer
        JobManager[Background Job Manager: ThreadPoolExecutor]
        PluginRegistry[Plugin Registry: plugin_registry]
        BookPlugin[BookMediaPlugin: EPUB, CBZ, CBR, ZIP]
        AudioPlugin[AudioMediaPlugin: MP3, M4B, FLAC, AAC, WAV]
        VideoPlugin[VideoMediaPlugin: MP4, MKV, WEBM, AVI, MOV, M4V]
        Scanner[Scanner & Watchdog Service]
        Optimizer[E-Ink Device Optimizer]
        Enricher[Metadata Enricher: Google Books, Open Library, TMDB, MusicBrainz]
    end

    subgraph Persistence & Storage Layer
        DB[(SQLite WAL: aarkib.db)]
        BooksDir[(Media Folders: ./data/books, ./data/audio, ./data/video)]
        CoversDir[(Cover Storage: ./data/covers - WebP)]
        OptimizedDir[(Optimized Cache: ./data/optimized)]
    end

    Web --> AuthFilter
    EReader --> AuthFilter
    EInk --> AuthFilter
    MediaPlayer --> AuthFilter

    AuthFilter --> UIRoutes
    AuthFilter --> APIRoutes
    AuthFilter --> OPDSRoutes
    AuthFilter --> ReaderRoutes

    UIRoutes --> DB
    APIRoutes --> DB
    ReaderRoutes --> BooksDir

    OPDSRoutes --> DB
    OPDSRoutes --> Optimizer

    Scanner --> PluginRegistry
    PluginRegistry --> BookPlugin
    PluginRegistry --> AudioPlugin
    PluginRegistry --> VideoPlugin
    Scanner --> DB
    Scanner --> CoversDir

    Optimizer --> BooksDir
    Optimizer --> OptimizedDir

    Enricher --> DB
    Enricher --> CoversDir
```

---

## 3. Core Subsystems & Components

### 3.1 Data Ingestion & Library Scanner (`services/scanner.py`)

The scanner discovers, validates, extracts metadata from, and tracks digital books across one or more library folders.

```mermaid
sequenceDiagram
    autonumber
    participant FS as Filesystem / Watchdog
    participant Scanner as Library Scanner
    participant Parser as EPUB / CBZ Parser
    participant DB as SQLite DB
    participant Cache as Covers Storage

    FS->>Scanner: Trigger Scan (Startup, CLI, API, or File Event)
    Scanner->>Scanner: Enumerate files (*.epub, *.cbz)
    loop Each File
        Scanner->>Scanner: Compute SHA-256 Hash
        Scanner->>DB: Check if file_hash exists
        alt New or Modified Book
            Scanner->>Parser: Parse archive & extract metadata
            Parser-->>Scanner: ParsedBookMetadata
            Scanner->>Cache: Convert cover image to WebP & save
            Scanner->>DB: Upsert Book, Authors, Series, Tags
        else Unchanged
            Scanner->>Scanner: Skip re-parsing
        end
    end
    Scanner->>DB: Remove records whose files no longer exist on disk
```

- **Generic Media Folder Architecture & WebUI Configuration**:
  - Media directories are managed in SQLite via the `Library` model (`libraries` table).
  - Each library defines a distinct `media_type`:
    - `all`: Mixed / Auto-detect by extension (`.epub` → `book`, `.cbz`/`.cbr`/`.zip` → `comic`, `.mp4`/`.mkv`/etc. → `video`).
    - `book`: Enforces `book` categorization for all documents in that folder.
    - `comic`: Enforces `comic` categorization for all archives/manga in that folder.
    - `video`: Enforces `video` categorization for movies and television series.
  - **Dynamic WebUI & API Control**: Users can configure, inspect item counts, change media types, rescan, or add/delete folders via the WebUI Settings page or REST API (`/api/libraries`). Changing a folder's `media_type` automatically re-classifies all existing items in the database.
- **Direct Folder Drops (No Upload UI)**:
  - Users add media simply by copying or mounting files into storage folders (`./data/media`, `./data/books`, external drives). The scanner and filesystem watcher handle indexing automatically without requiring web upload forms.
- **Multi-Directory Discovery**: Supported via namespaced environment conventions:
  - Canonical: `AARKIB_MEDIA_DIR` (supports colon, semicolon, comma, or newline delimiters).
  - Numbered environment variables: `AARKIB_MEDIA_DIR1`, `AARKIB_MEDIA_DIR2`, `AARKIB_MEDIA_DIR_1`, etc.
  - Named category variables: `AARKIB_MEDIA_DIR_MANGA`, `AARKIB_MEDIA_DIR_MOVIES`, `AARKIB_MEDIA_DIR_VIDEO`.
- **Deduplication & Integrity**: Every media item is indexed by its SHA-256 hash. If a file is moved within the library, its record is updated without losing reading/playback history or metadata customizations.
- **Background Filesystem Watching**: A `watchdog.observers.Observer` monitors all active library directories for file additions, modifications, or deletions when `AARKIB_WATCH_LIBRARY=true`.

---

### 3.2 Metadata Parsers (`services/parsers/`)

- **EPUB Parser (`parsers/epub.py`)**:
  - Unpacks EPUB container metadata (`META-INF/container.xml`) using secure XML parsing via `defusedxml`.
  - Extracts Dublin Core tags (title, creator, description, publisher, language, identifiers).
  - **Series Extraction**:
    1. Calibre meta tags: `<meta name="calibre:series" content="..." />` and `<meta name="calibre:series_index" content="..." />`.
    2. EPUB 3 Collection properties: `<meta property="belongs-to-collection" id="...">` and `<meta refines="#..." property="group-position">`.
    3. Filename/Title regex heuristic fallback: Extracts series names and volume numbers from conventions like `Series Name - Vol. 01 - Title` or `Series Name #01`.
  - **Cover Extraction**: Locates `<meta name="cover">` or items flagged with `properties="cover-image"`. Falls back to scanning root-level images matching cover naming conventions.
- **CBZ Parser (`parsers/cbz.py`)**:
  - Parses Comic book archive zip files.
  - Inspects embedded `ComicInfo.xml` (Anansi / ComicRack standard) for `<Series>`, `<Number>`, `<Writer>`, `<Penciller>`, and `<Genre>`.
  - Sorts archive image entries naturally (`page1.jpg`, `page2.jpg`, `page10.jpg`) and uses the first image page as the volume cover.

---

### 3.3 E-Ink On-Demand Optimization Engine (`services/optimizer.py`)

Dedicated e-paper devices suffer from limited CPU processing power, small RAM envelopes, and fixed e-ink display refresh modes. Modern EPUBs often contain embedded web fonts (multi-megabyte WOFF/OTF files), complex CSS resets, and high-resolution 24-bit color illustrations that cause sluggish page turns or rendering artifacts on e-ink hardware.

#### Optimization Pipeline

```mermaid
graph LR
    EPUBIn[Original EPUB Archive] --> ZipExtract[Read In-Memory Archive]
    ZipExtract --> StripFonts[Strip Embedded Fonts: .ttf, .otf, .woff]
    ZipExtract --> CleanCSS[Sanitize CSS & Remove @font-face]
    ZipExtract --> ProcessImages[Process Images: Resize / Grayscale / Dither]
    
    StripFonts --> Rebuild[Repack EPUB Archive: Store mimetype, Deflate contents]
    CleanCSS --> Rebuild
    ProcessImages --> Rebuild
    Rebuild --> DiskCache[Cache to data/optimized/:hash_:preset.epub]
```

- **Device Presets**:
  - **Xteink X3**: 528×792, 16-level grayscale with Floyd-Steinberg dithering, fonts stripped.
  - **Xteink X4**: 480×800, 16-level grayscale with Floyd-Steinberg dithering, fonts stripped.
  - **Kindle Paperwhite / Oasis**: 1072×1448, grayscale, fonts stripped, CSS cleaned.
  - **Kobo Clara / Libra**: 1264×1680, grayscale, fonts stripped, CSS cleaned.
  - **Generic E-Ink**: 1200×1600, grayscale, fonts stripped.
- **Caching Mechanism**: Generated optimized files are stored in `data/optimized/{book_hash}_{preset}.epub`. If the source book changes (updated hash), caches are invalidated automatically.
- **Non-Destructive**: The source library file is never overwritten or mutated.

---

### 3.4 OPDS Catalog & Sync Protocols (`routes/opds.py`)

Aarkib exposes a complete suite of OPDS endpoints tailored for modern e-readers and synchronization clients:

| Protocol / Standard | Endpoint | MIME Type / Format | Purpose |
| :--- | :--- | :--- | :--- |
| **OPDS 1.2 Catalog** | `/opds` | `application/atom+xml` | Main Atom navigation feed (Recent, Authors, Series, Tags). |
| **OPDS 1.2 Search** | `/opds/search?q={query}` | `application/atom+xml` | OpenSearch feed for querying titles and creators. |
| **OPDS 2.0 Catalog** | `/opds/v2/catalog.json` | `application/opds+json` | Modern JSON-LD publication and navigation feeds. |
| **OPDS Authentication** | `/opds/authentication.json` | `application/opds-authentication+json` | Informs clients of HTTP Basic Auth challenge requirements. |
| **OPDS Progression 1.0** | `/opds/media/<id>/progression` | `application/vnd.opds.progression+json` | Read/write reading progression (percentage, locator, timestamp). |
| **Device OPDS Feeds** | `/opds/<preset>` | `application/atom+xml` | Atom feeds offering pre-routed links to optimized EPUB downloads. |

#### Reading Progression & Conflict Handling

In adherence to the [OPDS Progression 1.0 Specification](https://github.com/opds-community/drafts/blob/main/opds-progression-1.0.md):
- Progression values sent/received over the wire are expressed as floats in `[0.0, 1.0]`, translated internally to percentages `[0.0, 100.0]`.
- Every progress update includes a UTC `modified` ISO timestamp.
- **Conflict Resolution**: If a client attempts to `PUT` a progression payload whose `modified` timestamp is older than the server's recorded timestamp, the server responds with:
  - HTTP status: `409 Conflict`
  - Header: `Content-Type: application/problem+json`
  - Body: `{"type": "https://registry.opds.io/error#progression-date", "title": "Conflict", "detail": "Server has newer progression"}`

---

### 3.5 In-Browser Web Readers (`routes/reader.py` & `static/js/`)

Aarkib provides rich in-browser reading environments without external server plugins:

1. **EPUB Web Reader (`reader_epub.html`, `reader-epub.js`)**:
   - Built on `ePub.js` and `JSZip`.
   - **In-Memory Streaming**: Rather than serving unpacked files or individual XML resources through custom routing (which risks directory traversal vulnerabilities), the web client downloads the book as an `ArrayBuffer` via `/api/media/<id>/file` and opens it directly in browser memory.
   - **UI Controls**: Font size adjustments, margins, font family selection, full-text navigation, and color themes (Light, Dark, Sepia, OLED).
   - **Position Sync**: Continuously pushes reading CFI locators and calculated percentage to `/api/media/<id>/progress`.
2. **CBZ Comic Reader (`reader_cbz.html`, `reader-cbz.js`)**:
   - Custom, responsive HTML5 canvas and image viewer.
   - Dual viewing modes: **Continuous Vertical Webtoon Scroll** and **Single-Page Flip**.
   - Features: Fit-to-width, fit-to-height, fullscreen toggle, keyboard navigation (arrow keys, space), and automatic page-progress reporting.

---

### 3.6 Generalized Plugin Architecture (`plugins/`, `plugins/base.py`)

Aarkib features a decoupled, extensible plugin architecture designed to manage diverse personal media libraries, external protocols, and device optimization pipelines under a unified lifecycle:

1. **Foundational Plugin Hierarchy (`BasePlugin`)**:
   - Every plugin inherits from `BasePlugin` (`plugins/base.py`), exposing:
     - Identification & Metadata: `name`, `display_name`, `plugin_type`, `description`.
     - Lifecycle & Routing: `register_routes(app)`, `init_app(app)`, `check_health()`.
     - Configuration & Security: `enabled`, `csrf_exempt`, `blueprint_options`.
   - Archetypes derived from `BasePlugin`:
     - **`MediaPlugin`**: Format-specific file parsing (`parse_metadata`), artwork extraction (`extract_cover`), player URLs (`get_player_url`), and file extensions (`supported_extensions`).
     - **`ProtocolPlugin`**: Server-side protocol feeds and client streaming APIs (`protocol_version`, `url_prefix`, `csrf_exempt=True`).
     - **`OptimizerPlugin`**: Media transformation and hardware-targeted optimization pipelines (`get_presets`, `optimize`, `supported_formats`).

2. **Central Registry (`PluginRegistry`)**:
   - Singleton `plugin_registry` initialized at application startup in `aarkib/__init__.py`.
   - Dynamically maps file extensions, media types, protocol names, and optimizer handlers.
   - Automatically registers blueprints and handles CSRF exemptions on startup without hardcoding route wiring in core application files.
   - Supports feature toggling via environment configuration (`AARKIB_ENABLE_SUBSONIC`, `AARKIB_ENABLE_OPDS`, `AARKIB_ENABLE_EINK_OPTIMIZER`).

3. **Protocol Plugins**:
   - **OPDS Protocol Plugin (`OPDSProtocolPlugin`)**: Implements OPDS 1.2 (Atom XML), OPDS 2.0 (JSON-LD), OPDS Authentication, and OPDS Progression 1.0 reading synchronization under `/opds`.
   - **Subsonic Protocol Plugin (`SubsonicProtocolPlugin`)**: Implements Subsonic v1.16.1 REST API endpoints under `/rest` for native streaming and cataloging in third-party mobile apps (Symfonium, DSub, Ultrasonic).

4. **Optimizer Plugins**:
   - **E-Ink Device Optimizer (`EInkOptimizerPlugin`)**: Hardware-specific EPUB transformation pipeline with font stripping, CSS sanitization, and 16-level grayscale Floyd-Steinberg dithering.

5. **Media Plugins**:
   - **Books & Comics (`BookMediaPlugin`)**: `.epub`, `.cbz`, `.cbr`, `.zip` with web readers and ComicInfo.xml support.
   - **Video (`VideoMediaPlugin`)**: `.mp4`, `.mkv`, `.webm`, `.avi`, `.mov`, `.m4v` with MP4 box parsing, HTTP 206 chunk seeking, and HTML5 video player.
   - **Audio (`AudioMediaPlugin`)**: Generic `.mp3`, `.m4a`, `.flac`, `.ogg`, `.opus`, `.wav`, `.aac` playback.
   - **Audiobooks (`AudiobookMediaPlugin`)**: Dedicated `.m4b` chapter markers and audiobook player.
   - **Music (`MusicMediaPlugin`)**: Dedicated albums, tracks, and disc numbering.
   - **Podcasts (`PodcastMediaPlugin`)**: Episodic seasons, episode numbers, and podcast player.

### 3.8 Dynamic System Preferences & Settings Service (`services/settings_service.py`)

Aarkib decouples runtime application preferences from static environment configuration. Key operational controls are managed via a database-backed settings architecture:

1. **Managed Runtime Settings**:
   - `AUTH_REQUIRED` (bool): Require visitor authentication to browse, stream, download, or access OPDS.
   - `ALLOW_REGISTRATION` (bool): Enable or disable public account registration on the login screen.
   - `AUTO_SCAN_ON_START` (bool): Automatically trigger a full library scan at server startup.
   - `WATCH_LIBRARY` (bool): Enable or disable the real-time background `watchdog` observer.
   - `AUTO_ENRICH` (bool): Automatically fetch online metadata during library scans.
   - `METADATA_PROVIDER` (str): Online provider selector (`all`, `googlebooks`, `openlibrary`).
   - `PAGE_SIZE` (int): Catalog items per page.

2. **Precedence Hierarchy**:
   `WebUI Settings (Database)` $\to$ `Environment Variables (.env)` $\to$ `Hardcoded Defaults`.
   If a setting has been modified via the Web UI, its persisted value in the `settings` SQLite table overrides the `.env` value. Unconfigured settings seamlessly fall back to `.env` variables or built-in defaults.

3. **Hot-Reloading & Live Synchronization**:
   - At startup, `load_settings_into_config(app)` injects all database overrides into Flask's `app.config`.
   - Modifying settings via `PATCH /api/settings` immediately commits to SQLite and updates `app.config` in-memory without restarting the server.
   - Toggling `WATCH_LIBRARY` dynamically starts or stops the background `watchdog.Observer` thread on the fly.
   - Admins can revert all customizations to environment defaults at any time via `POST /api/settings/reset`.

---

## 4. Data Models & Entity Relationship

```mermaid
erDiagram
    User ||--o{ UserProgress : "tracks"
    User ||--o{ Bookmark : "creates"
    Book ||--o{ UserProgress : "has"
    Book ||--o{ Bookmark : "contains"
    Book }|--|{ Author : "written_by"
    Book }|--|{ Series : "belongs_to"
    Book }|--|{ Tag : "categorized_under"

    Library {
        int id PK
        string slug UK
        string name
        string path UK
        string media_type
        datetime created_at
        datetime updated_at
    }

    User {
        int id PK
        string username UK
        string password_hash
        boolean is_admin
        datetime created_at
    }

    Book {
        int id PK
        string original_file_path UK
        string file_hash UK
        string media_type
        string title
        string sort_title
        string file_format
        int file_size
        string cover_image_path
        text description
        string publisher
        string publication_date
        string language
        string isbn
        float series_index
        datetime created_at
        datetime updated_at
    }

    Author {
        int id PK
        string name UK
        string sort_name
    }

    Series {
        int id PK
        string name UK
    }

    Tag {
        int id PK
        string name UK
    }

    UserProgress {
        int id PK
        int user_id FK
        int book_id FK
        float percentage
        string locator
        datetime modified
        datetime completed_at
    }

    Bookmark {
        int id PK
        int user_id FK
        int book_id FK
        string locator
        string label
        datetime created_at
    }
```

### Multi-Media Schema Mixins & Models (`models/`)
- **`Library` (`models/library.py`)**: Persistent media library directory configuration (`slug`, `name`, `path`, `media_type`).
- **`SystemSetting` (`models/setting.py`)**: Key-value application configuration store (`key`, `value`, `updated_at`) supporting runtime WebUI overrides with dynamic in-memory hot-reloading into Flask's `app.config`.
- **`MediaItemMixin` (`models/media.py`)**: Standardized base columns across all media (`title`, `sort_title`, `media_type`, `original_file_path`, `file_format`, `file_size`, `file_hash`, `cover_image_path`, `description`, `publisher`, `language`, `publication_date`, timestamps), plus relational `library_id` FK.
- **`VideoItemMixin` (`models/media.py`)**: Schema extension columns for video media (`duration`, `resolution_width`, `resolution_height`, `codec`, `season`, `episode`).
- **`AudioTrackMixin` (`models/media.py`)**: Schema extension columns for audio media: `duration` (seconds), `bitrate` (kbps), `album`, `track_number`, `disc_number`, and `chapters_json`.
- **`BackgroundJob` (`models/job.py`)**: Persistent background task tracking (`task_id`, `job_type`, `status`, `progress`, `result_json`, timestamps).
- **Curation Models**: `UserFavorite` (`models/user.py`) for starring media items and `Playlist` / `PlaylistItem` (`models/playlist.py`) for custom media collections.

### Database Pragmas & Concurrency
- Configured with SQLite Write-Ahead Logging (`PRAGMA journal_mode=WAL`).
- `PRAGMA synchronous=NORMAL` to maximize transaction throughput while maintaining durability.
- `PRAGMA foreign_keys=ON` to enforce relational constraints.
- Automatic schema migration (`migrate_database()`) checks `db.metadata.tables` against runtime SQLite columns and executes non-destructive `ALTER TABLE ADD COLUMN` operations on startup.

---

## 5. Security & Authentication Architecture

1. **Authentication Enforcement (`AARKIB_AUTH_REQUIRED`)**:
   - When enabled (default), all web routes redirect unauthenticated users to `/auth/login`.
   - If the database contains zero users, the application automatically redirects visitors to `/auth/register` to establish the initial Administrator account.
2. **Dual-Credential Interceptor**:
   - Web sessions are secured with signed HTTP-only cookies (`Lax` SameSite policy).
   - API and OPDS requests inspect the `Authorization: Basic <credentials>` header. When present, Flask-Login's `request_loader` validates credentials against the `User` model and populates `current_user`, allowing headless readers to sync progress and access collections seamlessly without browser cookies.
3. **Security Headers**:
   - Injected on all outgoing responses: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin`.
4. **Data Isolation**:
   - All reading positions, progress markers, and bookmarks are strictly partitioned by `user_id`. One user cannot read or alter another user's progress.
5. **Administrative Boundaries**:
   - Modifying book metadata, triggering full-library scans, creating users, and changing user permissions are protected by `@admin_required`.
6. **Defensive Parsing & SSRF Protection**:
   - All external XML processing (`parsers/epub.py`, `parsers/cbz.py`, and `services/optimizer.py`) utilizes `defusedxml` to defend against XML entity expansion (Billion Laughs) and XXE vulnerabilities.
   - The metadata enricher verifies URL schemes (`http`, `https`) before issuing outbound requests to prevent SSRF or arbitrary local file disclosure (`file://`).

---

## 6. Deployment & Runtime Operations

### Packaging & Environment
- **Python Version**: `>=3.14`
- **Dependency Management**: `uv` using pinned `uv.lock`.
- **Container Strategy**: Multi-stage `Dockerfile` using `python:3.14-slim` and `uv` for minimal attack surface and lightweight image footprints. Runs as an unprivileged user (`USER aarkib`) with a standard container `HEALTHCHECK` querying `/api/health`.
- **Persistent Volumes**:
  - `/app/data`: Houses `aarkib.db`, `covers/`, and `optimized/`.
  - `/app/data/books` (or external mounts like `/media/audio`, `/media/video`): Primary read-only media storage.

### Server Launcher & Web Administration
- `uv run aarkib`: Start the web application server (or container startup via `aarkib`).
- **Administrative Operations**: Managed exclusively via the modern Web UI:
  - Account setup & user management: First-run setup wizard and `/settings#users`.
  - Content discovery: Background filesystem watchers and on-demand rescan via `/settings`.
  - Full-Text Search: Automatic startup synchronization and manual reindexing via `/settings`.
  - Metadata enrichment: Background enricher jobs triggered via `/settings` or per-media detail views.
