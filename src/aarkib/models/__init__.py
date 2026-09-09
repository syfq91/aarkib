from aarkib.models.author import Author, book_authors
from aarkib.models.library import Library
from aarkib.models.media import (
    AudioTrackMixin,
    MediaItemMixin,
    MediaType,
    PlayableItemMixin,
    VideoItemMixin,
)
from aarkib.models.media_item import Book, Item, MediaItem
from aarkib.models.progress import Bookmark, UserProgress
from aarkib.models.series import Series
from aarkib.models.tag import Tag, book_tags
from aarkib.models.user import User

# Generalized domain aliases
Creator = Author
Collection = Series

__all__ = [
    "User",
    "MediaItem",
    "Item",
    "Book",
    "Library",
    "Author",
    "Creator",
    "Series",
    "Collection",
    "Tag",
    "UserProgress",
    "Bookmark",
    "MediaType",
    "MediaItemMixin",
    "PlayableItemMixin",
    "AudioTrackMixin",
    "VideoItemMixin",
    "book_authors",
    "book_tags",
]
