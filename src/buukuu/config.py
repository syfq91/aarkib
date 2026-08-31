from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Config:
    """Base application configuration."""

    SECRET_KEY: str = os.getenv("SECRET_KEY", "buukuu-secret-key-change-in-production")

    # Data storage paths
    DATA_DIR: Path = Path(os.getenv("BUUKUU_DATA_DIR", BASE_DIR / "data"))
    LIBRARY_DIR: Path = Path(os.getenv("BUUKUU_LIBRARY_DIR", DATA_DIR / "books"))
    COVERS_DIR: Path = Path(os.getenv("BUUKUU_COVERS_DIR", DATA_DIR / "covers"))

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
