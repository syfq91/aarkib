"""Backward-compatibility module for Book / MediaItem.

The canonical unified media model is defined in aarkib.models.media_item.
"""

from __future__ import annotations

from aarkib.models.media_item import Book, Item, MediaItem

__all__ = ["Book", "Item", "MediaItem"]
