"""Metadata provider connecting to the ComicVine API for comics and graphic novels."""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
from typing import ClassVar

from flask import current_app

from aarkib.services.metadata.base import (
    MediaMetadataDetails,
    MetadataProvider,
    MetadataSearchResult,
    compute_confidence_score,
)
from aarkib.services.metadata.client import ResilientHttpClient
from aarkib.services.metadata.limiter import comicvine_limiter

logger = logging.getLogger(__name__)

COMICVINE_BASE = "https://comicvine.gamespot.com/api"


class ComicVineProvider(MetadataProvider):
    """Metadata provider connecting to ComicVine for comic books and graphic novels."""

    name = "comicvine"
    supported_media_types: ClassVar[set[str]] = {"comic", "book", "all"}

    def __init__(
        self,
        api_key: str | None = None,
        client: ResilientHttpClient | None = None,
    ) -> None:
        self._api_key = api_key
        self.client = client or ResilientHttpClient(
            provider_name=self.name,
            limiter=comicvine_limiter,
            user_agent="Aarkib/0.1.0 (https://github.com/syfq91/aarkib)",
        )

    def get_api_key(self) -> str | None:
        """Resolves ComicVine API key from instance, environment, or Flask config."""
        if self._api_key:
            return self._api_key
        env_key = os.getenv("COMICVINE_API_KEY")
        if env_key:
            return env_key.strip()
        try:
            cfg_key = current_app.config.get("COMICVINE_API_KEY")
            if cfg_key:
                return str(cfg_key).strip()
        except Exception:
            pass
        return None

    def search(
        self,
        query: str,
        media_type: str = "comic",
        year: str | None = None,
    ) -> list[MetadataSearchResult]:
        _ = media_type
        if not query or not query.strip():
            return []

        clean_query = query.strip()
        api_key = self.get_api_key()
        if not api_key:
            logger.debug(
                "ComicVine API key not configured. Skipping online search for %r.",
                clean_query,
            )
            return []

        encoded = urllib.parse.quote(clean_query)
        url = (
            f"{COMICVINE_BASE}/search/?api_key={api_key}&format=json"
            f"&resources=issue,volume&query={encoded}&limit=10"
        )

        data = self.client.get_json(url)
        if not isinstance(data, dict) or not data.get("results"):
            return []

        results: list[MetadataSearchResult] = []
        for item in data.get("results", []):
            item_id = str(item.get("id") or "")
            if not item_id:
                continue

            # Determine title: Issue name, or Volume name #Issue
            volume_info = item.get("volume") or {}
            volume_name = (
                volume_info.get("name") if isinstance(volume_info, dict) else None
            )
            issue_num = item.get("issue_number")
            raw_name = item.get("name")

            if volume_name and issue_num:
                display_title = f"{volume_name} #{issue_num}"
                if raw_name:
                    display_title += f": {raw_name}"
            elif raw_name:
                display_title = raw_name
            elif volume_name:
                display_title = volume_name
            else:
                display_title = clean_query

            # Image cover
            img_info = item.get("image") or {}
            poster_url = None
            if isinstance(img_info, dict):
                poster_url = (
                    img_info.get("medium_url")
                    or img_info.get("super_url")
                    or img_info.get("small_url")
                )

            # Date
            cover_date = item.get("cover_date")
            rel_year = str(cover_date)[:4] if cover_date else None

            # Description (clean HTML tags if present)
            raw_desc = item.get("description") or item.get("deck") or ""
            clean_desc = re.sub(r"<[^>]+>", "", raw_desc).strip() if raw_desc else None

            score = compute_confidence_score(
                query=clean_query,
                candidate_title=display_title,
                target_year=year,
                candidate_year=rel_year,
            )

            resource_type = item.get("resource_type") or "issue"
            full_id = f"{resource_type}/{item_id}"

            results.append(
                MetadataSearchResult(
                    id=full_id,
                    provider=self.name,
                    title=display_title,
                    year=rel_year,
                    creators=[],
                    overview=clean_desc,
                    poster_url=poster_url,
                    media_type="comic",
                    score=score,
                    extra={
                        "volume": volume_name,
                        "issue_number": issue_num,
                        "resource_type": resource_type,
                    },
                )
            )

        return results

    def fetch_details(
        self,
        external_id: str,
        media_type: str = "comic",
    ) -> MediaMetadataDetails | None:
        _ = media_type
        clean_id = str(external_id).strip()
        if not clean_id:
            return None

        api_key = self.get_api_key()
        if not api_key:
            return None

        # ID can be in format "issue/12345" or raw "12345"
        if "/" in clean_id:
            res_type, raw_id = clean_id.split("/", 1)
        else:
            res_type, raw_id = "issue", clean_id

        # ComicVine uses 4000-id for issues and 4050-id for volumes
        prefix = "4000" if res_type == "issue" else "4050"
        url = f"{COMICVINE_BASE}/{res_type}/{prefix}-{raw_id}/?api_key={api_key}&format=json"

        data = self.client.get_json(url)
        if not isinstance(data, dict) or not data.get("results"):
            return None

        item = data["results"]
        volume_info = item.get("volume") or {}
        volume_name = volume_info.get("name") if isinstance(volume_info, dict) else None
        issue_num = item.get("issue_number")
        raw_name = item.get("name")

        if volume_name and issue_num:
            display_title = f"{volume_name} #{issue_num}"
            if raw_name:
                display_title += f": {raw_name}"
        elif raw_name:
            display_title = raw_name
        elif volume_name:
            display_title = volume_name
        else:
            display_title = "Unknown Comic"

        # Creators (writers, pencillers, etc.)
        creators: list[str] = []
        for person in item.get("person_credits", []):
            if isinstance(person, dict) and person.get("name"):
                creators.append(person["name"])

        img_info = item.get("image") or {}
        poster_url = None
        if isinstance(img_info, dict):
            poster_url = (
                img_info.get("super_url")
                or img_info.get("medium_url")
                or img_info.get("small_url")
            )

        cover_date = item.get("cover_date")
        rel_date = str(cover_date)[:10] if cover_date else None

        raw_desc = item.get("description") or item.get("deck") or ""
        clean_desc = re.sub(r"<[^>]+>", "", raw_desc).strip() if raw_desc else None

        # Publisher
        publisher = None
        if isinstance(volume_info, dict) and volume_info.get("publisher"):
            pub_info = volume_info["publisher"]
            if isinstance(pub_info, dict):
                publisher = pub_info.get("name")

        series_idx = None
        if issue_num:
            try:
                series_idx = float(issue_num)
            except ValueError, TypeError:
                pass

        return MediaMetadataDetails(
            id=clean_id,
            provider=self.name,
            title=display_title,
            creators=creators,
            series=volume_name,
            series_index=series_idx,
            overview=clean_desc,
            publisher=publisher,
            release_date=rel_date,
            language="en",
            poster_url=poster_url,
            extra={
                "issue_number": issue_num,
                "volume": volume_name,
            },
        )
