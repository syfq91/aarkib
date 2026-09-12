from __future__ import annotations

from typing import TYPE_CHECKING

from aarkib.plugins.audio import (
    AudiobookMediaPlugin,
    AudioMediaPlugin,
    MusicMediaPlugin,
)
from aarkib.plugins.base import (
    BasePlugin,
    MediaPlugin,
    OptimizerPlugin,
    PluginRegistry,
    ProtocolPlugin,
    plugin_registry,
)
from aarkib.plugins.book import BookMediaPlugin
from aarkib.plugins.jellyfin import JellyfinProtocolPlugin
from aarkib.plugins.opds import OPDSProtocolPlugin
from aarkib.plugins.optimizer import EInkOptimizerPlugin
from aarkib.plugins.podcast import PodcastMediaPlugin
from aarkib.plugins.subsonic import SubsonicProtocolPlugin
from aarkib.plugins.video import VideoMediaPlugin

if TYPE_CHECKING:
    from flask import Flask


def init_plugins(app: Flask | None = None) -> PluginRegistry:
    """Initializes and registers built-in plugins, wiring blueprints and extensions if app is provided."""
    # Register built-in media plugins
    if not plugin_registry.get_plugin("books"):
        plugin_registry.register(BookMediaPlugin())

    if not plugin_registry.get_plugin("video"):
        plugin_registry.register(VideoMediaPlugin())

    if not plugin_registry.get_plugin("audio"):
        plugin_registry.register(AudioMediaPlugin())

    if not plugin_registry.get_plugin("audiobook"):
        plugin_registry.register(AudiobookMediaPlugin())

    if not plugin_registry.get_plugin("podcast"):
        plugin_registry.register(PodcastMediaPlugin())

    if not plugin_registry.get_plugin("music"):
        plugin_registry.register(MusicMediaPlugin())

    # Register built-in optimizer plugin
    if not plugin_registry.get_plugin("eink_optimizer"):
        plugin_registry.register(EInkOptimizerPlugin())

    # Register built-in protocol plugins
    if not plugin_registry.get_plugin("opds"):
        plugin_registry.register(OPDSProtocolPlugin())

    if not plugin_registry.get_plugin("subsonic"):
        plugin_registry.register(SubsonicProtocolPlugin())

    if not plugin_registry.get_plugin("jellyfin"):
        plugin_registry.register(JellyfinProtocolPlugin())

    # Wire blueprints, lifecycle hooks, and CSRF exemptions if app is provided
    if app is not None:
        from aarkib import csrf

        for plugin in plugin_registry.get_all_plugins():
            # Check dynamic enable/disable toggle (e.g. ENABLE_SUBSONIC, AARKIB_ENABLE_SUBSONIC)
            cfg_key = f"ENABLE_{plugin.name.upper()}"
            legacy_key = f"AARKIB_ENABLE_{plugin.name.upper()}"
            is_enabled = app.config.get(cfg_key, app.config.get(legacy_key, True))
            if isinstance(is_enabled, str):
                is_enabled = is_enabled.lower() not in ("0", "false", "no", "off")
            plugin.enabled = bool(is_enabled)

            if not plugin.enabled:
                continue

            plugin.init_app(app)
            bp = plugin.register_routes(app)
            if bp is not None:
                if bp.name not in app.blueprints:
                    options = dict(plugin.blueprint_options)
                    app.register_blueprint(bp, **options)
                if plugin.csrf_exempt:
                    csrf.exempt(bp)

    return plugin_registry


__all__ = [
    "BasePlugin",
    "MediaPlugin",
    "ProtocolPlugin",
    "OptimizerPlugin",
    "PluginRegistry",
    "plugin_registry",
    "BookMediaPlugin",
    "VideoMediaPlugin",
    "AudioMediaPlugin",
    "AudiobookMediaPlugin",
    "MusicMediaPlugin",
    "PodcastMediaPlugin",
    "EInkOptimizerPlugin",
    "OPDSProtocolPlugin",
    "SubsonicProtocolPlugin",
    "JellyfinProtocolPlugin",
    "init_plugins",
]
