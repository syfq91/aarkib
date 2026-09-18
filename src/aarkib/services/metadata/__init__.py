from __future__ import annotations

from aarkib.services.metadata.base import (
    MediaMetadataDetails,
    MetadataProvider,
    MetadataSearchResult,
    compute_confidence_score,
)
from aarkib.services.metadata.cache import MetadataCacheManager
from aarkib.services.metadata.client import ResilientHttpClient, is_safe_http_url
from aarkib.services.metadata.limiter import (
    TokenBucketRateLimiter,
    books_limiter,
    musicbrainz_limiter,
    tmdb_limiter,
)
from aarkib.services.metadata.matcher import (
    CandidateMatch,
    MetadataMatcher,
    calculate_match_confidence,
)
from aarkib.services.metadata.providers.google_books import GoogleBooksProvider
from aarkib.services.metadata.providers.musicbrainz import MusicBrainzProvider
from aarkib.services.metadata.providers.open_library import OpenLibraryProvider
from aarkib.services.metadata.providers.tmdb import TMDBProvider
from aarkib.services.metadata.registry import (
    MetadataProviderRegistry,
    metadata_registry,
)

__all__ = [
    "CandidateMatch",
    "MetadataMatcher",
    "calculate_match_confidence",
    "MetadataProvider",
    "MetadataSearchResult",
    "MediaMetadataDetails",
    "compute_confidence_score",
    "TokenBucketRateLimiter",
    "musicbrainz_limiter",
    "tmdb_limiter",
    "books_limiter",
    "MetadataCacheManager",
    "ResilientHttpClient",
    "is_safe_http_url",
    "GoogleBooksProvider",
    "OpenLibraryProvider",
    "TMDBProvider",
    "MusicBrainzProvider",
    "MetadataProviderRegistry",
    "metadata_registry",
]
