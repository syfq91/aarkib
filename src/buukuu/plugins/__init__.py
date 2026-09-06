from __future__ import annotations

from typing import TYPE_CHECKING

from buukuu.plugins.base import MediaPlugin, PluginRegistry, plugin_registry
from buukuu.plugins.book import BookMediaPlugin

if TYPE_CHECKING:
    from flask import Flask


def init_plugins(app: Flask | None = None) -> PluginRegistry:
    """Initializes and registers built-in media plugins, wiring blueprints if app is provided."""
    # Register BookMediaPlugin if not already registered
    if not plugin_registry.get_plugin("books"):
        plugin_registry.register(BookMediaPlugin())

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
    "init_plugins",
]
