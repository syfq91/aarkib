from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from buukuu.services.parsers.base import register_parser

if TYPE_CHECKING:
    from flask import Blueprint, Flask

    from buukuu.services.parsers.base import BaseParsedMetadata


class MediaPlugin(ABC):
    """Abstract base class defining the contract for all media plugins.

    A media plugin encapsulates:
    - Supported file extensions
    - Metadata parsing
    - Cover / poster / artwork extraction
    - In-browser playback / reading URL routing
    - Optional Flask route blueprints
    """

    name: str = ""
    media_type: str = ""
    supported_extensions: set[str] = set()

    @abstractmethod
    def parse_metadata(self, file_path: Path) -> BaseParsedMetadata | None:
        """Parses and extracts metadata from a physical media file."""
        ...

    @abstractmethod
    def extract_cover(self, file_path: Path) -> bytes | None:
        """Extracts raw cover or poster artwork bytes from the file, or None."""
        ...

    def get_player_url(
        self, item_id: int, file_format: str | None = None
    ) -> str | None:
        """Returns the web player or reader URL for an item, or None."""
        return None

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        """Registers and returns any Flask blueprint required by this plugin."""
        return None

    def check_health(self) -> dict[str, Any]:
        """Performs health / dependency checks for the plugin."""
        return {"status": "ok", "plugin": self.name}


class PluginRegistry:
    """Central registry tracking active media plugins in Buukuu."""

    def __init__(self) -> None:
        self._plugins: dict[str, MediaPlugin] = {}
        self._ext_map: dict[str, MediaPlugin] = {}

    def register(self, plugin: MediaPlugin) -> None:
        """Registers a MediaPlugin instance and wires its extensions into the parser registry."""
        self._plugins[plugin.name] = plugin
        for ext in plugin.supported_extensions:
            norm_ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            self._ext_map[norm_ext] = plugin
            # Automatically connect parser registry
            register_parser(norm_ext, plugin.parse_metadata)

    def unregister(self, plugin_name: str) -> None:
        """Unregisters a media plugin."""
        if plugin_name in self._plugins:
            plugin = self._plugins.pop(plugin_name)
            self._ext_map = {k: v for k, v in self._ext_map.items() if v != plugin}

    def get_plugin_for_extension(self, extension: str) -> MediaPlugin | None:
        """Looks up the handler plugin for a given file extension."""
        norm_ext = (
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        )
        return self._ext_map.get(norm_ext)

    def get_plugin_for_media_type(self, media_type: str) -> MediaPlugin | None:
        """Looks up a plugin by its media_type identifier."""
        for plugin in self._plugins.values():
            if plugin.media_type == media_type:
                return plugin
        return None

    def get_plugin(self, name: str) -> MediaPlugin | None:
        """Looks up a plugin by its name."""
        return self._plugins.get(name)

    def get_all_plugins(self) -> list[MediaPlugin]:
        """Returns all registered plugins."""
        return list(self._plugins.values())

    def get_all_supported_extensions(self) -> set[str]:
        """Returns the union of all file extensions handled by registered plugins."""
        return set(self._ext_map.keys())


plugin_registry = PluginRegistry()
