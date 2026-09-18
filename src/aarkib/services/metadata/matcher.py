from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aarkib.models import MediaItem
    from aarkib.services.metadata.base import MediaMetadataDetails
    from aarkib.services.metadata.registry import MetadataProviderRegistry

logger = logging.getLogger(__name__)


@dataclass
class CandidateMatch:
    """Represents a scored candidate match from a metadata provider."""

    id: str
    provider: str
    title: str
    creators: list[str] = field(default_factory=list)
    year: str | None = None
    confidence_score: float = 0.0
    details: MediaMetadataDetails | None = None
    overview: str | None = None
    poster_url: str | None = None
    media_type: str = "all"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serializes candidate match for API presentation and UI consumption."""
        return {
            "id": self.id,
            "provider": self.provider,
            "title": self.title,
            "creators": self.creators,
            "year": self.year,
            "score": round(self.confidence_score, 2),
            "confidence_score": round(self.confidence_score, 2),
            "overview": self.overview,
            "poster_url": self.poster_url,
            "media_type": self.media_type,
            "extra": self.extra,
            "has_details": self.details is not None,
        }


def _clean_text(text: str) -> str:
    """Lowercases text and strips non-alphanumeric punctuation."""
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return " ".join(cleaned.split())


def _clean_isbn(isbn: str | None) -> str:
    """Normalizes ISBN string by removing hyphens and spaces."""
    if not isbn:
        return ""
    return re.sub(r"[\s\-]", "", str(isbn)).upper()


def _tokenize(text: str) -> set[str]:
    """Extracts lowercase words of length > 1."""
    cleaned = _clean_text(text)
    return {w for w in cleaned.split() if len(w) > 1}


def calculate_match_confidence(
    query_title: str,
    candidate_title: str,
    query_year: str | int | None = None,
    candidate_year: str | int | None = None,
    query_creators: list[str] | None = None,
    candidate_creators: list[str] | None = None,
    query_identifiers: dict[str, str] | None = None,
    candidate_identifiers: dict[str, str] | None = None,
) -> float:
    """Calculates a deterministic match confidence score between 0.0 and 1.0.

    Evaluates:
    1. Identifier equality (exact ISBN, TMDB ID, MusicBrainz MBID) -> definitive match (1.0).
    2. Fuzzy title similarity (Token Jaccard overlap + SequenceMatcher character ratio).
    3. Subtitle containment & prefix bonus.
    4. Year proximity bonus/penalty.
    5. Creator / author name overlap.
    """
    # 1. Exact identifier equality check
    q_ids = query_identifiers or {}
    c_ids = candidate_identifiers or {}

    q_isbn = _clean_isbn(q_ids.get("isbn"))
    c_isbn = _clean_isbn(c_ids.get("isbn"))
    if q_isbn and c_isbn and q_isbn == c_isbn:
        return 1.0

    for id_key in ("tmdb_id", "comicvine_id", "musicbrainz_id", "external_id", "id"):
        q_val = str(q_ids.get(id_key) or "").strip()
        c_val = str(c_ids.get(id_key) or "").strip()
        if q_val and c_val and q_val.lower() == c_val.lower():
            return 1.0

    q_norm = _clean_text(query_title)
    c_norm = _clean_text(candidate_title)

    if not q_norm or not c_norm:
        return 0.0

    # 2. Title matching
    if q_norm == c_norm:
        title_score = 1.0
    else:
        # Token overlap
        q_tokens = _tokenize(q_norm)
        c_tokens = _tokenize(c_norm)
        jaccard = 0.0
        subset_match = False
        intersection: set[str] = set()
        if q_tokens and c_tokens:
            intersection = q_tokens & c_tokens
            union = q_tokens | c_tokens
            jaccard = len(intersection) / len(union) if union else 0.0
            if q_tokens.issubset(c_tokens) or c_tokens.issubset(q_tokens):
                subset_match = True

        # SequenceMatcher similarity
        seq_ratio = difflib.SequenceMatcher(None, q_norm, c_norm).ratio()

        if subset_match and intersection:
            ratio = len(intersection) / max(len(q_tokens), len(c_tokens))
            title_score = max(seq_ratio, 0.75 + (0.25 * ratio))
        else:
            containment_bonus = 0.15 if (q_norm in c_norm or c_norm in q_norm) else 0.0
            title_score = min(
                1.0,
                max(seq_ratio, (jaccard * 0.6) + (seq_ratio * 0.4)) + containment_bonus,
            )

    # 3. Year proximity modifier
    year_mod = 0.0
    if query_year and candidate_year:
        try:
            qy = int(str(query_year)[:4])
            cy = int(str(candidate_year)[:4])
            diff = abs(qy - cy)
            if diff == 0:
                year_mod = 0.15
            elif diff == 1:
                year_mod = 0.08
            elif diff <= 2:
                year_mod = 0.04
            elif diff <= 4:
                year_mod = 0.0
            else:
                year_mod = -0.15
        except ValueError, TypeError:
            pass

    # 4. Creator overlap modifier
    creator_mod = 0.0
    if query_creators and candidate_creators:
        q_authors = {_clean_text(a) for a in query_creators if a and str(a).strip()}
        c_authors = {_clean_text(a) for a in candidate_creators if a and str(a).strip()}
        if q_authors and c_authors:
            matched = False
            for qa in q_authors:
                for ca in c_authors:
                    if qa == ca or qa in ca or ca in qa:
                        matched = True
                        break
                if matched:
                    break

            if matched:
                creator_mod = 0.10
            elif len(q_authors) > 0 and len(c_authors) > 0:
                creator_mod = -0.05

    # 5. Composite score calculation
    if title_score >= 0.70 and year_mod >= 0.0:
        raw_score = (title_score * 0.80) + year_mod + creator_mod
    else:
        raw_score = (title_score * 0.75) + year_mod + creator_mod

    final_score = max(0.0, min(1.0, raw_score))
    return round(final_score, 2)


class MetadataMatcher:
    """Multi-candidate metadata search and scoring engine.

    Coordinates querying metadata providers and evaluating candidate matches
    against media items using deterministic confidence scoring.
    """

    def __init__(self, registry: MetadataProviderRegistry | None = None) -> None:
        from aarkib.services.metadata.registry import metadata_registry

        self.registry = registry or metadata_registry

    def find_candidates(
        self,
        item_or_query: MediaItem | str,
        media_type: str = "all",
        year: str | int | None = None,
        creators: list[str] | None = None,
        isbn: str | None = None,
        provider_name: str | None = None,
        limit: int = 15,
    ) -> list[CandidateMatch]:
        """Searches providers and returns candidate matches ranked by confidence score."""
        # Extract parameters from MediaItem or direct inputs
        if not isinstance(item_or_query, str) and hasattr(item_or_query, "title"):
            q_title = getattr(item_or_query, "title", "")
            q_type = getattr(item_or_query, "media_type", None) or media_type
            q_year = (
                getattr(item_or_query, "publication_date", None)
                or getattr(item_or_query, "release_year", None)
                or year
            )
            q_creators = (
                [c.name for c in item_or_query.creators]
                if getattr(item_or_query, "creators", None)
                else (creators or [])
            )
            q_isbn = getattr(item_or_query, "isbn", None) or isbn
            q_ext_id = getattr(item_or_query, "external_id", None)
        else:
            q_title = str(item_or_query)
            q_type = media_type
            q_year = year
            q_creators = creators or []
            q_isbn = isbn
            q_ext_id = None

        query_identifiers: dict[str, str] = {}
        if q_isbn:
            query_identifiers["isbn"] = q_isbn
        if q_ext_id and ":" in str(q_ext_id):
            parts = str(q_ext_id).split(":", 1)
            query_identifiers[parts[0]] = parts[1]
            query_identifiers["id"] = parts[1]

        # Formulate query string
        search_query = q_title
        is_book = q_type in ("book", "comic", "audiobook")
        if is_book and q_isbn:
            search_query = f"isbn:{q_isbn}"
        elif is_book and q_creators and q_creators[0] != "Unknown Author":
            search_query = f"{q_title} {q_creators[0]}"

        raw_results = self.registry.search(
            media_type=q_type,
            query=search_query,
            year=str(q_year)[:4] if q_year else None,
            provider_name=provider_name if provider_name != "all" else None,
        )

        candidates: list[CandidateMatch] = []
        for r in raw_results:
            c_ids = dict(r.extra.get("identifiers", {})) if r.extra else {}
            if r.id:
                c_ids["id"] = r.id
                c_ids[f"{r.provider}_id"] = r.id
            if r.extra and "isbn" in r.extra:
                c_ids["isbn"] = str(r.extra["isbn"])

            confidence = calculate_match_confidence(
                query_title=q_title,
                candidate_title=r.title,
                query_year=q_year,
                candidate_year=r.year,
                query_creators=q_creators,
                candidate_creators=r.creators,
                query_identifiers=query_identifiers,
                candidate_identifiers=c_ids,
            )

            candidates.append(
                CandidateMatch(
                    id=r.id,
                    provider=r.provider,
                    title=r.title,
                    creators=r.creators,
                    year=r.year,
                    confidence_score=confidence,
                    overview=r.overview,
                    poster_url=r.poster_url,
                    media_type=r.media_type,
                    extra=r.extra,
                )
            )

        # Sort strictly descending by confidence score
        candidates.sort(key=lambda c: c.confidence_score, reverse=True)
        return candidates[:limit]

    def select_best_match(
        self,
        candidates: list[CandidateMatch],
        min_confidence: float = 0.5,
    ) -> CandidateMatch | None:
        """Returns the highest scoring candidate match meeting the confidence threshold."""
        if not candidates:
            return None
        top = candidates[0]
        if top.confidence_score >= min_confidence:
            return top
        return None
