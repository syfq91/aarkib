from buukuu.models.author import Author, book_authors
from buukuu.models.book import Book
from buukuu.models.media import (
    AudioTrackMixin,
    MediaItemMixin,
    MediaType,
    VideoItemMixin,
)
from buukuu.models.progress import Bookmark, UserProgress
from buukuu.models.series import Series
from buukuu.models.tag import Tag, book_tags
from buukuu.models.user import User

# Generalized domain aliases
Creator = Author
Collection = Series

__all__ = [
    "User",
    "Book",
    "Author",
    "Creator",
    "Series",
    "Collection",
    "Tag",
    "UserProgress",
    "Bookmark",
    "MediaType",
    "MediaItemMixin",
    "AudioTrackMixin",
    "VideoItemMixin",
    "book_authors",
    "book_tags",
]
