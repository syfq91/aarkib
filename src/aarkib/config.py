from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent

PRIMARY_DIR_VARS: tuple[str, ...] = ("AARKIB_MEDIA_DIR",)

NUMBERED_DIR_REGEX = re.compile(r"^AARKIB_MEDIA_DIR_?(\d+)$", re.IGNORECASE)


def get_env_media_dirs(env: dict[str, str] | None = None) -> list[Path]:
    """Collects all media directory paths explicitly declared in environment variables.

    Supports:
    - Canonical: AARKIB_MEDIA_DIR (single path)
    - Numbered: AARKIB_MEDIA_DIR1, AARKIB_MEDIA_DIR2, AARKIB_MEDIA_DIR_1, etc.
    """
    target_env = os.environ if env is None else env
    collected_raw: list[str] = []

    # 1. Primary vars in specified order
    for var in PRIMARY_DIR_VARS:
        if var in target_env and target_env[var].strip():
            collected_raw.append(target_env[var].strip().strip("'\""))

    # 2. Numbered variables sorted by integer index
    numbered_matches: list[tuple[int, str, str]] = []

    for key, value in target_env.items():
        if not value or not value.strip():
            continue
        if key.upper() in PRIMARY_DIR_VARS:
            continue

        num_m = NUMBERED_DIR_REGEX.match(key)
        if num_m and num_m.group(1).isdigit():
            index = int(num_m.group(1))
            numbered_matches.append((index, key, value.strip().strip("'\"")))

    # Sort numbered matches: first by index (1, 2, ...), then key name
    numbered_matches.sort(key=lambda x: (x[0], x[1]))
    for _, _, val in numbered_matches:
        collected_raw.append(val)

    parsed_paths: list[Path] = []
    seen: set[str] = set()

    for item in collected_raw:
        if not item:
            continue
        path_obj = Path(item).expanduser()
        try:
            norm_key = str(path_obj.resolve())
        except Exception:
            norm_key = str(path_obj)

        if norm_key not in seen:
            seen.add(norm_key)
            parsed_paths.append(path_obj)

    return parsed_paths


get_env_library_dirs = get_env_media_dirs


def discover_media_dirs(
    data_dir: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> list[Path]:
    """Discovers all media directories from environment variables or returns default data/media."""
    env_paths = get_env_media_dirs(env=env)
    if env_paths:
        return env_paths

    data_path = Path(data_dir) if data_dir else (BASE_DIR / "data")
    return [data_path / "media"]


discover_library_dirs = discover_media_dirs


def get_ffmpeg_binary(app_config: dict[str, Any] | None = None) -> str | None:
    """Returns the executable path for ffmpeg or None if not available."""
    configured: str | None = None
    if app_config:
        configured = app_config.get("FFMPEG_PATH")
    if not configured:
        configured = os.getenv("AARKIB_FFMPEG_PATH") or "ffmpeg"
    if not configured:
        return None
    found = shutil.which(configured)
    if found:
        return found
    if os.path.isfile(configured) and os.access(configured, os.X_OK):
        return configured
    return None


def get_ffprobe_binary(app_config: dict[str, Any] | None = None) -> str | None:
    """Returns the executable path for ffprobe or None if not available."""
    configured: str | None = None
    if app_config:
        configured = app_config.get("FFPROBE_PATH")
    if not configured:
        configured = os.getenv("AARKIB_FFPROBE_PATH") or "ffprobe"
    if not configured:
        return None
    found = shutil.which(configured)
    if found:
        return found
    if os.path.isfile(configured) and os.access(configured, os.X_OK):
        return configured
    return None


def resolve_secret_key(data_dir: Path) -> str:
    """Resolve or generate a secret key with atomic 0600 permissions.

    - If the SECRET_KEY env var is set to a non-default value, it is used as-is.
    - If it is set to the old known-insecure default, we refuse to start.
    - Otherwise a random key is generated and persisted to data_dir with 0600
      permissions so that sessions survive restarts.
    """
    env_secret = os.getenv("SECRET_KEY")
    if env_secret and env_secret != "aarkib-secret-key-change-in-production":
        return env_secret
    if env_secret:
        raise RuntimeError(
            "SECRET_KEY is set to the known-insecure default "
            "'aarkib-secret-key-change-in-production'. Please set a unique "
            "SECRET_KEY environment variable, e.g. via "
            '`python -c "import secrets; print(secrets.token_hex(32))"`.'
        )

    key_path = data_dir / "secret_key"
    try:
        if key_path.is_file():
            return key_path.read_text().strip()
    except OSError:
        pass

    import secrets

    generated = secrets.token_hex(32)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(generated)
        return generated
    except FileExistsError:
        try:
            return key_path.read_text().strip()
        except OSError:
            return generated
    except OSError:
        return generated


class Config:
    """Base configuration with safe defaults."""

    DATA_DIR: Path = Path(os.getenv("AARKIB_DATA_DIR", BASE_DIR / "data"))
    SECRET_KEY: str = resolve_secret_key(DATA_DIR)
    MEDIA_DIRS: list[Path] = discover_media_dirs(DATA_DIR)
    MEDIA_DIR: Path = MEDIA_DIRS[0] if MEDIA_DIRS else (DATA_DIR / "media")
    LIBRARY_DIRS: list[Path] = MEDIA_DIRS
    LIBRARY_DIR: Path = MEDIA_DIR
    COVERS_DIR: Path = Path(os.getenv("AARKIB_COVERS_DIR", DATA_DIR / "covers"))
    OPTIMIZED_DIR: Path = Path(
        os.getenv("AARKIB_OPTIMIZED_DIR", DATA_DIR / "optimized")
    )
    TRANSCODE_DIR: Path = Path(
        os.getenv("AARKIB_TRANSCODE_DIR", DATA_DIR / "transcode")
    )
    BACKUP_DIR: Path = Path(os.getenv("AARKIB_BACKUP_DIR", DATA_DIR / "backups"))
    VAAPI_DEVICE: str | None = os.getenv("AARKIB_VAAPI_DEVICE")
    TRANSCODE_BACKEND: str = os.getenv("AARKIB_TRANSCODE_BACKEND", "auto")
    FFMPEG_PATH: str = os.getenv("AARKIB_FFMPEG_PATH", "ffmpeg")
    FFPROBE_PATH: str = os.getenv("AARKIB_FFPROBE_PATH", "ffprobe")

    # Database
    SQLALCHEMY_DATABASE_URI: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{DATA_DIR / 'aarkib.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # App Settings (Defaults managed via WebUI & database)
    AUTO_SCAN_ON_START: bool = True
    WATCH_LIBRARY: bool = True
    AUTO_ENRICH: bool = False
    METADATA_PROVIDER: str = "all"
    TMDB_API_KEY: str | None = os.getenv(
        "AARKIB_TMDB_API_KEY", os.getenv("TMDB_API_KEY")
    )
    COMICVINE_API_KEY: str | None = os.getenv(
        "AARKIB_COMICVINE_API_KEY", os.getenv("COMICVINE_API_KEY")
    )
    PODCASTINDEX_API_KEY: str | None = os.getenv(
        "AARKIB_PODCASTINDEX_API_KEY", os.getenv("PODCASTINDEX_API_KEY")
    )
    PODCASTINDEX_API_SECRET: str | None = os.getenv(
        "AARKIB_PODCASTINDEX_API_SECRET", os.getenv("PODCASTINDEX_API_SECRET")
    )
    GOOGLE_BOOKS_API_KEY: str | None = os.getenv(
        "AARKIB_GOOGLE_BOOKS_API_KEY", os.getenv("GOOGLE_BOOKS_API_KEY")
    )
    METADATA_CACHE_TTL_DAYS: int = int(
        os.getenv("AARKIB_METADATA_CACHE_TTL_DAYS", "30")
    )
    MUSICBRAINZ_RATE_LIMIT: float = float(
        os.getenv("AARKIB_MUSICBRAINZ_RATE_LIMIT", "1.0")
    )
    PAGE_SIZE: int = 24

    # Scheduled Maintenance & Automation
    BACKUP_SCHEDULE: str = os.getenv("AARKIB_BACKUP_SCHEDULE", "disabled").lower()
    BACKUP_RETENTION_COUNT: int = int(os.getenv("AARKIB_BACKUP_RETENTION_COUNT", "7"))
    PERIODIC_RESCAN_HOURS: int = int(os.getenv("AARKIB_PERIODIC_RESCAN_HOURS", "0"))
    CACHE_REAP_HOURS: int = int(os.getenv("AARKIB_CACHE_REAP_HOURS", "24"))

    # Plugin & Feature Toggles
    ENABLE_OPDS: bool = (os.getenv("AARKIB_ENABLE_OPDS", "true")).lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    ENABLE_SUBSONIC: bool = (os.getenv("AARKIB_ENABLE_SUBSONIC", "true")).lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    ENABLE_JELLYFIN: bool = (os.getenv("AARKIB_ENABLE_JELLYFIN", "true")).lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    ENABLE_EINK_OPTIMIZER: bool = (
        os.getenv("AARKIB_ENABLE_EINK_OPTIMIZER", "true")
    ).lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    ENABLE_BOOKS: bool = (os.getenv("AARKIB_ENABLE_BOOKS", "true")).lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    ENABLE_VIDEO: bool = (os.getenv("AARKIB_ENABLE_VIDEO", "true")).lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    ENABLE_AUDIO: bool = (os.getenv("AARKIB_ENABLE_AUDIO", "true")).lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    ENABLE_AUDIOBOOK: bool = (os.getenv("AARKIB_ENABLE_AUDIOBOOK", "true")).lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    ENABLE_PODCAST: bool = (os.getenv("AARKIB_ENABLE_PODCAST", "true")).lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    ENABLE_MUSIC: bool = (os.getenv("AARKIB_ENABLE_MUSIC", "true")).lower() in (
        "true",
        "1",
        "yes",
        "on",
    )

    # Security & Authentication Controls
    ALLOW_PASSWORDLESS_REMOTE: bool = (
        os.getenv("AARKIB_ALLOW_PASSWORDLESS_REMOTE", "false")
    ).lower() in ("true", "1", "yes", "on")
    AUTH_RATE_LIMIT_ENABLED: bool = (
        os.getenv("AARKIB_AUTH_RATE_LIMIT_ENABLED", "true")
    ).lower() in ("true", "1", "yes", "on")
    AUTH_RATE_LIMIT_MAX_ATTEMPTS: int = int(
        os.getenv("AARKIB_AUTH_RATE_LIMIT_MAX_ATTEMPTS", "5")
    )
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = int(
        os.getenv("AARKIB_AUTH_RATE_LIMIT_WINDOW_SECONDS", "60")
    )

    # Maximum request payload limit (16 MB)
    MAX_CONTENT_LENGTH: int = 16 * 1024 * 1024

    # Logging Format ('text' or 'json')
    LOG_FORMAT: str = os.getenv("AARKIB_LOG_FORMAT", "text").lower()


class TestConfig(Config):
    """Test configuration."""

    TESTING: bool = True
    SQLALCHEMY_DATABASE_URI: str = "sqlite:///:memory:"
    AUTO_SCAN_ON_START: bool = False
    WATCH_LIBRARY: bool = False
    AUTO_ENRICH: bool = False
    AUTH_RATE_LIMIT_ENABLED: bool = False
    BACKUP_SCHEDULE: str = "disabled"
    PERIODIC_RESCAN_HOURS: int = 0
    # CSRF is disabled in tests so request payloads don't need tokens.
    WTF_CSRF_ENABLED: bool = False


class ProductionConfig(Config):
    """Production configuration with stricter security defaults."""

    DEBUG: bool = False
    TESTING: bool = False
    SESSION_COOKIE_SECURE: bool = True
    PERMANENT_SESSION_LIFETIME: int = 3600 * 8  # 8 hours
