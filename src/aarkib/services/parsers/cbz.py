from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

import defusedxml.ElementTree as ET

from aarkib.services.parsers.base import ParsedBookMetadata
from aarkib.services.parsers.epub import extract_series_from_title

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".avif",
    ".bmp",
    ".tiff",
}


def natural_sort_key(s: str) -> list[int | str]:
    return [
        int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)
    ]


def parse_cbz(file_path: Path) -> ParsedBookMetadata | None:
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            namelist = zf.namelist()
            # Filter image pages
            image_names = [
                name
                for name in namelist
                if Path(name).suffix.lower() in IMAGE_EXTENSIONS
                and not Path(name).name.startswith(".")
                and "__MACOSX" not in name
            ]
            image_names.sort(key=natural_sort_key)

            title = file_path.stem
            authors: list[str] = []
            description: str | None = None
            series: str | None = None
            series_index: float | None = None
            tags: list[str] = []
            publication_date: str | None = None
            page_count: int = len(image_names)

            # Check for ComicInfo.xml
            comic_info_name = None
            for name in namelist:
                if Path(name).name.lower() == "comicinfo.xml":
                    comic_info_name = name
                    break

            if comic_info_name:
                try:
                    xml_data = zf.read(comic_info_name)
                    root = ET.fromstring(xml_data)

                    title_elem = root.find("Title")
                    series_elem = root.find("Series")
                    number_elem = root.find("Number")
                    summary_elem = root.find("Summary")
                    writer_elem = root.find("Writer")
                    genre_elem = root.find("Genre")
                    tags_elem = root.find("Tags")
                    year_elem = root.find("Year")
                    month_elem = root.find("Month")
                    day_elem = root.find("Day")

                    if title_elem is not None and title_elem.text:
                        title = title_elem.text.strip()
                    if series_elem is not None and series_elem.text:
                        series = series_elem.text.strip()
                    if number_elem is not None and number_elem.text:
                        try:
                            series_index = float(number_elem.text.strip())
                        except ValueError:
                            pass
                    if summary_elem is not None and summary_elem.text:
                        description = summary_elem.text.strip()
                    if writer_elem is not None and writer_elem.text:
                        authors = [
                            a.strip() for a in writer_elem.text.split(",") if a.strip()
                        ]
                    if genre_elem is not None and genre_elem.text:
                        tags.extend(
                            [g.strip() for g in genre_elem.text.split(",") if g.strip()]
                        )
                    if tags_elem is not None and tags_elem.text:
                        tags.extend(
                            [t.strip() for t in tags_elem.text.split(",") if t.strip()]
                        )

                    if year_elem is not None and year_elem.text:
                        year = year_elem.text.strip()
                        month = (
                            month_elem.text.strip().zfill(2)
                            if month_elem is not None and month_elem.text
                            else "01"
                        )
                        day = (
                            day_elem.text.strip().zfill(2)
                            if day_elem is not None and day_elem.text
                            else "01"
                        )
                        publication_date = f"{year}-{month}-{day}"
                except Exception:
                    logger.debug(
                        "Failed to parse ComicInfo.xml for %s: %s",
                        file_path,
                        exc_info=True,
                    )

            if not series:
                s_name, s_idx, _ = extract_series_from_title(title)
                if not s_name:
                    s_name, s_idx, _ = extract_series_from_title(file_path.stem)
                if s_name:
                    series = s_name
                    if series_index is None:
                        series_index = s_idx

            cover_bytes: bytes | None = None
            if image_names:
                try:
                    cover_bytes = zf.read(image_names[0])
                except Exception:
                    logger.debug(
                        "Failed to read cover from %s: %s", file_path, exc_info=True
                    )

            return ParsedBookMetadata(
                title=title,
                creators=authors if authors else ["Unknown Author"],
                description=description,
                series=series,
                series_index=series_index,
                tags=list(set(tags)),
                cover_bytes=cover_bytes,
                page_count=page_count,
                publication_date=publication_date,
                file_format="cbz",
                media_type="comic",
            )
    except Exception:
        s_name, s_idx, clean_t = extract_series_from_title(file_path.stem)
        return ParsedBookMetadata(
            title=clean_t,
            creators=["Unknown Author"],
            series=s_name,
            series_index=s_idx,
            file_format="cbz",
            media_type="comic",
        )
