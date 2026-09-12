from __future__ import annotations

import logging
import os
import re
from typing import ClassVar

from flask import current_app, has_app_context

from aarkib.services.metadata.base import (
    MediaMetadataDetails,
    MetadataProvider,
    MetadataSearchResult,
    compute_confidence_score,
)
from aarkib.services.metadata.client import ResilientHttpClient
from aarkib.services.metadata.limiter import tmdb_limiter

logger = logging.getLogger(__name__)

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


class TMDBProvider(MetadataProvider):
    """Metadata provider connecting to The Movie Database (TMDB) API."""

    name = "tmdb"
    supported_media_types: ClassVar[set[str]] = {"video", "movie", "tv", "all"}

    def __init__(
        self,
        api_key: str | None = None,
        client: ResilientHttpClient | None = None,
    ) -> None:
        self._api_key_override = api_key
        self.client = client or ResilientHttpClient(
            provider_name=self.name,
            limiter=tmdb_limiter,
        )

    def _get_api_key(self) -> str | None:
        """Retrieves configured TMDB API key."""
        if self._api_key_override:
            return self._api_key_override
        if has_app_context():
            key = current_app.config.get("TMDB_API_KEY")
            if key:
                return key
        return os.getenv("AARKIB_TMDB_API_KEY", os.getenv("TMDB_API_KEY"))

    def _build_auth(self) -> tuple[dict[str, str], dict[str, str]]:
        """Returns headers and query params needed for TMDB authentication."""
        api_key = self._get_api_key()
        if not api_key:
            return {}, {}
        # Support both v4 Bearer tokens and standard v3 API keys
        if api_key.startswith("eyJ"):
            return {"Authorization": f"Bearer {api_key}"}, {}
        return {}, {"api_key": api_key}

    def search(
        self,
        query: str,
        media_type: str = "video",
        year: str | None = None,
    ) -> list[MetadataSearchResult]:
        if not query:
            return []

        auth_headers, auth_params = self._build_auth()
        clean_query = query.strip()

        # Extract season/episode hints if present in query e.g. "Breaking Bad S01E02"
        tv_match = re.search(r"\bS(\d{1,2})E(\d{1,2})\b", clean_query, re.IGNORECASE)
        search_query = clean_query
        target_season = None
        target_episode = None
        if tv_match:
            target_season = int(tv_match.group(1))
            target_episode = int(tv_match.group(2))
            search_query = clean_query[: tv_match.start()].strip() or clean_query

        results: list[MetadataSearchResult] = []

        # 1. Search Movies
        movie_data = None
        if media_type in ("movie", "video", "all") and not (
            tv_match and media_type == "tv"
        ):
            movie_params = dict(auth_params)
            movie_params["query"] = search_query
            if year:
                movie_params["year"] = str(year)[:4]
            movie_data = self.client.get_json(
                f"{TMDB_API_BASE}/search/movie",
                params=movie_params,
                headers=auth_headers,
            )
        if isinstance(movie_data, dict):
            for m in movie_data.get("results", [])[:8]:
                m_id = str(m.get("id"))
                title = m.get("title") or search_query
                rel_date = m.get("release_date")
                m_year = str(rel_date)[:4] if rel_date else None
                poster_path = m.get("poster_path")
                poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None
                overview = m.get("overview")

                score = compute_confidence_score(
                    query=search_query,
                    candidate_title=title,
                    target_year=year,
                    candidate_year=m_year,
                )

                results.append(
                    MetadataSearchResult(
                        id=f"movie:{m_id}",
                        provider=self.name,
                        title=title,
                        year=m_year,
                        creators=[],
                        overview=overview,
                        poster_url=poster_url,
                        media_type="video",
                        score=score,
                        extra={
                            "media_subtype": "movie",
                            "vote_average": m.get("vote_average"),
                        },
                    )
                )

        # 2. Search TV Shows
        if media_type in ("tv", "video", "all") or tv_match:
            tv_params = dict(auth_params)
            tv_params["query"] = search_query
            if year:
                tv_params["first_air_date_year"] = str(year)[:4]
            tv_data = self.client.get_json(
                f"{TMDB_API_BASE}/search/tv",
                params=tv_params,
                headers=auth_headers,
            )
        if isinstance(tv_data, dict):
            for t in tv_data.get("results", [])[:8]:
                t_id = str(t.get("id"))
                title = t.get("name") or search_query
                first_air = t.get("first_air_date")
                t_year = str(first_air)[:4] if first_air else None
                poster_path = t.get("poster_path")
                poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None
                overview = t.get("overview")

                score = compute_confidence_score(
                    query=search_query,
                    candidate_title=title,
                    target_year=year,
                    candidate_year=t_year,
                )

                candidate_id = f"tv:{t_id}"
                candidate_title = title
                if target_season is not None and target_episode is not None:
                    candidate_id = f"tv:{t_id}:s{target_season}:e{target_episode}"
                    candidate_title = (
                        f"{title} S{target_season:02d}E{target_episode:02d}"
                    )

                results.append(
                    MetadataSearchResult(
                        id=candidate_id,
                        provider=self.name,
                        title=candidate_title,
                        year=t_year,
                        creators=[],
                        overview=overview,
                        poster_url=poster_url,
                        media_type="video",
                        score=score,
                        extra={
                            "media_subtype": "tv",
                            "vote_average": t.get("vote_average"),
                        },
                    )
                )

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def fetch_details(
        self,
        external_id: str,
        media_type: str = "video",
    ) -> MediaMetadataDetails | None:
        _ = media_type
        if not external_id:
            return None

        auth_headers, auth_params = self._build_auth()
        clean_id = external_id.strip()

        # Handle episode ID: "tv:12345:s1:e2"
        episode_match = re.match(r"^tv:(\d+):s(\d+):e(\d+)$", clean_id, re.IGNORECASE)
        if episode_match:
            tv_id = episode_match.group(1)
            season_num = int(episode_match.group(2))
            episode_num = int(episode_match.group(3))

            ep_url = (
                f"{TMDB_API_BASE}/tv/{tv_id}/season/{season_num}/episode/{episode_num}"
            )
            ep_data = self.client.get_json(
                ep_url, params=auth_params, headers=auth_headers
            )

            # Also fetch show title for series name
            show_data = self.client.get_json(
                f"{TMDB_API_BASE}/tv/{tv_id}", params=auth_params, headers=auth_headers
            )
            show_name = show_data.get("name") if isinstance(show_data, dict) else None

            if isinstance(ep_data, dict):
                ep_name = ep_data.get("name")
                title = (
                    f"{show_name} - {ep_name}"
                    if show_name and ep_name
                    else (ep_name or show_name or clean_id)
                )
                still_path = ep_data.get("still_path")
                poster_url = f"{TMDB_IMAGE_BASE}{still_path}" if still_path else None
                if (
                    not poster_url
                    and isinstance(show_data, dict)
                    and show_data.get("poster_path")
                ):
                    poster_url = f"{TMDB_IMAGE_BASE}{show_data.get('poster_path')}"

                poster_bytes = self.client.get_bytes(poster_url) if poster_url else None
                air_date = ep_data.get("air_date")
                directors = [
                    c.get("name")
                    for c in ep_data.get("crew", [])
                    if c.get("job") == "Director" and c.get("name")
                ]

                return MediaMetadataDetails(
                    id=clean_id,
                    provider=self.name,
                    title=title,
                    creators=directors,
                    overview=ep_data.get("overview"),
                    poster_url=poster_url,
                    poster_bytes=poster_bytes,
                    release_date=air_date,
                    season=season_num,
                    episode=episode_num,
                    duration=(ep_data.get("runtime") or 0) * 60.0
                    if ep_data.get("runtime")
                    else None,
                    extra={"show_name": show_name},
                )

        # Handle standard movie or TV show:
        if clean_id.startswith("tv:"):
            tv_id = clean_id.split(":", 1)[1]
            tv_url = f"{TMDB_API_BASE}/tv/{tv_id}"
            tv_data = self.client.get_json(
                tv_url, params=auth_params, headers=auth_headers
            )
            if not isinstance(tv_data, dict):
                return None

            title = tv_data.get("name") or clean_id
            poster_path = tv_data.get("poster_path")
            poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None
            poster_bytes = self.client.get_bytes(poster_url) if poster_url else None
            genres = [g.get("name") for g in tv_data.get("genres", []) if g.get("name")]
            creators = [
                c.get("name") for c in tv_data.get("created_by", []) if c.get("name")
            ]

            return MediaMetadataDetails(
                id=clean_id,
                provider=self.name,
                title=title,
                creators=creators,
                overview=tv_data.get("overview"),
                poster_url=poster_url,
                poster_bytes=poster_bytes,
                release_date=tv_data.get("first_air_date"),
                genres=genres,
                language=tv_data.get("original_language"),
            )

        # Fallback: movie lookup
        movie_id = (
            clean_id.split(":", 1)[1] if clean_id.startswith("movie:") else clean_id
        )
        movie_params = dict(auth_params)
        movie_params["append_to_response"] = "credits"
        movie_url = f"{TMDB_API_BASE}/movie/{movie_id}"
        movie_data = self.client.get_json(
            movie_url, params=movie_params, headers=auth_headers
        )
        if not isinstance(movie_data, dict):
            return None

        title = movie_data.get("title") or clean_id
        poster_path = movie_data.get("poster_path")
        poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None
        poster_bytes = self.client.get_bytes(poster_url) if poster_url else None
        genres = [g.get("name") for g in movie_data.get("genres", []) if g.get("name")]

        directors = []
        credits = movie_data.get("credits", {})
        if isinstance(credits, dict):
            directors = [
                c.get("name")
                for c in credits.get("crew", [])
                if c.get("job") == "Director" and c.get("name")
            ]

        production_cos = movie_data.get("production_companies", [])
        publisher = production_cos[0].get("name") if production_cos else None
        duration = (
            float(movie_data.get("runtime") * 60) if movie_data.get("runtime") else None
        )

        return MediaMetadataDetails(
            id=clean_id,
            provider=self.name,
            title=title,
            creators=directors,
            overview=movie_data.get("overview"),
            poster_url=poster_url,
            poster_bytes=poster_bytes,
            release_date=movie_data.get("release_date"),
            publisher=publisher,
            genres=genres,
            language=movie_data.get("original_language"),
            duration=duration,
        )
