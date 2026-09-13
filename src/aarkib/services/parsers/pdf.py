from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from aarkib.services.parsers.base import ParsedBookMetadata
from aarkib.services.parsers.epub import extract_series_from_title

logger = logging.getLogger(__name__)


def parse_pdf(file_path: Path) -> ParsedBookMetadata | None:
    """Extracts metadata, page count, and cover art from a PDF document."""
    try:
        reader = PdfReader(str(file_path))
        meta = reader.metadata

        raw_title = (meta.title if meta and meta.title else "").strip()
        title = raw_title if raw_title else file_path.stem

        # Extract authors from author or creator fields
        authors: list[str] = []
        raw_author = (meta.author if meta and meta.author else "") or (
            meta.creator if meta and meta.creator else ""
        )
        if raw_author and raw_author.strip():
            # Delimiters: comma or semicolon
            authors = [
                a.strip() for a in raw_author.replace(";", ",").split(",") if a.strip()
            ]

        # Extract description/subject
        description: str | None = None
        if meta and meta.subject and meta.subject.strip():
            description = meta.subject.strip()

        # Extract publication date
        publication_date: str | None = None
        if meta and meta.creation_date:
            cdate = meta.creation_date
            if isinstance(cdate, datetime):
                publication_date = cdate.strftime("%Y-%m-%d")
            else:
                publication_date = str(cdate)[:10]

        # Page count
        page_count: int = len(reader.pages)

        # Extract series and index from title or file stem
        series_name, series_index, clean_title = extract_series_from_title(title)
        if not series_name:
            series_name, series_index, clean_title = extract_series_from_title(
                file_path.stem
            )
        if series_name and clean_title:
            title = clean_title

        # Extract cover bytes from the first page if embedded images exist
        cover_bytes: bytes | None = None
        if reader.pages:
            try:
                first_page = reader.pages[0]
                if hasattr(first_page, "images") and first_page.images:
                    # Pick the image with the largest payload on the first page
                    largest_img = max(
                        first_page.images,
                        key=lambda img: len(img.data) if hasattr(img, "data") else 0,
                    )
                    if hasattr(largest_img, "data") and len(largest_img.data) > 100:
                        cover_bytes = largest_img.data
            except Exception as img_exc:
                logger.debug(
                    "Could not extract cover image from first page of %s: %s",
                    file_path,
                    img_exc,
                )

        return ParsedBookMetadata(
            title=title,
            authors=authors if authors else ["Unknown Author"],
            description=description,
            series=series_name,
            series_index=series_index,
            tags=[],
            cover_bytes=cover_bytes,
            page_count=page_count,
            publication_date=publication_date,
            file_format="pdf",
            media_type="book",
        )
    except Exception as exc:
        logger.warning("Failed to parse PDF %s: %s", file_path, exc, exc_info=True)
        series_name, series_index, clean_t = extract_series_from_title(file_path.stem)
        return ParsedBookMetadata(
            title=clean_t,
            authors=["Unknown Author"],
            series=series_name,
            series_index=series_index,
            file_format="pdf",
            media_type="book",
        )
