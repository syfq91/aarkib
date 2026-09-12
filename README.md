# 🏛️ Aarkib

> A modern, lightweight, self-hosted media server built with Flask, SQLite, and PWA capabilities. Built for books, comics, and video streaming (movies & TV shows) with OPDS 1.2, OPDS 2.0, **OPDS Progression 1.0** reading sync, e-ink optimization, and in-browser readers & media players — with an extensible plugin architecture and planned audio support.

[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![OPDS: Progression 1.0](https://img.shields.io/badge/OPDS-Progression%201.0-green.svg)](https://github.com/opds-community/drafts/blob/main/opds-progression-1.0.md)

---

## ✨ Features

- 📖 **E-Books & Comics**: Native support for `.epub`, `.cbz`, `.cbr`, and `.zip` formats with automatic metadata & cover extraction.
- 🎬 **Movies & TV Shows**: Streaming for `.mp4`, `.mkv`, `.webm`, `.avi`, `.mov`, `.m4v` with HTTP 206 byte-range seeking, smart TV show (`S01E02`) / movie naming detection, pure-Python MP4 container metadata (duration, width, height), and poster extraction.
- 🎵 **Audiobooks & Music**: Built-in support for audio media (`.mp3`, `.m4b`, `.flac`, `.aac`, `.wav`) with pure-Python ID3/FLAC metadata parsing, dedicated in-browser HTML5 audio player, runtime duration, album artwork, and listening progress synchronization.
- 🎙️ **Podcasts & Audio Shows**: Ingest local podcast audio collections, OPML feed import, iTunes & PodcastIndex online enrichment, and dedicated episode player with playback speed and scrubber navigation.
- 📱 **Subsonic / OpenSubsonic API**: Built-in Subsonic API compatibility layer (`/rest`) enabling native mobile streaming via popular clients like Symfonium, DSub, Ultrasonic, and Plappa.
- ⚡ **Non-Blocking Background Job Architecture**: Asynchronous worker for library scans and batch enrichments without blocking web workers.
- 🎬 **Adaptive Remuxing & Transcoding**: Direct MKV container remuxing (`-c copy`) and HLS transcoding with Linux VAAPI hardware acceleration.
- 🔌 **Extensible Media Plugin & Provider System**: Pluggable architecture (`MediaPlugin` & `MetadataProvider`) allowing modular media parsers, artwork extractors, and online metadata scrapers (Google Books, Open Library, TMDB, MusicBrainz).
- 📡 **OPDS 1.2 & OPDS 2.0 Feeds**: Full OPDS catalog feeds compatible with e-readers like KOReader, Thorium Reader, Cantook, Panels, and Moon+ Reader.
- 🔄 **OPDS Progression 1.0**: Built-in support for the latest [OPDS Progression 1.0](https://github.com/opds-community/drafts/blob/main/opds-progression-1.0.md) standard to sync reading positions across devices with conflict resolution.
- 🌐 **In-Browser Web Readers & Players**:
  - **EPUB Web Reader**: Fast in-memory array buffer decoding via ePub.js & JSZip, with themes (Dark, Sepia, OLED, Light), font sizing, and bookmarking.
  - **CBZ Comic Reader**: Smooth canvas & image viewer with continuous scroll, single-page flip, zoom, reading direction (LTR/RTL), page spreads, and fullscreen support.
  - **HTML5 Video Player**: Clean player with position resume, keyboard shortcuts (Space, Arrow keys, Fullscreen, Mute), playback speed selector (0.75x–2.0x), and automatic next-episode countdown.
- 👥 **Multi-User & Role Management**:
  - Isolated reading progress, bookmarks, and statistics per user.
  - Mandatory authentication across all interfaces (WebUI, REST API, OPDS feeds, Subsonic).
  - First-time boot setup wizard (`/auth/setup`) requiring the creation of the primary administrator account with a compulsory password.
  - Support for passwordless accounts for normal reader users.
  - Dedicated admin dashboard (`/settings/users`) to create accounts (with or without password for readers; compulsory for admins), reset passwords, and toggle roles.
- 🎨 **Modern Responsive UI / PWA**:
  - Clean top header navigation bar with user avatar menu.
  - Dedicated mobile bottom navigation bar on mobile devices.
  - Dark, Light, and OLED themes with persistent state.
  - Installable Progressive Web App (PWA) with offline asset caching.
- ✨ **Metadata Enrichment & Manual Editor**:
  - Auto-enrich media using Google Books, Open Library, TMDB, and MusicBrainz.
  - Interactive "✏️ Edit Metadata" modal on media details.
  - Smart automatic series volume detection from filenames and EPUB 3 / Calibre OPF tags.
- 🐳 **Docker & Production Ready**: Hardened container running as unprivileged user (`USER aarkib`), built-in healthcheck endpoint (`/api/health`), and persistent SQLite WAL storage.

---

## 🚀 Quick Start

### Option A: Running with `uv` (Local Development)

```bash
# 1. Clone the repository
git clone https://github.com/syfq91/aarkib.git
cd aarkib

# 2. Sync dependencies
uv sync

# 3. Create your storage folders
mkdir -p data/media data/covers

# 4. Copy environment configuration
cp .env.example .env

# 5. Start the server
uv run aarkib
```

Visit **`http://localhost:5000`** in your browser. On your first visit, you will be redirected to the Setup Wizard (`/auth/setup`) to configure your primary **Administrator** account (with a compulsory password).

---

### Option B: Running with Docker Compose

```bash
# Start container in background
docker compose up -d
```

Your library media placed in `./data` (or subdirectories `./data/media`, `./data/books`, `./data/videos`) will be mounted automatically. Alternatively, external host media folders can be mounted directly to `/media` (e.g., `/media/books`, `/media/videos`) in `docker-compose.yml` and managed via the WebUI.

---

## ⚙️ Configuration (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SECRET_KEY` | Auto-generated in `<DATA_DIR>/secret_key` | Secret key for session security & signing. Auto-generated and persisted on first boot if omitted. |
| `AARKIB_DATA_DIR` | `./data` | Base storage directory. |
| `AARKIB_MEDIA_DIR` | `./data/media` | Primary media folder. |
| `AARKIB_MEDIA_DIR1`, `DIR2`, ... | *(none)* | Additional numbered media folders (`AARKIB_MEDIA_DIR1`, `AARKIB_MEDIA_DIR2`, etc.). |
| `AARKIB_COVERS_DIR` | `./data/covers` | Storage directory for extracted cover art. |
| `AARKIB_OPTIMIZED_DIR` | `./data/optimized` | Cache directory for on-demand e-ink optimized EPUBs. |
| `DATABASE_URL` | `sqlite:///data/aarkib.db` | SQLAlchemy database URI. |
| `FLASK_DEBUG` | `0` | Enable Flask development debugger (disabled by default in production). |
| `APP_ENV` | `development` | Runtime environment selecting the app config: `production` (enforces secure sessions), `testing`, or `development`. |
| `PORT` | `5000` | Server listening port. |

> **💡 WebUI Configuration & Preferences**:
> - **System Preferences**: Administrators can configure runtime application options directly from **Settings → ⚙️ System Preferences** without restarting the server:
>   - **Auto-Scan on Startup** & **Real-Time Filesystem Watcher**
>   - **Auto-Enrich Metadata on Scan** & **Preferred Online Metadata Provider**
>   - **Catalog Items Per Page**
> - **WebUI-First Management**: System preferences and plugin toggles are persisted in the database with built-in defaults and can be modified or reverted in the UI without touching `.env` or Docker Compose.
> - **Media Folders Management**: Head to **Settings → 📁 Media Folders & Libraries** in the web interface to browse server folders, add new media directories, and customize each folder's library name and media type:
>   - **Interactive Folder Browser**: Click **Browse** in the WebUI to select any mounted or local folder on the server without typing manual paths.
>   - **Mixed / Auto-detect (`all`)**: Automatically detects books (`.epub`), comics/manga (`.cbz`, `.cbr`, `.zip`), videos (`.mp4`, `.mkv`), audiobooks, and music.
>   - **Books Only (`book`)**: Catalogs files inside as books.
>   - **Comics & Manga (`comic`)**: Catalogs files inside as comics/manga.
>   - **Movies & TV Shows (`video`)**: Catalogs video media files inside as movies & shows.
>   - **Audiobooks (`audiobook`)** & **Music Tracks (`music`)**: Catalogs dedicated audio formats.
> - **Environment Configuration**: A default media directory can be set via `AARKIB_MEDIA_DIR=/media/storage`, with optional numbered mount variables like `AARKIB_MEDIA_DIR1=/mnt/nas/books`. All media types and additional directories are configured cleanly via the WebUI.

---

## 📡 OPDS Catalog & E-Reader Setup

Aarkib exposes standard OPDS feeds as well as on-demand auto-optimizing feeds for e-ink devices (strips bloat fonts, converts images to grayscale/dithered e-ink format, and compresses on the fly without modifying original files):

### Standard Feeds:
- **OPDS 1.2 Feed (Atom)**: `http://<your-server-ip>:5000/opds`
- **OPDS 2.0 Feed (JSON)**: `http://<your-server-ip>:5000/opds/v2/catalog.json`
- **OPDS Authentication Document**: `http://<your-server-ip>:5000/opds/authentication.json`
- **OPDS Progression 1.0 Endpoint**: `http://<your-server-ip>:5000/opds/media/<id>/progression`

### ⚡ Specialized E-Ink Auto-Converting Feeds (On-Demand):
- **⚡ Xteink X4**: `http://<your-server-ip>:5000/opds/x4` (480×800 resolution, grayscale dithering, font-stripped)
- **⚡ Xteink X3**: `http://<your-server-ip>:5000/opds/x3` (528×792 resolution, grayscale dithering, font-stripped)
- **⚡ Kindle**: `http://<your-server-ip>:5000/opds/kindle` (1072×1448 resolution, grayscale, font-stripped)
- **⚡ Kobo**: `http://<your-server-ip>:5000/opds/kobo` (1264×1680 resolution, grayscale, font-stripped)
- **⚡ Generic E-Ink**: `http://<your-server-ip>:5000/opds/eink` (1200×1600 resolution, grayscale, font-stripped)

### Connecting KOReader / Xteink / Thorium / Panels:
1. Open your e-reader app and add your chosen OPDS catalog URL (e.g. `http://<server-ip>:5000/opds/x4`).
2. When prompted, enter your Aarkib **Username** and **Password** (HTTP Basic Auth).
3. Browse, download optimized EPUBs, and synchronize reading positions automatically!

---

## 🎬 Video & Audio Support

Aarkib has expanded from books and comics into a full-featured personal media server:

- **Movies & TV Shows (Implemented)**:
  - Supports `.mp4`, `.mkv`, `.webm`, `.avi`, `.mov`, and `.m4v`.
  - HTTP 206 byte-range seeking for smooth video playback and random seeking.
  - Automated TV show detection (`S01E02` / `1x02`) and movie naming parsing.
  - Pure-Python MP4 container metadata parser (extracts runtime duration, width, height without requiring ffmpeg).
  - Responsive in-browser HTML5 video player with playback position resume, keyboard shortcuts (Space, Arrow keys, Fullscreen, Mute), playback speed selection (0.75x–2.0x), and next-episode autoplay countdown.
- **Audiobooks & Music (Implemented & Expanding)**:
  - Supports `.mp3`, `.m4b`, `.flac`, `.aac`, and `.wav`.
  - Dedicated in-browser HTML5 audio player (`/reader/audio/<id>`) with album artwork, playback speed controls ($0.75\times$–$2.0\times$), track scrubber, and automatic position resume.
  - Pure-Python ID3v2, FLAC, and WAV audio tag and embedded cover art extraction.
  - Planned: M4B chapter mark navigation, narrator metadata, user playlists, and favorites.
- **Unified Media Progression**:
  - Continuous position markers and completion status across both books (CFI / page indices) and AV media (millisecond timestamps).

---

## 🔌 REST API Endpoints

Aarkib exposes clean, unified REST APIs across all media types:

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/api/media` | `GET` | List catalog items (supports `q`, `media_type`, `library`, `sort_by`, `page`). |
| `/api/media/<id>` | `GET` | Retrieve full item details and user progress. |
| `/api/media/<id>/file` | `GET` | Stream or download original media file (supports HTTP 206 byte-ranges). |
| `/api/media/<id>/cover` | `GET` | Retrieve cached WebP cover/poster image. |
| `/api/media/<id>/progress` | `GET`, `POST`| Fetch or update playback / reading progress. |
| `/api/media/<id>` | `PATCH` | Canonical RESTful metadata edit (title, creators, series, tags, locked fields). |
| `/api/media/<id>/edit` | `POST` | Update metadata via form/JSON. |
| `/api/media/<id>/stream/info` | `GET` | Probe media codecs, technical streams, and playback compatibility. |
| `/api/media/<id>/stream/remux` | `GET` | Direct remux pipeline for video playback. |
| `/api/media/<id>/stream/hls/master.m3u8` | `GET` | HLS adaptive bitrate master playlist. |
| `/api/libraries` | `GET`, `POST` | List all configured media folders or add a new folder with custom `media_type`. |
| `/api/libraries/<id>` | `GET`, `PUT`, `DELETE` | View, update `media_type` / name, or remove media folder. |
| `/api/libraries/<id>/scan`| `POST` | Trigger targeted rescan of a specific media folder. |
| `/api/libraries/enrich` | `POST` | Enrich catalog items across configured media folders. |
| `/api/media/<id>/enrich` | `POST` | Enrich a single media item with online metadata. |
| `/api/settings` | `GET`, `PATCH` | Retrieve or dynamically update runtime preferences (auto-scan, watcher, enrichment, page size). |
| `/api/settings/reset` | `POST` | Reset runtime settings to environment defaults. |
| `/api/favorites` | `GET` | List favorited media items for the authenticated user. |
| `/api/playlists` | `GET`, `POST` | View or create media playlists. |
| `/api/health` | `GET` | Healthcheck monitoring endpoint (`{"status": "healthy"}`). |

---

## ⚙️ Web UI Administration

Aarkib is engineered for unified, web-first administration. All management tasks are organized into dedicated category pages under **Settings**:

- **First-Run Setup Wizard (`/auth/setup`)**: On first launch, navigating to the server prompts you to create the initial **Administrator** account (password is compulsory). Once configured, the setup wizard is permanently locked.
- **System Preferences (`/settings/system`)**: Fine-tune startup library scans, real-time filesystem watcher (`watchdog`), automatic metadata enrichment, preferred online providers, and catalog page size dynamically without restarting the server.
- **User Management (`/settings/users`)**: Administrator dashboard to create new accounts (compulsory password for admins; passwordless optional for readers), toggle Administrator/Reader roles, reset passwords, or remove accounts. Public registration is disabled.
- **Media Folders & Libraries (`/settings/libraries`)**: Configure media storage paths, set media types (books, comics, video, mixed), and trigger targeted folder rescans.
- **Plugins & Protocols (`/settings/plugins`)**: View active media format plugins, OPDS 1.2/2.0 feed URLs, Subsonic API compatibility endpoints, and device optimizer presets.
- **Background Tasks & Indexing (`/settings/jobs`)**: Monitor asynchronous library scanner and enrichment jobs, trigger full rescans, or rebuild the SQLite FTS5 search index.

---

## 🧪 Testing & Code Quality

```bash
# Run pytest test suite (168 tests)
uv run pytest

# Check code quality & formatting with ruff
uv run ruff check .
uv run ruff format --check .

# Auto-fix linting and formatting
uv run ruff check --fix .
uv run ruff format .
```

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.
