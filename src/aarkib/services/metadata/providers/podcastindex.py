"""Metadata provider connecting to the PodcastIndex.org open directory."""

from __future__ import annotations

import hashlib
import logging
import os
import time
import urllib.parse
from typing import ClassVar

from aarkib.services.metadata.base import (
    MediaMetadataDetails,
    MetadataProvider,
    MetadataSearchResult,
    compute_confidence_score,
)
from aarkib.services.metadata.client import ResilientHttpClient

logger = logging.getLogger(__name__)

PODCASTINDEX_BASE = "https://api.podcastindex.org/api/1.0"


class PodcastIndexProvider(MetadataProvider):
    """Metadata provider connecting to the open PodcastIndex directory with optional API keys."""

    name = "podcastindex"
    supported_media_types: ClassVar[set[str]] = {"podcast", "audio", "all"}

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        client: ResilientHttpClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self.client = client or ResilientHttpClient(
            provider_name=self.name,
        )

    @property
    def api_key(self) -> str:
        """Dynamically resolves PodcastIndex API key from instance, Flask config, or environment."""
        if self._api_key:
            return self._api_key
        try:
            from flask import current_app, has_app_context

            if has_app_context():
                cfg_val = current_app.config.get("PODCASTINDEX_API_KEY")
                if cfg_val:
                    return str(cfg_val).strip()
        except Exception:
            pass
        return os.getenv(
            "AARKIB_PODCASTINDEX_API_KEY", os.getenv("PODCASTINDEX_API_KEY", "")
        ).strip()

    @api_key.setter
    def api_key(self, val: str | None) -> None:
        self._api_key = val

    @property
    def api_secret(self) -> str:
        """Dynamically resolves PodcastIndex API secret from instance, Flask config, or environment."""
        if self._api_secret:
            return self._api_secret
        try:
            from flask import current_app, has_app_context

            if has_app_context():
                cfg_val = current_app.config.get("PODCASTINDEX_API_SECRET")
                if cfg_val:
                    return str(cfg_val).strip()
        except Exception:
            pass
        return os.getenv(
            "AARKIB_PODCASTINDEX_API_SECRET", os.getenv("PODCASTINDEX_API_SECRET", "")
        ).strip()

    @api_secret.setter
    def api_secret(self, val: str | None) -> None:
        self._api_secret = val

    def is_configured(self) -> bool:
        """Returns True if valid PodcastIndex API credentials are provided."""
        return bool(self.api_key and self.api_secret)

    def _auth_headers(self) -> dict[str, str]:
        """Generates required SHA1 authentication headers for PodcastIndex API."""
        now = str(int(time.time()))
        auth_hash = hashlib.sha1(
            (self.api_key + self.api_secret + now).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        return {
            "X-Auth-Date": now,
            "X-Auth-Key": self.api_key,
            "Authorization": auth_hash,
        }

    def search(
        self,
        query: str,
        media_type: str = "podcast",
        year: str | None = None,
    ) -> list[MetadataSearchResult]:
        _ = media_type
        if not self.is_configured() or not query or not query.strip():
            return []

        clean_query = query.strip()
        encoded = urllib.parse.quote(clean_query)
        url = f"{PODCASTINDEX_BASE}/search/byterm?q={encoded}"

        headers = self._auth_headers()
        data = self.client.get_json(url, headers=headers)
        if not isinstance(data, dict) or not data.get("feeds"):
            return []

        results: list[MetadataSearchResult] = []
        for item in data.get("feeds", []):
            feed_id = str(item.get("id") or "")
            if not feed_id:
                continue

            show_title = item.get("title") or clean_query
            author = item.get("author")
            creators = [author] if author else []
            poster_url = item.get("artwork") or item.get("image")
            overview = item.get("description")

            score = compute_confidence_score(
                query=clean_query,
                candidate_title=show_title,
                target_year=year,
            )

            results.append(
                MetadataSearchResult(
                    id=feed_id,
                    provider=self.name,
                    title=show_title,
                    year=None,
                    creators=creators,
                    overview=overview,
                    poster_url=poster_url,
                    media_type="podcast",
                    score=score,
                    extra={
                        "feed_url": item.get("url"),
                        "categories": item.get("categories"),
                    },
                )
            )

        return results

    def fetch_details(
        self,
        external_id: str,
        media_type: str = "podcast",
    ) -> MediaMetadataDetails | None:
        _ = media_type
        if not self.is_configured() or not external_id:
            return None

        clean_id = str(external_id).strip()
        url = f"{PODCASTINDEX_BASE}/podcasts/byfeedid?id={clean_id}"
        headers = self._auth_headers()
        data = self.client.get_json(url, headers=headers)
        if not isinstance(data, dict) or not data.get("feed"):
            return None

        item = data["feed"]
        show_title = item.get("title") or "Unknown Show"
        author = item.get("author")
        creators = [author] if author else []
        poster_url = item.get("artwork") or item.get("image")

        categories = []
        raw_cats = item.get("categories")
        if isinstance(raw_cats, dict):
            categories = list(raw_cats.values())
        elif isinstance(raw_cats, list):
            categories = [str(c) for c in raw_cats]

        return MediaMetadataDetails(
            id=clean_id,
            provider=self.name,
            title=show_title,
            creators=creators,
            album=show_title,
            overview=item.get("description"),
            publisher=author,
            release_date=None,
            language=item.get("language") or "en",
            poster_url=poster_url,
            genres=categories,
            extra={
                "feed_url": item.get("url"),
                "episode_count": item.get("episodeCount"),
            },
        )
