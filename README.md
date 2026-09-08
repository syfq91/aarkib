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
- 🎵 **Audiobooks & Music (WIP / In Progress)**: Foundational architecture (`AudioTrackMixin`, `MediaType.AUDIO`) in progress for audio media (`.mp3`, `.m4b`, `.flac`, `.aac`) with duration, bitrate, album, and listening progress synchronization.
- 🔌 **Extensible Media Plugin System**: Pluggable architecture (`MediaPlugin` & `PluginRegistry`) allowing modular media parsers, artwork extractors, and in-browser player routes for different media types.
- 📡 **OPDS 1.2 & OPDS 2.0 Feeds**: Full OPDS catalog feeds compatible with e-readers like KOReader, Thorium Reader, Cantook, Panels, and Moon+ Reader.
- 🔄 **OPDS Progression 1.0**: Built-in support for the latest [OPDS Progression 1.0](https://github.com/opds-community/drafts/blob/main/opds-progression-1.0.md) standard to sync reading positions across devices with conflict resolution.
- 🌐 **In-Browser Web Readers & Players**:
  - **EPUB Web Reader**: Fast in-memory array buffer decoding via ePub.js & JSZip, with themes (Dark, Sepia, OLED, Light), font sizing, and bookmarking.
  - **CBZ Comic Reader**: Smooth canvas & image viewer with continuous scroll, single-page flip, zoom, reading direction (LTR/RTL), page spreads, and fullscreen support.
  - **HTML5 Video Player**: Clean player with position resume, keyboard shortcuts (Space, Arrow keys, Fullscreen, Mute), playback speed selector (0.75x–2.0x), and automatic next-episode countdown.
- 👥 **Multi-User & Role Management**:
  - Isolated reading progress, bookmarks, and statistics per user.
  - Admin dashboard to manage users, reset passwords, and toggle roles.
  - Required login mode by default (`AARKIB_AUTH_REQUIRED=true`) with first-user admin bootstrapping.
- 🎨 **Modern Responsive UI / PWA**:
  - Clean top header navigation bar with user avatar menu.
  - Dedicated mobile bottom navigation bar on mobile devices.
  - Dark, Light, and OLED themes with persistent state.
  - Installable Progressive Web App (PWA) with offline asset caching.
- ✨ **Metadata Enrichment & Manual Editor**:
  - Auto-enrich books using Google Books & Open Library APIs.
  - Interactive "✏️ Edit Book & Series" metadata modal on book details.
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
mkdir -p data/books data/covers

# 4. Copy environment configuration
cp .env.example .env

# 5. Start the server
uv run aarkib
```

Visit **`http://localhost:5000`** in your browser. On your first visit, you will be prompted to register the **Administrator** account.

---

### Option B: Running with Docker Compose

```bash
# Start container in background
docker compose up -d
```

Your library books placed in `./data/books` will be mounted automatically.

---

## ⚙️ Configuration (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SECRET_KEY` | `aarkib-secret-key-change-in-production` | Secret key for session security & signing. |
| `AARKIB_DATA_DIR` | `./data` | Base storage directory. |
| `AARKIB_MEDIA_DIR` / `AARKIB_LIBRARY_DIR` | `./data/media` or `./data/books` | Primary media folder (or delimited list: `/dir1:/dir2`). |
| `AARKIB_MEDIA_DIRS` / `MEDIA_DIRS` | *(none)* | Delimited list of multiple media directories. |
| `AARKIB_MEDIA_DIR1`, `DIR2`, ... | *(none)* | Additional numbered media folders (`AARKIB_MEDIA_DIR1`, `MEDIA_DIR2`, `DIR1`, etc.). |
| `AARKIB_COVERS_DIR` | `./data/covers` | Storage directory for extracted cover art. |
| `AARKIB_OPTIMIZED_DIR` | `./data/optimized` | Cache directory for on-demand e-ink optimized EPUBs. |
| `DATABASE_URL` | `sqlite:///data/aarkib.db` | SQLAlchemy database URI. |
| `AARKIB_AUTH_REQUIRED` | `true` | When `true`, visitors must log in to browse or download. |
| `AARKIB_ALLOW_REGISTRATION` | `true` | Allow new readers to sign up from the web UI. |
| `AARKIB_AUTO_SCAN` | `true` | Automatically scan library on startup. |
| `AARKIB_WATCH_LIBRARY` | `true` | Watch library for filesystem changes. |
| `AARKIB_AUTO_ENRICH` | `false` | Automatically fetch metadata on library scan. |
| `AARKIB_METADATA_PROVIDER` | `all` | Online enrichment provider: `googlebooks`, `openlibrary`, or `all`. |
| `AARKIB_PAGE_SIZE` | `24` | Number of items per page in UI views. |
| `FLASK_DEBUG` | `0` | Enable Flask development debugger (disabled by default in production). |
| `PORT` | `5000` | Server listening port. |

> **💡 Generic Media Folders & WebUI Configuration**:
> - **WebUI Media Folders Management**: Head to **Settings → 📁 Media Folders & Libraries** in the web interface to view all folders, add new media directories, and customize each folder's media type:
>   - **Mixed / Auto-detect (`all`)**: Automatically detects books (`.epub`), comics/manga (`.cbz`, `.cbr`, `.zip`), and videos (`.mp4`, `.mkv`, etc.).
>   - **Books Only (`book`)**: Catalogs files inside as books.
>   - **Comics & Manga (`comic`)**: Catalogs files inside as comics/manga.
>   - **Movies & TV Shows (`video`)**: Catalogs video media files inside as movies & shows.
> - **Environment Configuration**: You can also declare folders in `.env` using `AARKIB_MEDIA_DIR=/media/storage`, numbered variables like `AARKIB_MEDIA_DIR1=/mnt/nas/books`, `AARKIB_MEDIA_DIR2=/media/manga`, `AARKIB_MEDIA_DIR3=/media/movies` (or `MEDIA_DIR1`, `DIR1`), or named variables like `AARKIB_MEDIA_DIR_MANGA=/manga`.
> - *(Legacy `AARKIB_LIBRARY_DIR` and `BUUKUU_*` variables are fully supported for backward compatibility).*

---

## 📡 OPDS Catalog & E-Reader Setup

Aarkib exposes standard OPDS feeds as well as on-demand auto-optimizing feeds for e-ink devices (strips bloat fonts, converts images to grayscale/dithered e-ink format, and compresses on the fly without modifying original files):

### Standard Feeds:
- **OPDS 1.2 Feed (Atom)**: `http://<your-server-ip>:5000/opds`
- **OPDS 2.0 Feed (JSON)**: `http://<your-server-ip>:5000/opds/v2/catalog.json`
- **OPDS Authentication Document**: `http://<your-server-ip>:5000/opds/authentication.json`
- **OPDS Progression 1.0 Endpoint**: `http://<your-server-ip>:5000/opds/books/<id>/progression`

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

## 🎧 Video & Audio Support (Work In Progress)

Aarkib is actively expanding beyond books and comics into a unified personal media server. The multi-media data model and plugin layer are designed for seamless extension:

- **Media Plugin Interface** (`MediaPlugin`): Encapsulates file extension matching, metadata parsing, artwork extraction, and web player routing.
- **Audio Track Data Model** (`AudioTrackMixin`): Pre-defined schema columns for duration, bitrate, album, track number, and disc number.
- **Video Item Data Model** (`VideoItemMixin`): Pre-defined schema columns for video runtime duration, resolution dimensions, video codec, season, and episode indexing.
- **Unified Progression**: The progression database schema stores continuous position markers and completion status across both books (CFI / page indices) and AV media (millisecond timestamps).

---

## 🛠️ CLI Commands

Aarkib includes a CLI for server administration:

```bash
# Scan and index books in data/books
uv run aarkib scan

# Scan library and auto-fetch metadata from Google Books / Open Library
uv run aarkib scan --enrich

# Fetch online metadata for all indexed books
uv run aarkib enrich

# Create a new reader or admin user
uv run aarkib create-user --username alice --password secret123 --admin

# List all registered users
uv run aarkib list-users
```

---

## 🧪 Testing & Code Quality

```bash
# Run pytest test suite (61+ tests)
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
