# 🏛️ Aarkib

> Your self-hosted personal media server for books, comics, movies, TV shows, music, audiobooks, and podcasts.

Read, watch, and listen anywhere — in your web browser, on your e-reader (KOReader, Kindle, Kobo), or through native mobile streaming apps (Symfonium, Plappa, DSub).

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![OPDS](https://img.shields.io/badge/OPDS-1.2%20%7C%202.0%20%7C%20Progression-green.svg)](https://opds.io/)
[![Subsonic](https://img.shields.io/badge/Subsonic-API%20Compatible-blue.svg)](http://www.subsonic.org/pages/api.jsp)
[![Jellyfin](https://img.shields.io/badge/Jellyfin-Client%20Compatible-purple.svg)](https://jellyfin.org/)

---

## ✨ Features

- 📖 **E-Books, Comics & Documents**: Read `.epub`, `.pdf`, `.cbz`, `.cbr`, and `.zip` files directly in your browser or on your favorite e-reader. Includes dedicated PDF web reader, customizable themes (Dark, Sepia, OLED, Light), font sizing, bookmarks, continuous vertical scroll, and two-page spread modes.
- 🎬 **Movies & TV Shows**: Stream video files (`.mp4`, `.mkv`, `.webm`, etc.) with instant seeking, automatic TV show detection (`S01E02`), resume playback, and next-episode autoplay.
- 🎵 **Audiobooks & Music**: Listen in-browser with album artwork, playback speed controls ($0.75\times$–$2.0\times$), track scrubbing, and saved listening positions.
- 🎙️ **Podcasts**: Organize audio shows, import OPML subscriptions, stream episodes, and search online directory details.
- 📱 **Connect Your Devices**:
  - **E-Readers (OPDS)**: Connect KOReader, Moon+ Reader, Thorium, or Panels to browse, download, and synchronize reading positions across devices.
  - **E-Ink Optimization**: Automatic on-the-fly book optimization tailored for Kindle, Kobo, and e-ink displays (grayscale dithering and font stripping for faster page turns).
  - **Mobile Audio Apps (Subsonic)**: Stream music and audiobooks to native apps like Symfonium, DSub, Ultrasonic, and Plappa.
  - **TV & Mobile Streaming Apps (Jellyfin)**: Connect official and third-party Jellyfin clients (Jellyfin Mobile for Android/iOS, Android TV, Swiftfin, Findroid, Jellyfin Media Player) to browse and stream movies, TV shows, and music.
- 🎨 **Modern Web App & PWA**: Responsive interface with Dark, Light, and OLED themes. Installable on phones and tablets as a Progressive Web App.
- 👥 **Multi-User**: Dedicated accounts for family members with individual reading progress, bookmarks, and viewing history.
- 🔍 **Automatic Metadata & Cover Art**: Automatically fetch covers, summaries, series numbering, and details from Google Books, Open Library, ComicVine, TMDB, and MusicBrainz, with per-library customization and a built-in metadata editor.

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
    devices:
      - /dev/dri:/dev/dri # Hardware acceleration for Intel & AMD video transcoding
    group_add:
      - video
      - render
```

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

---

## ⚙️ Web Settings & Management

All server management is handled directly through the web interface under **Settings**:

- **📁 Media Folders**: Add new folders, browse mounted directories, select media types, and trigger library rescans.
- **👥 Users**: Create family accounts, assign administrator or reader roles, and manage passwords.
- **⚙️ System Preferences**: Configure startup auto-scanning, real-time filesystem watchers, and automatic metadata enrichment.
- **🔌 Plugins & Integrations**: View available media formats and access OPDS and Subsonic connection endpoints.
- **⚡ Background Tasks**: Monitor active library scans, enrichment jobs, and search index status.

---

## 🛠️ Architecture & Developer Guidelines

- [**`ARCHITECTURE.md`**](ARCHITECTURE.md): System architecture, core subsystems, data models, ER diagrams, and technical design.
- [**`DESIGN.md`**](DESIGN.md): Visual identity, design tokens, and UI/UX design system (Cinematic Obsidian).
- [**`AGENTS.md`**](AGENTS.md): Engineering standards, safety invariants, and developer guidelines for AI agents and human contributors.

---

## 📄 License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.
