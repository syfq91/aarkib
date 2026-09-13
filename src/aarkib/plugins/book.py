from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from aarkib.plugins.base import MediaPlugin
from aarkib.services.parsers.base import BaseParsedMetadata

if TYPE_CHECKING:
    from flask import Blueprint, Flask


class BookMediaPlugin(MediaPlugin):
    """Built-in media plugin for books and comics (EPUB, CBZ, CBR, ZIP)."""

    name = "books"
    media_type = "book"
    supported_media_types: ClassVar[set[str]] = {"book", "comic"}
    supported_extensions: ClassVar[set[str]] = {".epub", ".cbz", ".zip", ".cbr", ".pdf"}

    def parse_metadata(self, file_path: Path) -> BaseParsedMetadata | None:
        """Parses EPUB, PDF, or Comic archive metadata."""
        ext = file_path.suffix.lower()
        if ext == ".epub":
            from aarkib.services.parsers.epub import parse_epub

            return parse_epub(file_path)
        elif ext == ".pdf":
            from aarkib.services.parsers.pdf import parse_pdf

            return parse_pdf(file_path)
        elif ext in (".cbz", ".zip", ".cbr"):
            from aarkib.services.parsers.cbz import parse_cbz

            return parse_cbz(file_path)
        return None

    def extract_cover(self, file_path: Path) -> bytes | None:
        """Extracts cover bytes from EPUB, PDF, or comic archive."""
        meta = self.parse_metadata(file_path)
        return meta.cover_bytes if meta else None

    def get_player_url(
        self, item_id: int, file_format: str | None = None
    ) -> str | None:
        """Returns web reader URL for EPUB, PDF, or CBZ books."""
        fmt = (file_format or "").lower().lstrip(".")
        if fmt in ("cbz", "zip", "cbr"):
            return f"/reader/cbz/{item_id}"
        elif fmt == "pdf":
            return f"/reader/pdf/{item_id}"
        return f"/reader/epub/{item_id}"

    def get_playback_info(
        self, item: Any, user_id: int | None = None
    ) -> dict[str, Any]:
        """Returns reading descriptor including reader URL, page count, and format."""
        info = super().get_playback_info(item, user_id=user_id)
        info.update(
            {
                "playback_strategy": "read",
                "reader_url": self.get_player_url(item.id, item.file_format),
                "page_count": getattr(item, "page_count", None),
                "isbn": getattr(item, "isbn", None),
            }
        )
        return info

    def register_routes(self, app: Flask | None = None) -> Blueprint | None:
        """Returns the web reader blueprint for EPUB and CBZ formats."""
        from aarkib.routes.reader import reader_bp

        return reader_bp

    def check_health(self) -> dict[str, Any]:
        """Verifies book plugin dependencies."""
        return {
            "status": "ok",
            "plugin": self.name,
            "media_type": self.media_type,
            "supported_extensions": sorted(self.supported_extensions),
        }
