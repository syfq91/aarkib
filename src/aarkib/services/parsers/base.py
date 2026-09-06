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

    return None
