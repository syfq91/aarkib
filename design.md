# 🏛️ Aarkib System Architecture & Technical Design

**Aarkib** is a modern, lightweight, self-hosted media server engineered with Python 3.14, Flask, and SQLite. It provides catalog management, in-browser reading, on-demand e-ink device optimization, OPDS catalog feeds, multi-client reading progress synchronization, and an extensible plugin system with work-in-progress (WIP) support for audio and video media.

---

## 1. Architectural Principles & Goals

1. **Lightweight & Self-Contained**: Minimal runtime dependencies (`Flask`, `SQLAlchemy`, `Pillow`, `defusedxml`, `watchdog`). Operates seamlessly on low-powered hardware such as Raspberry Pi, home servers, or NAS appliances without requiring heavy services (like Redis or Celery).
2. **Standard-First Interoperability**: Implements established open standards:
   - **OPDS 1.2** (Atom XML) & **OPDS 2.0** (JSON-LD) for universal e-reader compatibility (KOReader, Moon+ Reader, Thorium, Cantook).
   - **OPDS Progression 1.0** for reading position synchronization with strict conflict resolution.
   - **OPDS Authentication Specification** (`application/opds-authentication+json`) alongside HTTP Basic Auth.
3. **E-Ink Native Experience**: Hardware-tailored processing pipeline that optimizes EPUB files on-demand (font stripping, CSS sanitization, image resizing/dithering) specifically for e-paper devices (Xteink, Kindle, Kobo).
4. **Non-Destructive Storage**: Original media archives (`.epub`, `.cbz`, `.mp3`, `.mp4`) are strictly read-only and never modified. Extracted covers, thumbnails, and optimized device variants are cached separately.
5. **Zero-Friction Web Reading & Media Access**: Built-in, responsive web readers for EPUB and CBZ with client-side progress tracking and offline asset caching via PWA Service Workers, with playback interfaces in progress for audiobooks, music, and video.
6. **Extensible Multi-Media Plugin Architecture**: Core data models and scanner pipeline decoupled from file types through abstract `MediaPlugin` handlers and declarative mixins.

---

## 2. High-Level System Architecture

```mermaid
graph TD
    subgraph Clients
        Web[Web Browser / PWA]
        EReader[E-Readers / Apps: KOReader, Moon+, Thorium]
        EInk[E-Ink Devices: Xteink, Kindle, Kobo]
        MediaPlayer[Media Players: Web Audio/Video Player - WIP]
    end

    subgraph Presentation & Routing Layer
        AuthFilter[Auth Guard & Basic Auth Interceptor]
        UIRoutes[UI Blueprint: /book/:id, /authors, /series]
        APIRoutes[REST API Blueprint: /api/books, /api/progress]
        OPDSRoutes[OPDS Blueprint: /opds, /opds/v2, /opds/:device]
        ReaderRoutes[Reader Blueprint: /reader/epub/:id, /reader/cbz/:id]
    end

    subgraph Service & Plugin Layer
        PluginRegistry[Plugin Registry: plugin_registry]
        BookPlugin[BookMediaPlugin: EPUB, CBZ, CBR, ZIP]
        AudioPlugin[AudioMediaPlugin: MP3, M4B, FLAC - WIP]
        VideoPlugin[VideoMediaPlugin: MP4, MKV - WIP]
        Scanner[Scanner & Watchdog Service]
        Optimizer[E-Ink Device Optimizer]
        Enricher[Metadata Enricher: Google Books & Open Library]
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

- **Multi-Directory Discovery**: Supported via multiple environment conventions:
  - `AARKIB_LIBRARY_DIR` or `AARKIB_BOOKS_DIR` (supports colon, semicolon, comma, or newline delimiters; legacy `BUUKUU_*` supported).
  - Numbered environment variables: `AARKIB_LIBRARY_DIR1`, `AARKIB_LIBRARY_DIR2`, `DIR1`, `DIR2` (or `BUUKUU_LIBRARY_DIR1`, ...).
  - Named variables: `AARKIB_LIBRARY_DIR_MANGA`, `AARKIB_LIBRARY_DIR_AUDIO`, `AARKIB_LIBRARY_DIR_VIDEO`.
- **Deduplication & Integrity**: Every book or media item is indexed by its SHA-256 hash. If a file is moved within the library, its record is updated without losing reading history or metadata customizations.
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
| **OPDS Progression 1.0** | `/opds/books/<id>/progression` | `application/vnd.opds.progression+json` | Read/write reading progression (percentage, locator, timestamp). |
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
   - **In-Memory Streaming**: Rather than serving unpacked files or individual XML resources through custom routing (which risks directory traversal vulnerabilities), the web client downloads the book as an `ArrayBuffer` via `/api/books/<id>/file` and opens it directly in browser memory.
   - **UI Controls**: Font size adjustments, margins, font family selection, full-text navigation, and color themes (Light, Dark, Sepia, OLED).
   - **Position Sync**: Continuously pushes reading CFI locators and calculated percentage to `/api/books/<id>/progress`.
2. **CBZ Comic Reader (`reader_cbz.html`, `reader-cbz.js`)**:
   - Custom, responsive HTML5 canvas and image viewer.
   - Dual viewing modes: **Continuous Vertical Webtoon Scroll** and **Single-Page Flip**.
   - Features: Fit-to-width, fit-to-height, fullscreen toggle, keyboard navigation (arrow keys, space), and automatic page-progress reporting.

---

### 3.6 Multi-Media Plugin Architecture & WIP Audio/Video Support (`plugins/`, `models/media.py`)

Aarkib features a decoupled, extensible plugin architecture designed to manage diverse personal media libraries under unified indexing, storage, and progress-tracking foundations:

1. **Plugin Contract (`MediaPlugin`)**:
   - Every media handler inherits from the abstract base class `MediaPlugin` (`plugins/base.py`).
   - Declares `name`, `media_type` (`MediaType.BOOK`, `MediaType.COMIC`, `MediaType.AUDIO`, `MediaType.VIDEO`), and `supported_extensions`.
   - Implements standardized lifecycle hooks:
     - `parse_metadata(file_path)`: Extracts title, creators/artists, descriptions, and technical metadata.
     - `extract_cover(file_path)`: Extracts embedded cover art, poster artwork, or chapter thumbnails.
     - `get_player_url(item_id, file_format)`: Returns the in-browser playback or reader route.
     - `register_routes(app)`: Injects custom Flask blueprints (e.g. streaming endpoints, custom player interfaces).
     - `check_health()`: Diagnostic checks for optional native tools or libraries.

2. **Central Registry (`PluginRegistry`)**:
   - Singleton `plugin_registry` initialized at application startup in `aarkib/__init__.py`.
   - Dynamically maps file extensions (e.g. `.epub`, `.cbz`, `.mp3`, `.mp4`) to their respective plugins and metadata parsers.
   - Enables new media plugins to be registered modularly without altering the core library crawler.

3. **Audio Support (Work In Progress)**:
   - **Target Formats**: `.mp3`, `.m4b`, `.flac`, `.aac`.
   - **Data Model** (`AudioTrackMixin`): Pre-defined database columns for `duration` (runtime in seconds), `bitrate` (kbps), `album`, `track_number`, and `disc_number`.
   - **Planned Capabilities**: Tag metadata parsing, chapter detection for audiobooks, cover art extraction, and a dedicated in-browser web audio player with listening resume position.

4. **Video Support (Work In Progress)**:
   - **Target Formats**: `.mp4`, `.mkv`, `.webm`.
   - **Data Model** (`VideoItemMixin`): Pre-defined database columns for `duration`, `resolution_width`, `resolution_height`, `codec`, `season`, and `episode`.
   - **Planned Capabilities**: Video metadata extraction, poster frame generation, responsive HTML5 web video player with subtitle track support, and stream progress persistence.

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

### Multi-Media Schema Mixins (`models/media.py`)
- **`MediaItemMixin`**: Standardized base columns across all media (`title`, `sort_title`, `media_type`, `original_file_path`, `file_format`, `file_size`, `file_hash`, `cover_image_path`, `description`, `publisher`, `language`, `publication_date`, timestamps).
- **`AudioTrackMixin` (WIP)**: Schema extension columns for audio media: `duration` (seconds), `bitrate` (kbps), `album`, `track_number`, `disc_number`.
- **`VideoItemMixin` (WIP)**: Schema extension columns for video media: `duration` (seconds), `resolution_width`, `resolution_height`, `codec`, `season`, `episode`.

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
   - API and OPDS requests inspect the `Authorization: Basic <credentials>` header. If present, credentials are authenticated directly against the `User` model, allowing headless readers to connect seamlessly without browser session cookies.
3. **Security Headers**:
   - Injected on all outgoing responses: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`.
4. **Data Isolation**:
   - All reading positions, progress markers, and bookmarks are strictly partitioned by `user_id`. One user cannot read or alter another user's progress.
5. **Administrative Boundaries**:
   - Modifying book metadata, triggering full-library scans, creating users, and changing user permissions are protected by `@admin_required`.

---

## 6. Deployment & Runtime Operations

### Packaging & Environment
- **Python Version**: `>=3.14`
- **Dependency Management**: `uv` using pinned `uv.lock`.
- **Container Strategy**: Multi-stage `Dockerfile` using `python:3.14-slim` and `uv` for minimal attack surface and lightweight image footprints.
- **Persistent Volumes**:
  - `/app/data`: Houses `aarkib.db`, `covers/`, and `optimized/`.
  - `/app/data/books` (or external mounts like `/media/audio`, `/media/video`): Primary read-only media storage.

### Key Operational CLI Commands
- `uv run aarkib`: Start web application server.
- `uv run aarkib scan [--enrich]`: Trigger indexing scan and optional online metadata enrichment.
- `uv run aarkib create-admin <username>`: Bootstrap or update administrator account.
- `uv run aarkib create-user <username> [--admin]`: Create a reader or admin account.
- `uv run aarkib list-users`: Display registered user credentials and roles.
