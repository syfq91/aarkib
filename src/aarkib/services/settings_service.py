from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from flask import Flask
from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models.setting import SystemSetting

logger = logging.getLogger("aarkib.settings")


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    type: type  # bool, int, or str
    default: Any
    display_name: str
    description: str
    category: str
    choices: tuple[str, ...] | None = None
    min_value: int | None = None
    max_value: int | None = None


MANAGED_SETTINGS: dict[str, SettingDefinition] = {
    "AUTO_SCAN_ON_START": SettingDefinition(
        key="AUTO_SCAN_ON_START",
        type=bool,
        default=True,
        display_name="Scan Libraries on Startup",
        description="Automatically perform a full scan of all configured media folders when the server boots.",
        category="Library & Automation",
    ),
    "WATCH_LIBRARY": SettingDefinition(
        key="WATCH_LIBRARY",
        type=bool,
        default=True,
        display_name="Watch Folders in Real-Time",
        description="Continuously monitor media folders for filesystem changes to auto-index new or removed files.",
        category="Library & Automation",
    ),
    "AUTO_ENRICH": SettingDefinition(
        key="AUTO_ENRICH",
        type=bool,
        default=False,
        display_name="Automatically Enrich Metadata",
        description="Query online metadata providers (Google Books, Open Library) for books during library scans.",
        category="Metadata Enrichment",
    ),
    "METADATA_PROVIDER": SettingDefinition(
        key="METADATA_PROVIDER",
        type=str,
        default="all",
        display_name="Metadata Provider",
        description="Default online service used for fetching book descriptions, subjects, and covers.",
        category="Metadata Enrichment",
        choices=("all", "googlebooks", "openlibrary"),
    ),
    "PAGE_SIZE": SettingDefinition(
        key="PAGE_SIZE",
        type=int,
        default=24,
        display_name="Catalog Page Size",
        description="Number of media cards displayed per page in catalog and search views.",
        category="Display & Presentation",
        min_value=6,
        max_value=240,
    ),
    "ENABLE_OPDS": SettingDefinition(
        key="ENABLE_OPDS",
        type=bool,
        default=True,
        display_name="OPDS Feeds & Reading Sync",
        description="Enable OPDS 1.2 (Atom) & 2.0 (JSON-LD) catalog feeds and OPDS Progression 1.0 sync for e-readers.",
        category="Protocols & Services",
    ),
    "ENABLE_SUBSONIC": SettingDefinition(
        key="ENABLE_SUBSONIC",
        type=bool,
        default=True,
        display_name="Subsonic Mobile Streaming API",
        description="Enable Subsonic v1.16.1 compatible REST endpoints (/rest) for third-party mobile apps (Symfonium, DSub).",
        category="Protocols & Services",
    ),
    "ENABLE_EINK_OPTIMIZER": SettingDefinition(
        key="ENABLE_EINK_OPTIMIZER",
        type=bool,
        default=True,
        display_name="E-Ink Device EPUB Optimizer",
        description="Enable hardware-specific on-demand EPUB font stripping, CSS cleaning, and grayscale dithering.",
        category="Protocols & Services",
    ),
    "ENABLE_BOOKS": SettingDefinition(
        key="ENABLE_BOOKS",
        type=bool,
        default=True,
        display_name="Books & Comics Plugin",
        description="Enable indexing and web reading for EPUB, CBZ, CBR, and ZIP archives.",
        category="Media Format Plugins",
    ),
    "ENABLE_VIDEO": SettingDefinition(
        key="ENABLE_VIDEO",
        type=bool,
        default=True,
        display_name="Movies & TV Shows Plugin",
        description="Enable indexing, container metadata parsing, HTTP 206 streaming, and HTML5 video playback.",
        category="Media Format Plugins",
    ),
    "ENABLE_AUDIO": SettingDefinition(
        key="ENABLE_AUDIO",
        type=bool,
        default=True,
        display_name="Audio & Music Plugin",
        description="Enable indexing and in-browser playback for generic audio tracks (MP3, FLAC, AAC, WAV, OGG).",
        category="Media Format Plugins",
    ),
    "ENABLE_AUDIOBOOK": SettingDefinition(
        key="ENABLE_AUDIOBOOK",
        type=bool,
        default=True,
        display_name="Dedicated Audiobooks Plugin",
        description="Enable dedicated M4B audiobook parsing with chapter markers and bookmark navigation.",
        category="Media Format Plugins",
    ),
    "ENABLE_PODCAST": SettingDefinition(
        key="ENABLE_PODCAST",
        type=bool,
        default=True,
        display_name="Podcasts Plugin",
        description="Enable episodic podcast parsing, OPML import, and dedicated podcast player.",
        category="Media Format Plugins",
    ),
    "ENABLE_MUSIC": SettingDefinition(
        key="ENABLE_MUSIC",
        type=bool,
        default=True,
        display_name="Music Albums Plugin",
        description="Enable music track and album clustering with disc numbering and album artwork.",
        category="Media Format Plugins",
    ),
}


def parse_setting_value(spec: SettingDefinition, raw: Any) -> Any:
    """Parses and validates a raw setting value according to its definition."""
    if spec.type is bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("true", "1", "yes", "on")
        return bool(raw)

    if spec.type is int:
        try:
            val = int(raw)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Setting '{spec.key}' must be a valid integer.") from e

        if spec.min_value is not None and val < spec.min_value:
            raise ValueError(f"Setting '{spec.key}' must be at least {spec.min_value}.")
        if spec.max_value is not None and val > spec.max_value:
            raise ValueError(f"Setting '{spec.key}' cannot exceed {spec.max_value}.")
        return val

    if spec.type is str:
        s = str(raw).strip()
        if spec.choices and s not in spec.choices:
            valid = ", ".join(repr(c) for c in spec.choices)
            raise ValueError(
                f"Setting '{spec.key}' must be one of: {valid} (got {s!r})."
            )
        return s

    return raw


def serialize_setting_value(spec: SettingDefinition, val: Any) -> str:
    """Serializes a typed setting value into a string for database storage."""
    if spec.type is bool:
        return "true" if val else "false"
    return str(val)


def load_settings_into_config(app: Flask) -> None:
    """Loads saved system settings from the database and updates app.config."""
    # Capture initial environment defaults if not already captured
    if "_ENV_DEFAULTS" not in app.config:
        env_defaults: dict[str, Any] = {}
        for key, spec in MANAGED_SETTINGS.items():
            env_defaults[key] = app.config.get(key, spec.default)
        app.config["_ENV_DEFAULTS"] = env_defaults

    try:
        rows = db.session.scalars(select(SystemSetting)).all()
    except Exception as e:
        logger.warning("Could not query settings table during startup: %s", e)
        return

    for row in rows:
        spec = MANAGED_SETTINGS.get(row.key)
        if spec:
            try:
                parsed = parse_setting_value(spec, row.value)
                app.config[row.key] = parsed
            except Exception as e:
                logger.warning(
                    "Error parsing stored setting %s=%r: %s", row.key, row.value, e
                )

    sync_plugins_state(app)


def sync_plugins_state(app: Flask) -> None:
    """Synchronizes plugin enabled status in plugin_registry with app.config."""
    from aarkib.plugins import plugin_registry

    for plugin in plugin_registry.get_all_plugins():
        cfg_key = f"ENABLE_{plugin.name.upper()}"
        legacy_key = f"AARKIB_ENABLE_{plugin.name.upper()}"
        if cfg_key in app.config:
            plugin.enabled = bool(app.config[cfg_key])
        elif legacy_key in app.config:
            plugin.enabled = bool(app.config[legacy_key])


def get_effective_settings(app: Flask) -> dict[str, Any]:
    """Returns a structured view of all managed settings, their current values,
    types, defaults, and whether they have been overridden in the database."""
    # Query database overrides
    db_rows = {s.key: s for s in db.session.scalars(select(SystemSetting)).all()}
    env_defaults = app.config.get("_ENV_DEFAULTS", {})

    settings_detail: dict[str, Any] = {}
    flat_values: dict[str, Any] = {}

    for key, spec in MANAGED_SETTINGS.items():
        is_overridden = key in db_rows
        current_val = app.config.get(key, spec.default)
        env_default = env_defaults.get(key, spec.default)

        flat_values[key] = current_val
        settings_detail[key] = {
            "key": key,
            "value": current_val,
            "type": spec.type.__name__,
            "is_overridden": is_overridden,
            "env_default": env_default,
            "display_name": spec.display_name,
            "description": spec.description,
            "category": spec.category,
            "choices": list(spec.choices) if spec.choices else None,
            "min_value": spec.min_value,
            "max_value": spec.max_value,
            "updated_at": (
                db_rows[key].updated_at.isoformat() if is_overridden else None
            ),
        }

    return {
        "settings": settings_detail,
        "values": flat_values,
    }


def update_settings(app: Flask, updates: dict[str, Any]) -> dict[str, Any]:
    """Validates and persists setting updates, updates app.config in-memory,
    and restarts or stops the filesystem watcher if WATCH_LIBRARY was toggled."""
    if not isinstance(updates, dict):
        raise ValueError("Payload must be a JSON object of setting key-value pairs.")

    from aarkib.services.scanner import start_library_watcher, stop_library_watcher

    old_watch_library = app.config.get("WATCH_LIBRARY", True)

    for raw_key, raw_val in updates.items():
        key = raw_key.upper().strip()
        spec = MANAGED_SETTINGS.get(key)
        if not spec:
            continue

        parsed_val = parse_setting_value(spec, raw_val)
        serialized_val = serialize_setting_value(spec, parsed_val)

        # Upsert in database
        setting_obj = db.session.get(SystemSetting, key)
        if setting_obj:
            setting_obj.value = serialized_val
        else:
            setting_obj = SystemSetting(key=key, value=serialized_val)
            db.session.add(setting_obj)

        # Update in-memory app.config
        app.config[key] = parsed_val

    db.session.commit()
    sync_plugins_state(app)

    # Handle dynamic watcher toggle if changed
    new_watch_library = app.config.get("WATCH_LIBRARY", True)
    if old_watch_library != new_watch_library:
        if new_watch_library:
            start_library_watcher(app)
        else:
            stop_library_watcher(app)

    return get_effective_settings(app)


def reset_settings_to_defaults(app: Flask) -> dict[str, Any]:
    """Deletes all database overrides and restores settings to environment defaults."""
    from aarkib.services.scanner import start_library_watcher, stop_library_watcher

    old_watch_library = app.config.get("WATCH_LIBRARY", True)
    env_defaults = app.config.get("_ENV_DEFAULTS", {})

    # Remove all managed settings from database
    for key in MANAGED_SETTINGS:
        row = db.session.get(SystemSetting, key)
        if row:
            db.session.delete(row)

    db.session.commit()

    # Restore in-memory config to env defaults
    for key, spec in MANAGED_SETTINGS.items():
        app.config[key] = env_defaults.get(key, spec.default)

    sync_plugins_state(app)

    # Handle dynamic watcher toggle if changed
    new_watch_library = app.config.get("WATCH_LIBRARY", True)
    if old_watch_library != new_watch_library:
        if new_watch_library:
            start_library_watcher(app)
        else:
            stop_library_watcher(app)

    return get_effective_settings(app)
