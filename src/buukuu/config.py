from __future__ import annotations

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

PRIMARY_DIR_VARS: tuple[str, ...] = (
    "BUUKUU_LIBRARY_DIR",
    "BUUKU_LIBRARY_DIR",
    "BUUKUU_LIBRARY_DIRS",
    "BUUKU_LIBRARY_DIRS",
    "BUUKUU_BOOKS_DIR",
    "BUUKUU_BOOKS_DIRS",
    "LIBRARY_DIR",
    "LIBRARY_DIRS",
    "BOOKS_DIR",
    "BOOKS_DIRS",
)

NUMBERED_DIR_REGEX = re.compile(
    r"^(?:BUUKU{1,2}_)?(?:LIBRARY_|BOOKS_)?DIR_?(\d+)$", re.IGNORECASE
)
NAMED_DIR_REGEX = re.compile(
    r"^(?:BUUKU{1,2}_)?(?:LIBRARY_|BOOKS_)?DIR_([A-Za-z0-9_]+)$", re.IGNORECASE
)


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


def get_env_library_dirs(env: dict[str, str] | None = None) -> list[Path]:
    """Collects all library directory paths explicitly declared in environment variables.

    Supports:
    - BUUKUU_LIBRARY_DIR, BUUKUU_LIBRARY_DIRS, LIBRARY_DIR, LIBRARY_DIRS, BUUKUU_BOOKS_DIR, BOOKS_DIR
    - Numbered variables: DIR1, DIR2, DIR_1, DIR_2, BUUKUU_DIR1, BUUKUU_DIR_1, BUUKUU_LIBRARY_DIR_1, etc.
    - Named variables: BUUKUU_LIBRARY_DIR_MANGA, BUUKUU_DIR_COMICS, etc.
    - Delimited values (colons, semicolons, commas, newlines).
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


def discover_library_dirs(
    data_dir: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> list[Path]:
    """Discovers all library directories from environment variables or returns default data/books."""
    env_paths = get_env_library_dirs(env=env)
    if env_paths:
        return env_paths

    data_path = Path(data_dir) if data_dir else (BASE_DIR / "data")
    return [data_path / "books"]


class Config:
    """Base application configuration."""

    SECRET_KEY: str = os.getenv("SECRET_KEY", "buukuu-secret-key-change-in-production")

    # Data storage paths
    DATA_DIR: Path = Path(os.getenv("BUUKUU_DATA_DIR", BASE_DIR / "data"))
    LIBRARY_DIRS: list[Path] = discover_library_dirs(DATA_DIR)
    LIBRARY_DIR: Path = LIBRARY_DIRS[0] if LIBRARY_DIRS else (DATA_DIR / "books")
    COVERS_DIR: Path = Path(os.getenv("BUUKUU_COVERS_DIR", DATA_DIR / "covers"))
    OPTIMIZED_DIR: Path = Path(
        os.getenv("BUUKUU_OPTIMIZED_DIR", DATA_DIR / "optimized")
    )

    # Database
    SQLALCHEMY_DATABASE_URI: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{DATA_DIR / 'buukuu.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # App Settings
    AUTH_REQUIRED: bool = os.getenv("BUUKUU_AUTH_REQUIRED", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    ALLOW_REGISTRATION: bool = os.getenv(
        "BUUKUU_ALLOW_REGISTRATION", "true"
    ).lower() in (
        "true",
        "1",
        "yes",
    )
    AUTO_SCAN_ON_START: bool = os.getenv("BUUKUU_AUTO_SCAN", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    WATCH_LIBRARY: bool = os.getenv("BUUKUU_WATCH_LIBRARY", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    AUTO_ENRICH: bool = os.getenv("BUUKUU_AUTO_ENRICH", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    METADATA_PROVIDER: str = os.getenv("BUUKUU_METADATA_PROVIDER", "all")
    PAGE_SIZE: int = int(os.getenv("BUUKUU_PAGE_SIZE", "24"))

    # Upload limits (500 MB)
    MAX_CONTENT_LENGTH: int = 500 * 1024 * 1024


class TestConfig(Config):
    """Test configuration."""

    TESTING: bool = True
    SQLALCHEMY_DATABASE_URI: str = "sqlite:///:memory:"
    AUTH_REQUIRED: bool = False
    AUTO_SCAN_ON_START: bool = False
    WATCH_LIBRARY: bool = False
    AUTO_ENRICH: bool = False
