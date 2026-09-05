# 📚 Buukuu

> A modern, lightweight, self-hosted book & comic server built with Flask, SQLite, and PWA capabilities. Provides OPDS 1.2, OPDS 2.0, and **OPDS Progression 1.0** reading sync, full multi-user authentication, online metadata enrichment, and browser-based EPUB & CBZ readers.

[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![OPDS: Progression 1.0](https://img.shields.io/badge/OPDS-Progression%201.0-green.svg)](https://github.com/opds-community/drafts/blob/main/opds-progression-1.0.md)

---

## ✨ Features

- 📖 **E-Books & Comics**: Native support for `.epub` and `.cbz` formats with automatic metadata & cover extraction.
- 📡 **OPDS 1.2 & OPDS 2.0 Feeds**: Full OPDS catalog feeds compatible with e-readers like KOReader, Thorium Reader, Cantook, Panels, and Moon+ Reader.
- 🔄 **OPDS Progression 1.0**: Built-in support for the latest [OPDS Progression 1.0](https://github.com/opds-community/drafts/blob/main/opds-progression-1.0.md) standard to sync reading positions across devices with conflict resolution.
- 🌐 **In-Browser Web Readers**:
  - **EPUB Web Reader**: Fast in-memory array buffer decoding via ePub.js & JSZip, with themes (Dark, Sepia, OLED, Light), font sizing, and bookmarking.
  - **CBZ Comic Reader**: Smooth canvas & image viewer with continuous scroll, single-page flip, zoom, and fullscreen support.
- 👥 **Multi-User & Role Management**:
  - Isolated reading progress, bookmarks, and statistics per user.
  - Admin dashboard to manage users, reset passwords, and toggle roles.
  - Required login mode by default (`BUUKUU_AUTH_REQUIRED=true`) with first-user admin bootstrapping.
- 🎨 **Modern Responsive UI / PWA**:
  - Clean top header navigation bar with user avatar menu.
  - Dedicated mobile bottom navigation bar on mobile devices.
  - Dark, Light, and OLED themes with persistent state.
  - Installable Progressive Web App (PWA) with offline asset caching.
- ✨ **Metadata Enrichment & Manual Editor**:
  - Auto-enrich books using Google Books & Open Library APIs.
  - Interactive "✏️ Edit Book & Series" metadata modal on book details.
  - Smart automatic series volume detection from filenames and EPUB 3 / Calibre OPF tags.
- 🐳 **Docker & Production Ready**: Docker & Docker Compose setup with persistent SQLite WAL storage.

---

## 🚀 Quick Start

### Option A: Running with `uv` (Local Development)

```bash
# 1. Clone the repository
git clone https://github.com/syafiqq21/buukuu.git
cd buukuu

# 2. Sync dependencies
uv sync

# 3. Create your storage folders
mkdir -p data/books data/covers

# 4. Copy environment configuration
cp .env.example .env

# 5. Start the server
uv run buukuu
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
| `SECRET_KEY` | `buukuu-secret-key-change-in-production` | Secret key for session security & signing. |
| `BUUKUU_DATA_DIR` | `./data` | Base storage directory. |
| `BUUKUU_LIBRARY_DIR` | `./data/books` | Primary library directory (or delimited list: `/dir1:/dir2`). |
| `BUUKUU_LIBRARY_DIR1`, `DIR2`, ... | *(none)* | Additional numbered library directories (`BUUKUU_LIBRARY_DIR1`, `BUUKUU_LIBRARY_DIR2`, `DIR1`, etc.). |
| `BUUKUU_COVERS_DIR` | `./data/covers` | Storage directory for extracted cover art. |
| `DATABASE_URL` | `sqlite:///data/buukuu.db` | SQLAlchemy database URI. |
| `BUUKUU_AUTH_REQUIRED` | `true` | When `true`, visitors must log in to browse or download. |
| `BUUKUU_ALLOW_REGISTRATION` | `true` | Allow new readers to sign up from the web UI. |
| `BUUKUU_AUTO_SCAN` | `true` | Automatically scan library on startup. |
| `BUUKUU_WATCH_LIBRARY` | `true` | Watch library for filesystem changes. |
| `BUUKUU_AUTO_ENRICH` | `false` | Automatically fetch metadata on library scan. |
| `BUUKUU_METADATA_PROVIDER` | `all` | Online enrichment provider: `googlebooks`, `openlibrary`, or `all`. |
| `BUUKUU_PAGE_SIZE` | `24` | Number of books per page in UI views. |
| `PORT` | `5000` | Server listening port. |

> **💡 Multiple Library Folders**: You can specify multiple folders in `.env` using numbered variables like `BUUKUU_LIBRARY_DIR1=/mnt/nas/books`, `BUUKUU_LIBRARY_DIR2=/media/manga`, `BUUKUU_LIBRARY_DIR3=/home/user/calibre` (or `DIR1`, `DIR2`), or as a delimited list in `BUUKUU_LIBRARY_DIR=/books:/manga`.

---

## 📡 OPDS Catalog & E-Reader Setup

Buukuu exposes standard OPDS feeds as well as on-demand auto-optimizing feeds for e-ink devices (strips bloat fonts, converts images to grayscale/dithered e-ink format, and compresses on the fly without modifying original files):

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
2. When prompted, enter your Buukuu **Username** and **Password** (HTTP Basic Auth).
3. Browse, download optimized EPUBs, and synchronize reading positions automatically!

---

## 🛠️ CLI Commands

Buukuu includes a CLI for server administration:

```bash
# Scan and index books in data/books
uv run buukuu scan

# Scan library and auto-fetch metadata from Google Books / Open Library
uv run buukuu scan --enrich

# Fetch online metadata for all indexed books
uv run buukuu enrich

# Create a new reader or admin user
uv run buukuu create-user --username alice --password secret123 --admin

# List all registered users
uv run buukuu list-users
```

---

## 🧪 Testing & Code Quality

```bash
# Run pytest test suite (28+ tests)
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
