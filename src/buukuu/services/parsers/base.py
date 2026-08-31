from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedBookMetadata:
    title: str
    authors: list[str] = field(default_factory=list)
    description: str | None = None
    publisher: str | None = None
    language: str | None = "en"
    isbn: str | None = None
    publication_date: str | None = None
    series: str | None = None
    series_index: float | None = None
    tags: list[str] = field(default_factory=list)
    cover_bytes: bytes | None = None
    page_count: int | None = None
    file_format: str = "epub"


def extract_metadata_from_file(file_path: Path) -> ParsedBookMetadata | None:
    """Dispatches to the appropriate parser based on file extension."""
    ext = file_path.suffix.lower()
    if ext == ".epub":
        from buukuu.services.parsers.epub import parse_epub

        return parse_epub(file_path)
    elif ext in (".cbz", ".zip"):
        from buukuu.services.parsers.cbz import parse_cbz

        return parse_cbz(file_path)
    return None
