from aarkib.models.author import Author, book_authors
from aarkib.models.book import Book
from aarkib.models.media import (
    AudioTrackMixin,
    MediaItemMixin,
    MediaType,
    VideoItemMixin,
)
from aarkib.models.progress import Bookmark, UserProgress
from aarkib.models.series import Series
from aarkib.models.tag import Tag, book_tags
from aarkib.models.user import User

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
