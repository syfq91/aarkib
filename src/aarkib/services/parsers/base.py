from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

    isbn: str | None = None
    series: str | None = None
    series_index: float | None = None
    page_count: int | None = None


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

    def __post_init__(self) -> None:
        self.media_type = "video"


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

    def __post_init__(self) -> None:
        self.media_type = "audio"
        if not self.series and self.album:
            self.series = self.album
        if self.series_index is None and self.track_number is not None:
            self.series_index = float(self.track_number)


@dataclass
class ParsedAudiobookMetadata(ParsedAudioMetadata):
    """Audiobook parsed metadata with chapter and narrator support."""

    author: str | None = None
    narrator: str | None = None
    chapters: list[dict[str, Any]] = field(default_factory=list)
    abridged: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        self.media_type = "audiobook"
        if self.author and not self.creators:
            self.creators = [self.author]
        elif not self.author and self.creators:
            self.author = self.creators[0]


@dataclass
class ParsedMusicMetadata(ParsedAudioMetadata):
    """Music track parsed metadata."""

    album_artist: str | None = None
    genre: str | None = None
    release_year: str | None = None
    is_compilation: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        self.media_type = "music"


@dataclass
class ParsedPodcastMetadata(ParsedAudioMetadata):
    """Podcast episode parsed metadata."""

    episode: int | None = None
    season: int | None = None
    episode_type: str | None = None  # full, trailer, bonus
    feed_url: str | None = None
    guid: str | None = None
    show_title: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.media_type = "podcast"
        if self.show_title and not self.series:
            self.series = self.show_title
        elif self.series and not self.show_title:
            self.show_title = self.series
        if self.series_index is None and self.episode is not None:
            self.series_index = float(self.episode)
        elif self.episode is None and self.series_index is not None:
            try:
                self.episode = int(self.series_index)
            except ValueError, TypeError:
                pass


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
    elif ext == ".pdf":
        from aarkib.services.parsers.pdf import parse_pdf

        return parse_pdf(file_path)
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
