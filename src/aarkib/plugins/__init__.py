from __future__ import annotations

from typing import TYPE_CHECKING

from aarkib.plugins.audio import (
    AudiobookMediaPlugin,
    AudioMediaPlugin,
    MusicMediaPlugin,
)
from aarkib.plugins.base import MediaPlugin, PluginRegistry, plugin_registry
from aarkib.plugins.book import BookMediaPlugin
from aarkib.plugins.podcast import PodcastMediaPlugin
from aarkib.plugins.video import VideoMediaPlugin

if TYPE_CHECKING:
    from flask import Flask


def init_plugins(app: Flask | None = None) -> PluginRegistry:
    """Initializes and registers built-in media plugins, wiring blueprints if app is provided."""
    # Register BookMediaPlugin if not already registered
    if not plugin_registry.get_plugin("books"):
        plugin_registry.register(BookMediaPlugin())

    # Register VideoMediaPlugin if not already registered
    if not plugin_registry.get_plugin("video"):
        plugin_registry.register(VideoMediaPlugin())

    # Register AudioMediaPlugin if not already registered
    if not plugin_registry.get_plugin("audio"):
        plugin_registry.register(AudioMediaPlugin())

    # Register AudiobookMediaPlugin if not already registered
    if not plugin_registry.get_plugin("audiobook"):
        plugin_registry.register(AudiobookMediaPlugin())

    # Register PodcastMediaPlugin if not already registered
    if not plugin_registry.get_plugin("podcast"):
        plugin_registry.register(PodcastMediaPlugin())

    # Register MusicMediaPlugin if not already registered
    if not plugin_registry.get_plugin("music"):
        plugin_registry.register(MusicMediaPlugin())

    # Wire blueprints into Flask app if provided
    if app is not None:
        for plugin in plugin_registry.get_all_plugins():
            bp = plugin.register_routes(app)
            if bp is not None and bp.name not in app.blueprints:
                app.register_blueprint(bp)

    return plugin_registry


__all__ = [
    "MediaPlugin",
    "PluginRegistry",
    "plugin_registry",
    "BookMediaPlugin",
    "VideoMediaPlugin",
    "AudioMediaPlugin",
    "AudiobookMediaPlugin",
    "MusicMediaPlugin",
    "PodcastMediaPlugin",
    "init_plugins",
]
