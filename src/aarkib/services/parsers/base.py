from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BaseParsedMetadata:
    """Generalized metadata extracted from any media file."""

    title: str
    creators: list[str] = field(default_factory=list)
    description: str | None = None
    publisher: str | None = None
    language: str | None = "en"
    publication_date: str | None = None
    tags: list[str] = field(default_factory=list)
    cover_bytes: bytes | None = None
    file_format: str = "epub"
    media_type: str = "book"


@dataclass
class ParsedBookMetadata(BaseParsedMetadata):
    """Book- and comic-specific parsed metadata."""

    authors: list[str] = field(default_factory=list)
    isbn: str | None = None
    series: str | None = None
    series_index: float | None = None
    page_count: int | None = None

    def __post_init__(self) -> None:
        if not self.authors and self.creators:
            self.authors = list(self.creators)
        elif self.authors and not self.creators:
            self.creators = list(self.authors)


@dataclass
class ParsedVideoMetadata(BaseParsedMetadata):
    """Video/Movie/TV show parsed metadata."""

    duration: float | None = None
    resolution_width: int | None = None
    resolution_height: int | None = None
    codec: str | None = None
    season: int | None = None
    episode: int | None = None
    series: str | None = None
    series_index: float | None = None
    authors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.media_type = "video"
        if not self.authors and self.creators:
            self.authors = list(self.creators)
        elif self.authors and not self.creators:
            self.creators = list(self.authors)


@dataclass
class ParsedAudioMetadata(BaseParsedMetadata):
    """Audio track, music, or audiobook parsed metadata."""

    album: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    duration: float | None = None
    bitrate: int | None = None
    series: str | None = None
    series_index: float | None = None
    authors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.media_type = "audio"
        if not self.series and self.album:
            self.series = self.album
        if self.series_index is None and self.track_number is not None:
            self.series_index = float(self.track_number)
        if not self.authors and self.creators:
            self.authors = list(self.creators)
        elif self.authors and not self.creators:
            self.creators = list(self.authors)


ParserFunc = Callable[[Path], BaseParsedMetadata | None]
PARSER_REGISTRY: dict[str, ParserFunc] = {}


def register_parser(extension: str, parser_func: ParserFunc) -> None:
    """Registers a parser function for a given file extension."""
    norm_ext = (
        extension.lower() if extension.startswith(".") else f".{extension.lower()}"
    )
    PARSER_REGISTRY[norm_ext] = parser_func


def extract_metadata_from_file(file_path: Path) -> BaseParsedMetadata | None:
    """Dispatches to the appropriate parser based on registered extension or default parsers."""
    ext = file_path.suffix.lower()

    if ext in PARSER_REGISTRY:
        return PARSER_REGISTRY[ext](file_path)

    if ext == ".epub":
        from aarkib.services.parsers.epub import parse_epub

        return parse_epub(file_path)
    elif ext in (".cbz", ".zip", ".cbr"):
        from aarkib.services.parsers.cbz import parse_cbz

        return parse_cbz(file_path)
    elif ext in (".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v"):
        from aarkib.services.parsers.video import parse_video

        return parse_video(file_path)
    elif ext in (
        ".mp3",
        ".m4a",
        ".m4b",
        ".flac",
        ".ogg",
        ".opus",
        ".wav",
        ".aac",
    ):
        from aarkib.services.parsers.audio import parse_audio

        return parse_audio(file_path)

    return None
