"""Metadata provider connecting to Apple iTunes Podcast Directory."""

from __future__ import annotations

import logging
import urllib.parse

from aarkib.services.metadata.base import (
    MediaMetadataDetails,
    MetadataProvider,
    MetadataSearchResult,
    compute_confidence_score,
)
from aarkib.services.metadata.client import ResilientHttpClient

logger = logging.getLogger(__name__)

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"


class iTunesPodcastProvider(MetadataProvider):
    """Zero-configuration podcast metadata provider using the public iTunes Search API."""

    name = "itunes"
    supported_media_types = {"podcast", "audio", "all"}

    def __init__(self, client: ResilientHttpClient | None = None) -> None:
        self.client = client or ResilientHttpClient(
            provider_name=self.name,
        )

    def search(
        self,
        query: str,
        media_type: str = "podcast",
        year: str | None = None,
    ) -> list[MetadataSearchResult]:
        if not query or not query.strip():
            return []

        clean_query = query.strip()
        encoded = urllib.parse.quote(clean_query)
        url = f"{ITUNES_SEARCH_URL}?term={encoded}&entity=podcast&limit=10"

        data = self.client.get_json(url)
        if not isinstance(data, dict) or not data.get("results"):
            return []

        results: list[MetadataSearchResult] = []
        for item in data.get("results", []):
            collection_id = str(item.get("collectionId") or "")
            if not collection_id:
                continue

            show_title = item.get("collectionName") or clean_query
            artist = item.get("artistName")
            creators = [artist] if artist else []

            # 600x600 high-res artwork or fallback
            poster_url = (
                item.get("artworkUrl600")
                or item.get("artworkUrl100")
                or item.get("artworkUrl60")
            )

            date_str = item.get("releaseDate")
            rel_year = str(date_str)[:4] if date_str else None

            score = compute_confidence_score(
                query=clean_query,
                candidate_title=show_title,
                target_year=year,
                candidate_year=rel_year,
            )

            genres = item.get("genres", [])
            feed_url = item.get("feedUrl")

            results.append(
                MetadataSearchResult(
                    id=collection_id,
                    provider=self.name,
                    title=show_title,
                    year=rel_year,
                    creators=creators,
                    overview=f"Podcast show by {artist}" if artist else None,
                    poster_url=poster_url,
                    media_type="podcast",
                    score=score,
                    extra={
                        "feed_url": feed_url,
                        "track_count": item.get("trackCount"),
                        "genres": genres,
                    },
                )
            )

        return results

    def fetch_details(
        self,
        external_id: str,
        media_type: str = "podcast",
    ) -> MediaMetadataDetails | None:
        clean_id = str(external_id).strip()
        if not clean_id:
            return None

        url = f"{ITUNES_LOOKUP_URL}?id={clean_id}&entity=podcast"
        data = self.client.get_json(url)
        if not isinstance(data, dict) or not data.get("results"):
            return None

        item = data["results"][0]
        show_title = item.get("collectionName") or "Unknown Show"
        artist = item.get("artistName")
        creators = [artist] if artist else []

        poster_url = (
            item.get("artworkUrl600")
            or item.get("artworkUrl100")
            or item.get("artworkUrl60")
        )

        date_str = item.get("releaseDate")
        rel_date = str(date_str)[:10] if date_str else None

        genres = item.get("genres", [])

        return MediaMetadataDetails(
            id=clean_id,
            provider=self.name,
            title=show_title,
            creators=creators,
            album=show_title,
            overview=None,
            publisher=artist,
            release_date=rel_date,
            language="en",
            poster_url=poster_url,
            genres=genres,
            extra={
                "feed_url": item.get("feedUrl"),
                "track_count": item.get("trackCount"),
            },
        )
