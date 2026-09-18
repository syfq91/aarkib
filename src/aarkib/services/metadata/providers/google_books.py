from __future__ import annotations

import logging
import os
import urllib.parse
from typing import ClassVar

from aarkib.services.metadata.base import (
    MediaMetadataDetails,
    MetadataProvider,
    MetadataSearchResult,
    compute_confidence_score,
)
from aarkib.services.metadata.client import ResilientHttpClient
from aarkib.services.metadata.limiter import books_limiter

logger = logging.getLogger(__name__)


class GoogleBooksProvider(MetadataProvider):
    """Metadata provider connecting to Google Books Volumes API."""

    name = "googlebooks"
    supported_media_types: ClassVar[set[str]] = {"book", "comic", "audiobook", "all"}

    def __init__(
        self,
        api_key: str | None = None,
        client: ResilientHttpClient | None = None,
    ) -> None:
        self._api_key = api_key
        self.client = client or ResilientHttpClient(
            provider_name=self.name,
            limiter=books_limiter,
        )

    def get_api_key(self) -> str | None:
        """Resolves Google Books API key from instance, Flask config, or environment."""
        if self._api_key:
            return self._api_key
        try:
            from flask import current_app, has_app_context

            if has_app_context():
                cfg_key = current_app.config.get("GOOGLE_BOOKS_API_KEY")
                if cfg_key:
                    return str(cfg_key).strip()
        except Exception:
            pass
        env_key = os.getenv(
            "AARKIB_GOOGLE_BOOKS_API_KEY", os.getenv("GOOGLE_BOOKS_API_KEY")
        )
        return env_key.strip() if env_key else None

    def search(
        self,
        query: str,
        media_type: str = "all",
        year: str | None = None,
    ) -> list[MetadataSearchResult]:
        _ = media_type
        if not query or not query.strip():
            return []

        clean_query = query.strip()
        encoded = urllib.parse.quote(clean_query)
        api_key = self.get_api_key()
        key_param = f"&key={api_key}" if api_key else ""
        url = f"https://www.googleapis.com/books/v1/volumes?q={encoded}&maxResults=10{key_param}"

        data = self.client.get_json(url)
        if not isinstance(data, dict) or not data.get("items"):
            return []

        results: list[MetadataSearchResult] = []
        for item in data.get("items", []):
            vol = item.get("volumeInfo", {})
            vol_id = item.get("id")
            if not vol_id:
                continue

            title = vol.get("title") or clean_query
            pub_date = vol.get("publishedDate")
            item_year = str(pub_date)[:4] if pub_date else None
            creators = vol.get("authors", [])
            overview = vol.get("description")

            # Cover image URL
            image_links = vol.get("imageLinks", {})
            poster_url = None
            for k in ("thumbnail", "small", "medium", "large"):
                if k in image_links:
                    poster_url = image_links[k]
                    if poster_url.startswith("http://"):
                        poster_url = "https://" + poster_url[7:]
                    break

            score = compute_confidence_score(
                query=clean_query,
                candidate_title=title,
                target_year=year,
                candidate_year=item_year,
            )

            results.append(
                MetadataSearchResult(
                    id=vol_id,
                    provider=self.name,
                    title=title,
                    year=item_year,
                    creators=creators,
                    overview=overview,
                    poster_url=poster_url,
                    media_type="book",
                    score=score,
                    extra={
                        "publisher": vol.get("publisher"),
                        "pageCount": vol.get("pageCount"),
                    },
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def fetch_details(
        self,
        external_id: str,
        media_type: str = "all",
    ) -> MediaMetadataDetails | None:
        _ = media_type
        if not external_id:
            return None

        api_key = self.get_api_key()
        key_param = f"?key={api_key}" if api_key else ""
        url = f"https://www.googleapis.com/books/v1/volumes/{urllib.parse.quote(external_id)}{key_param}"
        data = self.client.get_json(url)
        if not isinstance(data, dict):
            return None

        vol = data.get("volumeInfo", {})
        title = vol.get("title") or external_id
        creators = vol.get("authors", [])
        overview = vol.get("description")
        publisher = vol.get("publisher")
        pub_date = vol.get("publishedDate")
        language = vol.get("language")
        page_count = vol.get("pageCount")
        genres = vol.get("categories", [])

        # Cover
        image_links = vol.get("imageLinks", {})
        poster_url = None
        for k in ("extraLarge", "large", "medium", "small", "thumbnail"):
            if k in image_links:
                poster_url = image_links[k]
                if poster_url.startswith("http://"):
                    poster_url = "https://" + poster_url[7:]
                break

        poster_bytes = self.client.get_bytes(poster_url) if poster_url else None

        # ISBN
        isbn = None
        for ident in vol.get("industryIdentifiers", []):
            if ident.get("type") in ("ISBN_13", "ISBN_10"):
                isbn = ident.get("identifier")
                if ident.get("type") == "ISBN_13":
                    break

        return MediaMetadataDetails(
            id=external_id,
            provider=self.name,
            title=title,
            creators=creators,
            overview=overview,
            poster_url=poster_url,
            poster_bytes=poster_bytes,
            release_date=pub_date,
            publisher=publisher,
            genres=genres,
            language=language,
            isbn=isbn,
            page_count=page_count,
        )
