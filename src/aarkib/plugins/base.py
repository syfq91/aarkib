from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from aarkib.services.parsers.base import PARSER_REGISTRY, register_parser

if TYPE_CHECKING:
    from flask import Blueprint, Flask

    from aarkib.services.parsers.base import BaseParsedMetadata


class BasePlugin:
    """Base class defining the foundational contract for all Aarkib plugins."""

    name: str = ""
    display_name: str = ""
    plugin_type: str = "generic"
    description: str = ""
    enabled: bool = True
    csrf_exempt: bool = False
    blueprint_options: ClassVar[dict[str, Any]] = {}

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        """Registers and returns any Flask blueprint required by this plugin."""
        return None

    def init_app(self, app: Flask) -> None:
        """Lifecycle hook invoked when Flask application initializes."""

    def check_health(self) -> dict[str, Any]:
        """Performs health / dependency checks for the plugin."""
        return {
            "status": "ok",
            "plugin": self.name,
            "type": self.plugin_type,
            "enabled": self.enabled,
        }


class MediaPlugin(BasePlugin, ABC):
    """Abstract base class defining the contract for all media plugins.

    A media plugin encapsulates:
    - Supported file extensions
    - Metadata parsing
    - Cover / poster / artwork extraction
    - In-browser playback / reading URL routing
    - Optional Flask route blueprints
    """

    plugin_type: str = "media"
    media_type: str = ""
    supported_media_types: ClassVar[set[str]] = set()
    supported_extensions: ClassVar[set[str]] = set()

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

    def get_playback_info(
        self, item: Any, user_id: int | None = None
    ) -> dict[str, Any]:
        """Returns client-agnostic playback or reading descriptor for this media item."""
        res: dict[str, Any] = {
            "media_id": item.id,
            "media_type": getattr(item, "media_type", None) or self.media_type,
            "title": item.title,
            "file_format": item.file_format,
            "player_url": self.get_player_url(item.id, item.file_format),
            "file_url": f"/api/media/{item.id}/file",
            "cover_url": f"/api/media/{item.id}/cover",
            "duration": getattr(item, "duration", None),
            "resume_position": None,
            "progress_percentage": 0.0,
            "is_completed": False,
        }
        if user_id is not None:
            from sqlalchemy import select

            from aarkib.extensions import db
            from aarkib.models.progress import UserProgress

            progress = db.session.scalar(
                select(UserProgress).where(
                    UserProgress.user_id == user_id,
                    UserProgress.media_item_id == item.id,
                )
            )
            if progress:
                res["progress_percentage"] = progress.percentage
                res["is_completed"] = progress.is_completed
                res["playback_speed"] = progress.playback_speed or 1.0
                res["playback_type"] = progress.playback_type or item.media_type
                if progress.position_seconds is not None:
                    res["resume_position"] = progress.position_seconds
                else:
                    loc = progress.progress_location
                    try:
                        res["resume_position"] = float(loc) if loc else None
                    except ValueError, TypeError:
                        res["resume_position"] = loc
        return res

    def check_health(self) -> dict[str, Any]:
        """Performs health / dependency checks for the plugin."""
        res = super().check_health()
        res.update(
            {
                "media_type": self.media_type,
                "supported_extensions": sorted(self.supported_extensions),
            }
        )
        return res


class ProtocolPlugin(BasePlugin):
    """Abstract base class for external protocol and client API integration plugins."""

    plugin_type: str = "protocol"
    protocol_version: str = ""
    csrf_exempt: bool = True

    def check_health(self) -> dict[str, Any]:
        """Performs health check for the protocol plugin."""
        res = super().check_health()
        res["protocol_version"] = self.protocol_version
        return res


class OptimizerPlugin(BasePlugin):
    """Abstract base class for media transformation and device optimization plugins."""

    plugin_type: str = "optimizer"
    supported_formats: ClassVar[set[str]] = set()

    def get_presets(self) -> dict[str, Any]:
        """Returns dictionary of supported optimization presets."""
        return {}

    def optimize(
        self,
        file_path: Path,
        preset: str = "generic",
        cache_dir: Path | None = None,
    ) -> Path:
        """Optimizes a media file according to target device preset."""
        raise NotImplementedError

    def check_health(self) -> dict[str, Any]:
        """Performs health check for the optimizer plugin."""
        res = super().check_health()
        res.update(
            {
                "supported_formats": sorted(self.supported_formats),
                "presets": list(self.get_presets().keys()),
            }
        )
        return res


class PluginRegistry:
    """Central registry tracking active plugins in Aarkib."""

    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}
        self._ext_map: dict[str, MediaPlugin] = {}
        self._media_type_map: dict[str, MediaPlugin] = {}
        self._protocol_plugins: dict[str, ProtocolPlugin] = {}
        self._optimizer_plugins: dict[str, OptimizerPlugin] = {}

    def register(self, plugin: BasePlugin) -> None:
        """Registers a BasePlugin instance and wires its capabilities."""
        self._plugins[plugin.name] = plugin

        if isinstance(plugin, MediaPlugin):
            if plugin.media_type:
                self._media_type_map[plugin.media_type] = plugin
            for mt in getattr(plugin, "supported_media_types", set()):
                self._media_type_map[mt] = plugin
            for ext in plugin.supported_extensions:
                norm_ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                self._ext_map[norm_ext] = plugin
                # Automatically connect parser registry
                register_parser(norm_ext, plugin.parse_metadata)

        if isinstance(plugin, ProtocolPlugin):
            self._protocol_plugins[plugin.name] = plugin

        if isinstance(plugin, OptimizerPlugin):
            self._optimizer_plugins[plugin.name] = plugin

    def unregister(self, plugin_name: str) -> None:
        """Unregisters a plugin and cleans up all registry mappings."""
        plugin = self._plugins.pop(plugin_name, None)
        if plugin:
            if isinstance(plugin, MediaPlugin):
                self._media_type_map = {
                    k: v for k, v in self._media_type_map.items() if v != plugin
                }
                self._ext_map = {k: v for k, v in self._ext_map.items() if v != plugin}
                for ext in plugin.supported_extensions:
                    norm = ext.lower().lstrip(".")
                    PARSER_REGISTRY.pop(f".{norm}", None)
            if isinstance(plugin, ProtocolPlugin):
                self._protocol_plugins.pop(plugin.name, None)
            if isinstance(plugin, OptimizerPlugin):
                self._optimizer_plugins.pop(plugin.name, None)

    def get_plugin_for_extension(self, extension: str) -> MediaPlugin | None:
        """Looks up the handler plugin for a given file extension."""
        norm_ext = (
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        )
        return self._ext_map.get(norm_ext)

    def get_plugin_for_media_type(self, media_type: str) -> MediaPlugin | None:
        """Looks up a plugin by its media_type identifier."""
        return self._media_type_map.get(media_type)

    def get_plugin(self, name: str) -> BasePlugin | None:
        """Looks up a plugin by its name."""
        return self._plugins.get(name)

    def get_protocol_plugin(self, name: str) -> ProtocolPlugin | None:
        """Looks up a protocol plugin by its name."""
        return self._protocol_plugins.get(name)

    def get_optimizer_plugin(self, name: str) -> OptimizerPlugin | None:
        """Looks up an optimizer plugin by its name."""
        return self._optimizer_plugins.get(name)

    def get_all_plugins(self) -> list[BasePlugin]:
        """Returns all registered plugins."""
        return list(self._plugins.values())

    def get_plugins_by_type(self, plugin_type: str) -> list[BasePlugin]:
        """Returns all registered plugins of a specific type."""
        return [p for p in self._plugins.values() if p.plugin_type == plugin_type]

    def get_all_supported_extensions(self, active_only: bool = True) -> set[str]:
        """Returns the union of file extensions handled by registered plugins."""
        if not active_only:
            return set(self._ext_map.keys())
        return {ext for ext, plugin in self._ext_map.items() if plugin.enabled}


plugin_registry = PluginRegistry()

__all__ = [
    "BasePlugin",
    "MediaPlugin",
    "ProtocolPlugin",
    "OptimizerPlugin",
    "PluginRegistry",
    "plugin_registry",
]
