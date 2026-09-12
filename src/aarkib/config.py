from __future__ import annotations

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

PRIMARY_DIR_VARS: tuple[str, ...] = ("AARKIB_MEDIA_DIR",)

NUMBERED_DIR_REGEX = re.compile(r"^AARKIB_MEDIA_DIR_?(\d+)$", re.IGNORECASE)
NAMED_DIR_REGEX = re.compile(r"^AARKIB_MEDIA_DIR_([A-Za-z0-9_]+)$", re.IGNORECASE)


def split_path_string(val: str) -> list[str]:
    """Splits a delimited string into distinct path strings.

    Supports delimiters: newline, semicolon, comma, and colon (safe for Windows drive letters).
    """
    if not val or not isinstance(val, str):
        return []
    val = val.strip()
    if not val:
        return []

    # Handle newlines
    if "\n" in val:
        results: list[str] = []
        for line in val.splitlines():
            results.extend(split_path_string(line))
        return results

    # Handle semicolon
    if ";" in val:
        parts: list[str] = []
        for p in val.split(";"):
            parts.extend(split_path_string(p))
        return [p for p in parts if p]

    # Handle comma
    if "," in val:
        parts = []
        for p in val.split(","):
            parts.extend(split_path_string(p))
        return [p for p in parts if p]

    # Handle colon (with care for Windows drive letters like C:\ or D:/)
    if ":" in val:
        if len(val) >= 2 and val[1] == ":" and val[0].isalpha():
            parts = [
                p.strip().strip("'\"")
                for p in re.split(r"(?<!^[a-zA-Z]):(?![\\/])", val)
                if p.strip()
            ]
            if len(parts) > 1:
                return parts
            return [val.strip().strip("'\"")]
        else:
            return [p.strip().strip("'\"") for p in val.split(":") if p.strip()]

    return [val.strip().strip("'\"")]


def get_env_media_dirs(env: dict[str, str] | None = None) -> list[Path]:
    """Collects all media directory paths explicitly declared in environment variables.

    Supports:
    - Canonical: AARKIB_MEDIA_DIR (single path or delimited by :, ;, ,, \n)
    - Numbered: AARKIB_MEDIA_DIR1, AARKIB_MEDIA_DIR2, AARKIB_MEDIA_DIR_1, etc.
    - Named categories: AARKIB_MEDIA_DIR_MANGA, AARKIB_MEDIA_DIR_MOVIES, etc.
    """
    target_env = os.environ if env is None else env
    collected_raw: list[str] = []

    # 1. Primary vars in specified order
    for var in PRIMARY_DIR_VARS:
        if var in target_env and target_env[var].strip():
            collected_raw.append(target_env[var])

    # 2. Numbered variables sorted by integer index
    numbered_matches: list[tuple[int, str, str]] = []
    # 3. Named variables sorted alphabetically
    named_matches: list[tuple[str, str]] = []

    for key, value in target_env.items():
        if not value or not value.strip():
            continue
        if key.upper() in PRIMARY_DIR_VARS:
            continue

        num_m = NUMBERED_DIR_REGEX.match(key)
        if num_m and num_m.group(1).isdigit():
            index = int(num_m.group(1))
            numbered_matches.append((index, key, value))
            continue

        named_m = NAMED_DIR_REGEX.match(key)
        if named_m:
            suffix = named_m.group(1).upper()
            if not suffix.isdigit():
                named_matches.append((suffix, value))

    # Sort numbered matches: first by index (1, 2, ...), then key name
    numbered_matches.sort(key=lambda x: (x[0], x[1]))
    for _, _, val in numbered_matches:
        collected_raw.append(val)

    # Sort named matches alphabetically
    named_matches.sort(key=lambda x: x[0])
    for _, val in named_matches:
        collected_raw.append(val)

    parsed_paths: list[Path] = []
    seen: set[str] = set()

    for item in collected_raw:
        for p_str in split_path_string(item):
            if not p_str:
                continue
            path_obj = Path(p_str).expanduser()
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


class Config:
    """Base application configuration."""

    # SECRET_KEY handling:
    # - If the env var is set to a non-default value, it is used as-is.
    # - If it is set to the old known-insecure default, we refuse to start.
    # - Otherwise a random key is generated and persisted to DATA_DIR so that
    #   sessions survive restarts. Operators should always set SECRET_KEY in
    #   production.
    _env_secret = os.getenv("SECRET_KEY")
    if _env_secret and _env_secret != "aarkib-secret-key-change-in-production":
        SECRET_KEY: str = _env_secret
    elif _env_secret:
        raise RuntimeError(
            "SECRET_KEY is set to the known-insecure default "
            "'aarkib-secret-key-change-in-production'. Please set a unique "
            "SECRET_KEY environment variable, e.g. via "
            '`python -c "import secrets; print(secrets.token_hex(32))"`.'
        )

    DATA_DIR: Path = Path(os.getenv("AARKIB_DATA_DIR", BASE_DIR / "data"))
    if "SECRET_KEY" not in locals():
        # Persist a generated key so sessions survive restarts (dev convenience).
        _key_path = DATA_DIR / "secret_key"
        if _key_path.exists():
            SECRET_KEY = _key_path.read_text().strip()
        else:
            import secrets as _secrets

            SECRET_KEY = _secrets.token_hex(32)
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                _key_path.write_text(SECRET_KEY)
                os.chmod(_key_path, 0o600)
            except OSError:
                pass
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
    VAAPI_DEVICE: str | None = os.getenv("AARKIB_VAAPI_DEVICE")

    # Database
    SQLALCHEMY_DATABASE_URI: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{DATA_DIR / 'aarkib.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # App Settings
    AUTH_REQUIRED: bool = (os.getenv("AARKIB_AUTH_REQUIRED", "true")).lower() in (
        "true",
        "1",
        "yes",
    )
    ALLOW_REGISTRATION: bool = (
        os.getenv("AARKIB_ALLOW_REGISTRATION", "true")
    ).lower() in (
        "true",
        "1",
        "yes",
    )
    AUTO_SCAN_ON_START: bool = (os.getenv("AARKIB_AUTO_SCAN", "true")).lower() in (
        "true",
        "1",
        "yes",
    )
    WATCH_LIBRARY: bool = (os.getenv("AARKIB_WATCH_LIBRARY", "true")).lower() in (
        "true",
        "1",
        "yes",
    )
    AUTO_ENRICH: bool = (os.getenv("AARKIB_AUTO_ENRICH", "false")).lower() in (
        "true",
        "1",
        "yes",
    )
    METADATA_PROVIDER: str = os.getenv("AARKIB_METADATA_PROVIDER", "all")
    TMDB_API_KEY: str | None = os.getenv(
        "AARKIB_TMDB_API_KEY", os.getenv("TMDB_API_KEY")
    )
    METADATA_CACHE_TTL_DAYS: int = int(
        os.getenv("AARKIB_METADATA_CACHE_TTL_DAYS", "30")
    )
    MUSICBRAINZ_RATE_LIMIT: float = float(
        os.getenv("AARKIB_MUSICBRAINZ_RATE_LIMIT", "1.0")
    )
    PAGE_SIZE: int = int(os.getenv("AARKIB_PAGE_SIZE", "24"))

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

    # Maximum request payload limit (16 MB)
    MAX_CONTENT_LENGTH: int = 16 * 1024 * 1024


class TestConfig(Config):
    """Test configuration."""

    TESTING: bool = True
    SQLALCHEMY_DATABASE_URI: str = "sqlite:///:memory:"
    AUTH_REQUIRED: bool = False
    AUTO_SCAN_ON_START: bool = False
    WATCH_LIBRARY: bool = False
    AUTO_ENRICH: bool = False
    # CSRF is disabled in tests so request payloads don't need tokens.
    WTF_CSRF_ENABLED: bool = False


class ProductionConfig(Config):
    """Production configuration with stricter security defaults."""

    DEBUG: bool = False
    TESTING: bool = False
    SESSION_COOKIE_SECURE: bool = True
    PERMANENT_SESSION_LIFETIME: int = 3600 * 8  # 8 hours
