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
from aarkib.services.metadata.limiter import musicbrainz_limiter

logger = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org/release"


class MusicBrainzProvider(MetadataProvider):
    """Metadata provider connecting to MusicBrainz and Cover Art Archive."""

    name = "musicbrainz"
    supported_media_types = {"music", "audio", "audiobook", "all"}

    def __init__(self, client: ResilientHttpClient | None = None) -> None:
        self.client = client or ResilientHttpClient(
            provider_name=self.name,
            limiter=musicbrainz_limiter,
        )

    def search(
        self,
        query: str,
        media_type: str = "music",
        year: str | None = None,
    ) -> list[MetadataSearchResult]:
        if not query or not query.strip():
            return []

        clean_query = query.strip()
        encoded = urllib.parse.quote(clean_query)
        url = f"{MB_BASE}/release/?query={encoded}&fmt=json&limit=10"

        data = self.client.get_json(url)
        if not isinstance(data, dict) or not data.get("releases"):
            return []

        results: list[MetadataSearchResult] = []
        for rel in data.get("releases", []):
            mbid = rel.get("id")
            if not mbid:
                continue

            title = rel.get("title") or clean_query
            date_str = rel.get("date")
            rel_year = str(date_str)[:4] if date_str else None

            # Artists
            artists = [
                a.get("name")
                for a in rel.get("artist-credit", [])
                if isinstance(a, dict) and a.get("name")
            ]

            # Cover Art Archive direct thumbnail
            poster_url = f"{CAA_BASE}/{mbid}/front-500"

            score = compute_confidence_score(
                query=clean_query,
                candidate_title=title,
                target_year=year,
                candidate_year=rel_year,
            )

            # Extra details
            label_info = rel.get("label-info", [])
            publisher = (
                label_info[0].get("label", {}).get("name")
                if label_info and isinstance(label_info[0], dict)
                else None
            )

            results.append(
                MetadataSearchResult(
                    id=mbid,
                    provider=self.name,
                    title=title,
                    year=rel_year,
                    creators=artists,
                    overview=f"Release by {', '.join(artists)}" if artists else None,
                    poster_url=poster_url,
                    media_type="music",
                    score=score,
                    extra={
                        "publisher": publisher,
                        "status": rel.get("status"),
                        "country": rel.get("country"),
                    },
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def fetch_details(
        self,
        external_id: str,
        media_type: str = "music",
    ) -> MediaMetadataDetails | None:
        if not external_id:
            return None

        clean_id = external_id.strip()
        url = f"{MB_BASE}/release/{clean_id}?inc=artists+recordings+release-groups+labels+genres&fmt=json"
        data = self.client.get_json(url)
        if not isinstance(data, dict):
            return None

        title = data.get("title") or clean_id
        artists = [
            a.get("name")
            for a in data.get("artist-credit", [])
            if isinstance(a, dict) and a.get("name")
        ]

        date_str = data.get("date")
        label_info = data.get("label-info", [])
        publisher = (
            label_info[0].get("label", {}).get("name")
            if label_info and isinstance(label_info[0], dict)
            else None
        )

        genres = [g.get("name") for g in data.get("genres", []) if g.get("name")]

        # Query Cover Art Archive JSON to find canonical front artwork
        caa_data = self.client.get_json(f"{CAA_BASE}/{clean_id}")
        poster_url = None
        if isinstance(caa_data, dict):
            images = caa_data.get("images", [])
            for img in images:
                if img.get("front"):
                    poster_url = (
                        img.get("thumbnails", {}).get("large")
                        or img.get("thumbnails", {}).get("500")
                        or img.get("image")
                    )
                    break
            if not poster_url and images:
                poster_url = images[0].get("image")

        if not poster_url:
            poster_url = f"{CAA_BASE}/{clean_id}/front-500"

        poster_bytes = self.client.get_bytes(poster_url) if poster_url else None

        return MediaMetadataDetails(
            id=clean_id,
            provider=self.name,
            title=title,
            album=title,
            album_artist=artists[0] if artists else None,
            creators=artists,
            overview=f"Album released {date_str or ''} by {', '.join(artists)}",
            poster_url=poster_url,
            poster_bytes=poster_bytes,
            release_date=date_str,
            publisher=publisher,
            genres=genres,
        )
