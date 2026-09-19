# 🏛️ Aarkib

> Your self-hosted personal media server for books, comics, movies, TV shows, music, audiobooks, and podcasts.

Read, watch, and listen anywhere — in your web browser, on your e-reader (KOReader, Kindle, Kobo), or through native mobile streaming apps (Symfonium, Plappa, DSub).

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![OPDS](https://img.shields.io/badge/OPDS-1.2%20%7C%202.0%20%7C%20Progression-green.svg)](https://opds.io/)
[![Subsonic](https://img.shields.io/badge/Subsonic-API%20Compatible-blue.svg)](http://www.subsonic.org/pages/api.jsp)
[![Jellyfin](https://img.shields.io/badge/Jellyfin-Client%20Compatible-purple.svg)](https://jellyfin.org/)

---

## ✨ Features

- 📖 **E-Books, Comics & Documents**: Read `.epub`, `.pdf`, `.cbz`, `.cbr`, and `.zip` files directly in your browser or on your favorite e-reader. Features a **3D Virtual Bookshelf** (`📦 3D Shelf`) with hardware-accelerated CSS 3D transforms, vertical book spine titles, and interactive hover tilt. Includes dedicated PDF web reader, customizable themes (Dark, Sepia, OLED, Light), font sizing, bookmarks, continuous vertical scroll, and two-page spread modes.
- 🎬 **Movies & TV Shows**: Stream video files (`.mp4`, `.mkv`, `.webm`, etc.) with deterministic playback planning: native Direct Play, on-the-fly Direct Remuxing, and hardware-accelerated transcoding (Intel QuickSync & VA-API) with automatic CPU fallback. Features high-fidelity anime/effects subtitle rendering via the **JASSUB WebAssembly libass engine**, a real-time **Playback Diagnostics HUD** (hotkey `D` or stats button) with forward buffer progress bar, automatic TV show episode parsing (`S01E02`), resume playback, and next-episode autoplay.
- 🎵 **Audiobooks & Music (Lossless Suite)**: Listen in-browser with a responsive **split-stage layout**, ambient artwork backlight glow, an interactive **HTML5 canvas waveform visualizer scrubber** with hover time preview, embedded chapter tick markers with tooltips, animated equalizer bars, playback speed controls ($0.75\times$–$2.5\times$), sleep timer with countdown badges, and saved listening positions.
- 🎙️ **Podcasts**: Organize audio shows, import OPML subscriptions, stream episodes with ambient cover lighting, and search online directory details.
- 🎨 **Modern Web App & PWA**: Responsive interface with Dark, Light, and OLED themes. Features **Category Quick-Filter Pills** (`Books & Comics`, `Audiobooks & Music`, `Movies & TV`), global **`⌘K` / `Ctrl+K` search shortcut**, a **Multi-Select Batch Curator** with floating action dock, and a **Sliding Metadata Inspector** with field lock toggles. Installable on phones and tablets as a Progressive Web App.
- 📱 **Connect Your Devices**:
  - **E-Readers (OPDS)**: Connect KOReader, Moon+ Reader, Thorium, or Panels to browse, download, and synchronize reading positions across devices.
  - **E-Ink Optimization**: Automatic on-the-fly book optimization tailored for Kindle, Kobo, and e-ink displays (grayscale dithering and font stripping for faster page turns).
  - **Mobile Audio Apps (Subsonic)**: Stream music and audiobooks to native apps like Symfonium, DSub, Ultrasonic, and Plappa.
  - **TV & Mobile Streaming Apps (Jellyfin & RFC 8628)**: Connect official and third-party Jellyfin clients (Jellyfin Mobile for Android/iOS, Android TV, Swiftfin, Findroid, Jellyfin Media Player) to browse and stream movies, TV shows, and music. Includes a cinematic **TV Device Pairing screen** (`/pair`) for effortless 10-foot login.
- 🎮 **Gamepad & 10-Foot Navigation**: Seamless out-of-the-box controller support across the entire Web UI, readers (comics, manga, EPUB, PDF), and players (video, audio, podcasts) using standard Xbox, PlayStation, Nintendo Switch, and Steam Deck gamepads. Includes spatial navigation, context-aware HUD button prompts, and haptic vibration feedback.
- 👥 **Multi-User & Family Profiles**: Dedicated accounts with multi-profile support, individual reading progress and bookmarks, kid-safe restrictions (`is_child`), and per-profile library access control lists (ACLs for browsing and downloading) configured through an interactive permission matrix.
- 🛡️ **Mount-Safe Storage Protection**: Intelligent availability guards prevent accidental catalog wipes when external drives or network storage (NFS/SMB) unmount, backed by three-way crawler reconciliation (`NEW`, `CHANGED`, `UNCHANGED`, `MISSING`).
- 🔍 **Automatic Metadata, Matching & Cover Art**: Automatically fetch covers, summaries, series numbering, and details from Google Books, Open Library, ComicVine, TMDB, MusicBrainz, iTunes, and PodcastIndex. Features fuzzy candidate matching (Levenshtein distance & composite confidence scoring), field-level provenance tracking (`AUTOMATIC`, `DERIVED`, `MANUAL`), and manual edit lock protection to safeguard user-curated metadata.
- 🔔 **Notifications & Smart Home**: Push notification dispatch via Apprise (Discord, Telegram, Slack, Webhooks, Pushbullet) with debounced media batching, plus bidirectional MQTT integration with Home Assistant Auto-Discovery.

---

## 🚀 Quick Start with Docker Compose

The recommended way to run Aarkib is with Docker Compose.

### 1. Start the Container

```bash
docker compose up -d
```

Open **`http://localhost:5000`** in your browser. On your first visit, the setup wizard will prompt you to create your **Administrator** account.

### Example `docker-compose.yml`

```yaml
services:
  aarkib:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: aarkib
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ./data:/app/data
      # Mount your host media folders to /media:
      # - /path/to/media/books:/media/books:ro
      # - /path/to/media/comics:/media/comics:ro
      # - /path/to/media/videos:/media/videos:ro
      # - /path/to/media/audiobooks:/media/audiobooks:ro
      # - /path/to/media/music:/media/music:ro
      # - /path/to/media/podcasts:/media/podcasts:ro
    # Hardware acceleration for Intel & AMD video transcoding
    # Set VIDEO_GID and RENDER_GID in .env to match host device permissions:
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "${VIDEO_GID:-video}"
      - "${RENDER_GID:-render}"
```

> [!TIP]
> **Hardware Transcoding**: To enable Intel QuickSync or AMD VA-API hardware acceleration, pass `/dev/dri` and configure your host's numeric video and render group IDs in `.env` (e.g., `VIDEO_GID=44`, `RENDER_GID=990`, found via `getent group video render | cut -d: -f3`). On systems without GPU hardware, comment out `devices` and `group_add`.

---

## 📁 Adding Your Media

You can add media to Aarkib in two ways:

1. **Place files into `./data`**:
   Any files or subfolders placed in the local `./data` folder on your host are automatically accessible inside the container.
2. **Mount external folders (Recommended)**:
   Mount your existing library folders into `/media` in `docker-compose.yml` (for example, `/mnt/storage/books:/media/books:ro`).
   Then open Aarkib, head to **Settings → 📁 Media Folders & Libraries**, click **Browse**, select your folder under **Mounted Media (/media)**, and pick its media type.

---

## 📱 Connecting Your Devices

### E-Readers (KOReader, Moon+ Reader, Thorium, Panels)

Add your Aarkib catalog URL in your reader app:
- **Standard Feed**: `http://<your-server-ip>:5000/opds`
- **E-Ink Optimized Feeds** (automatically strips heavy fonts and converts images to grayscale for faster page turns):
  - **Kindle**: `http://<your-server-ip>:5000/opds/kindle`
  - **Kobo**: `http://<your-server-ip>:5000/opds/kobo`
  - **Xteink**: `http://<your-server-ip>:5000/opds/x4` (or `/x3`)
  - **Generic E-Ink**: `http://<your-server-ip>:5000/opds/eink`

*When prompted by your reader app, sign in with your Aarkib username and password.*

### Mobile Streaming Apps (Symfonium, Plappa, DSub, Ultrasonic)

Connect your favorite Subsonic-compatible mobile app:
- **Server Address**: `http://<your-server-ip>:5000`
- **Username & Password**: Your Aarkib user credentials

### Jellyfin Client Apps (Android TV, Mobile, Swiftfin, Findroid, Desktop)

Connect your favorite Jellyfin-compatible client app:
- **Server Address / Host**: `http://<your-server-ip>:5000`
- **Username & Password**: Your Aarkib user credentials
- **Supported Clients**: Jellyfin Mobile (Android / iOS), Jellyfin Android TV, Swiftfin, Findroid, Jellyfin Media Player, Infuse

### Native Mobile & TV Apps (Versioned REST API v1 & 10-Foot UI)

Aarkib exposes a high-performance REST API designed specifically for custom native mobile (iOS/Android) and TV (Apple TV, Android TV, Fire TV) clients:
- **Clean, Versioned REST API v1 (`/api/v1`)**:
  - `GET /api/v1/media`: Filter catalog items by library, media type, full-text search, and pagination.
  - `GET /api/v1/media/<id>/playback-plan`: Client capability-aware playback planner delivering deterministic delivery descriptors (`DIRECT`, `REMUX`, `TRANSCODE`).
  - `GET /api/v1/libraries` & `POST /api/v1/libraries/<id>/reconcile`: Inspect folders and trigger mount-safe crawler reconciliations.
  - `GET /api/v1/profiles` & `PATCH /api/v1/profiles/<id>`: Manage family profiles and update granular library ACLs.
  - `GET /api/v1/jobs`, `POST /api/v1/jobs/<id>/cancel`, `POST /api/v1/jobs/<id>/retry`: Live asynchronous task monitoring and cooperative control.
- **Direct Login & Scoped Tokens**: `POST /api/auth/login` returns a persistent API Bearer token with optional scoped permissions (`media:read`, `media:write`, `admin`).
- **TV Device Pairing (RFC 8628)**: TV apps request an unambiguous 6-character code via `POST /api/auth/device-code`. The user pairs the TV by opening `http://<your-server-ip>:5000/pair` on their phone or computer.
- **Aggregated Home Feed**: `GET /api/home` provides ready-to-render dashboard rails including *Continue Watching*, *Continue Reading*, *Continue Listening*, *Next Up* (for episodic TV series), *Recently Added*, and *Favorites*.
- **Taxonomy Browsing**: Full creator (`/api/creators`), collection/series (`/api/collections`), and genre tag (`/api/tags`) endpoints with item counts and media filtering.
- **Interactive API Documentation & Explorer**: Explore the OpenAPI 3.1 specification and test endpoints interactively by visiting `http://<your-server-ip>:5000/api/docs` in any browser. Spec available at `/api/openapi.json`.

---

## ⚙️ Web Settings & Management

All server management is handled directly through the web interface under **Settings**:

- **📁 Media Folders**: Add new folders, browse mounted directories, select media types, and trigger mount-safe library rescans.
- **👥 Users & Family Profiles**: Create accounts, manage family member sub-profiles, assign roles, and configure per-library permissions (ACLs) using the interactive Library Access Control modal matrix.
- **⚙️ System Preferences**: Configure hardware transcoding backend (VA-API, Intel QSV, CPU), startup auto-scanning, real-time filesystem watchers, and automatic metadata enrichment.
- **🔌 Plugins & Integrations**: Configure push notifications (Apprise), MQTT / Home Assistant auto-discovery, view available media formats, and access OPDS and Subsonic connection endpoints.
- **⚡ Background Tasks (Live Manager)**: Real-time dashboard card in System Settings displaying active, queued, succeeded, and failed jobs with live progress bars, cooperative cancellation, and retry controls.

---

## 🛠️ Architecture & Developer Guidelines

- [**`ARCHITECTURE.md`**](ARCHITECTURE.md): System architecture, core subsystems, data models, ER diagrams, and technical design.
- [**`DESIGN.md`**](DESIGN.md): Visual identity, design tokens, and UI/UX design system (Cinematic Obsidian).
- [**`AGENTS.md`**](AGENTS.md): Engineering standards, safety invariants, and developer guidelines for AI agents and human contributors.

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.
