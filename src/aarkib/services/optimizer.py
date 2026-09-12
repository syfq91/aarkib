"""E-Ink Device Hardware Optimizer service re-export for backward compatibility."""

from __future__ import annotations

from aarkib.plugins.optimizer import (
    DEVICE_PRESETS,
    FONT_EXTENSIONS,
    FONT_MEDIA_TYPES,
    IMAGE_EXTENSIONS,
    EInkOptimizerPlugin,
    clean_css_content,
    clean_html_content,
    get_or_create_optimized_epub,
    get_preset,
    optimize_epub,
    optimize_image,
)

__all__ = [
    "DEVICE_PRESETS",
    "FONT_EXTENSIONS",
    "FONT_MEDIA_TYPES",
    "IMAGE_EXTENSIONS",
    "get_preset",
    "optimize_image",
    "clean_css_content",
    "clean_html_content",
    "optimize_epub",
    "get_or_create_optimized_epub",
    "EInkOptimizerPlugin",
]
