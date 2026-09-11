from __future__ import annotations

import logging

from aarkib.services.metadata.base import (
    MediaMetadataDetails,
    MetadataProvider,
    MetadataSearchResult,
)
from aarkib.services.metadata.providers.google_books import GoogleBooksProvider
from aarkib.services.metadata.providers.musicbrainz import MusicBrainzProvider
from aarkib.services.metadata.providers.open_library import OpenLibraryProvider
from aarkib.services.metadata.providers.tmdb import TMDBProvider

logger = logging.getLogger(__name__)


class MetadataProviderRegistry:
    """Registry coordinating external metadata providers and media-type priority waterfalls."""

    DEFAULT_WATERFALLS: dict[str, list[str]] = {
        "book": ["googlebooks", "openlibrary"],
        "comic": ["openlibrary", "googlebooks"],
        "video": ["tmdb"],
        "music": ["musicbrainz"],
        "audio": ["musicbrainz"],
        "audiobook": ["googlebooks", "openlibrary", "musicbrainz"],
        "all": ["tmdb", "musicbrainz", "googlebooks", "openlibrary"],
    }

    def __init__(self) -> None:
        self._providers: dict[str, MetadataProvider] = {}
        self.register(GoogleBooksProvider())
        self.register(OpenLibraryProvider())
        self.register(TMDBProvider())
        self.register(MusicBrainzProvider())

    def register(self, provider: MetadataProvider) -> None:
        """Registers a metadata provider instance."""
        self._providers[provider.name.lower()] = provider

    def get_provider(self, name: str) -> MetadataProvider | None:
        """Retrieves a provider by name."""
        return self._providers.get(name.lower().strip())

    def list_providers(self) -> list[str]:
        """Returns list of registered provider names."""
        return sorted(list(self._providers.keys()))

    def search(
        self,
        media_type: str,
        query: str,
        year: str | None = None,
        provider_name: str | None = None,
    ) -> list[MetadataSearchResult]:
        """Searches candidates across the appropriate providers for the media type."""
        norm_type = (media_type or "all").lower().strip()

        if provider_name and provider_name.lower() != "all":
            provider = self.get_provider(provider_name)
            if not provider:
                return []
            return provider.search(query=query, media_type=norm_type, year=year)

        waterfall = self.DEFAULT_WATERFALLS.get(
            norm_type, self.DEFAULT_WATERFALLS["all"]
        )
        all_results: list[MetadataSearchResult] = []
        seen_keys: set[str] = set()

        for p_name in waterfall:
            provider = self.get_provider(p_name)
            if not provider:
                continue
            try:
                candidates = provider.search(
                    query=query, media_type=norm_type, year=year
                )
                for c in candidates:
                    dedup_key = f"{c.title.lower()}:{c.year}"
                    if dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        all_results.append(c)
            except Exception as exc:
                logger.debug("Search error for provider %s: %s", p_name, exc)

        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results

    def fetch_details(
        self,
        provider_name: str,
        external_id: str,
        media_type: str = "all",
    ) -> MediaMetadataDetails | None:
        """Fetches full metadata details from a specific provider."""
        provider = self.get_provider(provider_name)
        if not provider:
            logger.warning("Unknown metadata provider requested: %s", provider_name)
            return None
        return provider.fetch_details(external_id=external_id, media_type=media_type)


# Global registry singleton
metadata_registry = MetadataProviderRegistry()
