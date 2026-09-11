import logging
import urllib.parse

from aarkib.services.metadata.base import (
    MediaMetadataDetails,
    MetadataProvider,
    MetadataSearchResult,
    compute_confidence_score,
)
from aarkib.services.metadata.client import ResilientHttpClient
from aarkib.services.metadata.limiter import books_limiter

logger = logging.getLogger(__name__)


class OpenLibraryProvider(MetadataProvider):
    """Metadata provider connecting to Open Library Search & Books APIs."""

    name = "openlibrary"
    supported_media_types = {"book", "comic", "all"}

    def __init__(self, client: ResilientHttpClient | None = None) -> None:
        self.client = client or ResilientHttpClient(
            provider_name=self.name,
            limiter=books_limiter,
        )

    def search(
        self,
        query: str,
        media_type: str = "all",
        year: str | None = None,
    ) -> list[MetadataSearchResult]:
        if not query or not query.strip():
            return []

        clean_query = query.strip()
        encoded = urllib.parse.quote(clean_query)
        url = f"https://openlibrary.org/search.json?q={encoded}&limit=10"

        data = self.client.get_json(url)
        if not isinstance(data, dict) or not data.get("docs"):
            return []

        results: list[MetadataSearchResult] = []
        for doc in data.get("docs", []):
            title = doc.get("title") or clean_query
            item_year = (
                str(doc.get("first_publish_year"))
                if doc.get("first_publish_year")
                else None
            )
            creators = doc.get("author_name", [])
            cover_id = doc.get("cover_i")
            poster_url = (
                f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
                if cover_id
                else None
            )

            # Work or Edition key
            raw_key = doc.get("key", "")
            if raw_key.startswith("/works/"):
                raw_key = raw_key[len("/works/") :]
            elif raw_key.startswith("/"):
                raw_key = raw_key[1:]
            work_key = raw_key or doc.get("cover_edition_key")
            if not work_key:
                continue

            score = compute_confidence_score(
                query=clean_query,
                candidate_title=title,
                target_year=year,
                candidate_year=item_year,
            )

            overview = None
            if doc.get("first_sentence"):
                fs = doc["first_sentence"]
                overview = (
                    fs.get("value")
                    if isinstance(fs, dict)
                    else (fs[0] if isinstance(fs, list) else str(fs))
                )

            results.append(
                MetadataSearchResult(
                    id=work_key,
                    provider=self.name,
                    title=title,
                    year=item_year,
                    creators=creators,
                    overview=overview,
                    poster_url=poster_url,
                    media_type="book",
                    score=score,
                    extra={"publishers": doc.get("publisher", [])[:2]},
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def fetch_details(
        self,
        external_id: str,
        media_type: str = "all",
    ) -> MediaMetadataDetails | None:
        if not external_id:
            return None

        # Clean key
        clean_id = external_id.lstrip("/")
        if clean_id.startswith("works/") or clean_id.startswith("OL"):
            work_url = (
                f"https://openlibrary.org/{clean_id}.json"
                if clean_id.startswith("works/")
                else f"https://openlibrary.org/works/{clean_id}.json"
            )
        else:
            work_url = f"https://openlibrary.org/works/{clean_id}.json"

        data = self.client.get_json(work_url)
        if not isinstance(data, dict):
            return None

        title = data.get("title") or external_id
        desc_obj = data.get("description")
        overview = (
            desc_obj.get("value")
            if isinstance(desc_obj, dict)
            else (desc_obj if isinstance(desc_obj, str) else None)
        )

        covers = data.get("covers", [])
        poster_url = (
            f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg" if covers else None
        )
        poster_bytes = self.client.get_bytes(poster_url) if poster_url else None

        genres = [str(s) for s in data.get("subjects", [])[:10]]

        return MediaMetadataDetails(
            id=clean_id,
            provider=self.name,
            title=title,
            overview=overview,
            poster_url=poster_url,
            poster_bytes=poster_bytes,
            genres=genres,
        )
