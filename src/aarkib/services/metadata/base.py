from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetadataSearchResult:
    """Represents a candidate match returned by an external metadata provider."""

    id: str  # Provider-specific ID (e.g., "550" or "release-mbid")
    provider: str  # e.g., "tmdb", "musicbrainz", "googlebooks", "openlibrary"
    title: str
    year: str | None = None
    creators: list[str] = field(default_factory=list)
    overview: str | None = None
    poster_url: str | None = None
    media_type: str = "all"
    score: float = 0.0  # Match confidence score from 0.0 to 1.0 (or percentage)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "title": self.title,
            "year": self.year,
            "creators": self.creators,
            "overview": self.overview,
            "poster_url": self.poster_url,
            "media_type": self.media_type,
            "score": round(self.score, 2),
            "extra": self.extra,
        }


@dataclass
class MediaMetadataDetails:
    """Comprehensive metadata payload returned when fetching full details."""

    id: str
    provider: str
    title: str
    sort_title: str | None = None
    creators: list[str] = field(default_factory=list)
    overview: str | None = None
    poster_url: str | None = None
    poster_bytes: bytes | None = None
    release_date: str | None = None
    publisher: str | None = None
    genres: list[str] = field(default_factory=list)
    language: str | None = None

    # Video-specific
    season: int | None = None
    episode: int | None = None
    duration: float | None = None

    # Audio & Music-specific
    album: str | None = None
    album_artist: str | None = None
    track_number: int | None = None
    disc_number: int | None = None

    # Audiobook-specific
    narrator: str | None = None
    chapters: list[dict[str, Any]] = field(default_factory=list)

    # Book-specific
    isbn: str | None = None
    page_count: int | None = None

    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "title": self.title,
            "sort_title": self.sort_title,
            "creators": self.creators,
            "overview": self.overview,
            "poster_url": self.poster_url,
            "release_date": self.release_date,
            "publisher": self.publisher,
            "genres": self.genres,
            "language": self.language,
            "season": self.season,
            "episode": self.episode,
            "duration": self.duration,
            "album": self.album,
            "album_artist": self.album_artist,
            "track_number": self.track_number,
            "disc_number": self.disc_number,
            "narrator": self.narrator,
            "chapters_count": len(self.chapters),
            "isbn": self.isbn,
            "page_count": self.page_count,
            "extra": self.extra,
        }


def _tokenize(text: str) -> set[str]:
    """Tokenize text into lowercase alphanumeric words."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return {word for word in cleaned.split() if len(word) > 1}


def compute_confidence_score(
    query: str,
    candidate_title: str,
    target_year: str | None = None,
    candidate_year: str | None = None,
) -> float:
    """Computes a match confidence score between 0.0 and 1.0 based on title and year similarity."""
    q_norm = (query or "").strip().lower()
    c_norm = (candidate_title or "").strip().lower()

    if not q_norm or not c_norm:
        return 0.0

    # Exact match gets high base score
    if q_norm == c_norm:
        title_score = 1.0
    else:
        # Token Jaccard similarity
        q_tokens = _tokenize(q_norm)
        c_tokens = _tokenize(c_norm)
        if not q_tokens or not c_tokens:
            title_score = 0.5 if (q_norm in c_norm or c_norm in q_norm) else 0.2
        else:
            intersection = q_tokens & c_tokens
            union = q_tokens | c_tokens
            jaccard = len(intersection) / len(union) if union else 0.0
            substring_bonus = 0.2 if (q_norm in c_norm or c_norm in q_norm) else 0.0
            title_score = min(1.0, jaccard + substring_bonus)

    # Year comparison modifier (weight 20%)
    year_bonus = 0.0
    if target_year and candidate_year:
        try:
            ty = int(str(target_year)[:4])
            cy = int(str(candidate_year)[:4])
            diff = abs(ty - cy)
            if diff == 0:
                year_bonus = 0.2
            elif diff <= 1:
                year_bonus = 0.1
            elif diff <= 3:
                year_bonus = 0.05
            else:
                year_bonus = -0.1
        except ValueError, TypeError:
            pass

    final_score = max(0.0, min(1.0, (title_score * 0.8) + year_bonus))
    return round(final_score, 2)


class MetadataProvider(ABC):
    """Abstract base class for online metadata enrichment providers."""

    name: str
    supported_media_types: set[str]

    @abstractmethod
    def search(
        self,
        query: str,
        media_type: str = "all",
        year: str | None = None,
    ) -> list[MetadataSearchResult]:
        """Searches the provider for candidate matches."""
        pass

    @abstractmethod
    def fetch_details(
        self,
        external_id: str,
        media_type: str = "all",
    ) -> MediaMetadataDetails | None:
        """Fetches detailed metadata, synopsis, and artwork for a specific candidate."""
        pass
