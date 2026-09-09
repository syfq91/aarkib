from __future__ import annotations

import logging
import posixpath
import re
import zipfile
from pathlib import Path

import defusedxml.ElementTree as ET

from aarkib.services.parsers.base import ParsedBookMetadata

logger = logging.getLogger(__name__)

NAMESPACES = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def extract_series_from_title(title_str: str) -> tuple[str | None, float | None, str]:
    """Extract (series_name, series_index, clean_title) from title or filename pattern."""
    if not title_str:
        return None, None, title_str

    # Pattern 1: [Series Name 01] Title or [Series Name #1] Title
    m = re.match(
        r"^\[([^\d\]]+?)(?:\s*(?:Vol\.?|Volume|Book|#)?\s*(\d+(?:\.\d+)?))?\]\s*(.*)$",
        title_str,
        re.IGNORECASE,
    )
    if m:
        s_name = m.group(1).strip()
        s_idx = float(m.group(2)) if m.group(2) else None
        clean_t = m.group(3).strip() or s_name
        return s_name, s_idx, clean_t

    # Pattern 2: Series Name - Vol. 01 - Book Title or Series Name - 01 - Title
    m = re.match(
        r"^(.+?)\s*[-–—]\s*(?:(?:Vol\.?|Volume|Book|#)?\s*(\d+(?:\.\d+)?))\s*[-–—]\s*(.+)$",
        title_str,
        re.IGNORECASE,
    )
    if m:
        s_name = m.group(1).strip()
        s_idx = float(m.group(2)) if m.group(2) else None
        clean_t = m.group(3).strip()
        return s_name, s_idx, clean_t

    # Pattern 3: Series Name #1 - Book Title
    m = re.match(r"^(.+?)\s+#(\d+(?:\.\d+)?)\s*[-–—:]\s*(.+)$", title_str)
    if m:
        s_name = m.group(1).strip()
        s_idx = float(m.group(2))
        clean_t = m.group(3).strip()
        return s_name, s_idx, clean_t

    # Pattern 4: Series Name Vol 01 or Series Name Vol. 1
    m = re.match(
        r"^(.+?)\s+(?:Vol\.?|Volume|Book)\s*(\d+(?:\.\d+)?)$",
        title_str,
        re.IGNORECASE,
    )
    if m:
        s_name = m.group(1).strip()
        s_idx = float(m.group(2))
        return s_name, s_idx, title_str

    return None, None, title_str


def _locate_opf_path(zf: zipfile.ZipFile) -> str:
    """Finds the OPF package path inside an EPUB archive."""
    try:
        container_data = zf.read("META-INF/container.xml")
        root = ET.fromstring(container_data)
        rootfile_elem = root.find(".//container:rootfile", namespaces=NAMESPACES)
        if rootfile_elem is not None:
            opf_path = rootfile_elem.attrib.get("full-path", "")
        else:
            opf_path = ""
    except Exception:
        opf_path = ""

    if not opf_path:
        for name in zf.namelist():
            if name.lower().endswith(".opf"):
                opf_path = name
                break
    return opf_path


def _parse_opf_metadata(metadata_elem, default_title: str) -> dict:
    """Parses the OPF <metadata> element into a dictionary of book fields."""
    parsed: dict = {
        "title": default_title,
        "authors": [],
        "description": None,
        "publisher": None,
        "language": "en",
        "isbn": None,
        "publication_date": None,
        "series": None,
        "series_index": None,
        "tags": [],
        "cover_id": None,
        "collections": {},
        "group_positions": {},
    }

    if metadata_elem is None:
        return parsed

    for elem in metadata_elem:
        tag = elem.tag
        text = (elem.text or "").strip()

        if tag.endswith("title") and text:
            parsed["title"] = text
        elif tag.endswith("creator") and text:
            parsed["authors"].append(text)
        elif tag.endswith("description") and text:
            parsed["description"] = text
        elif tag.endswith("publisher") and text:
            parsed["publisher"] = text
        elif tag.endswith("language") and text:
            parsed["language"] = text
        elif tag.endswith("identifier") and text:
            scheme = elem.attrib.get("scheme", "").upper()
            if "ISBN" in scheme or "isbn" in text.lower():
                parsed["isbn"] = text
            elif not parsed["isbn"]:
                parsed["isbn"] = text
        elif tag.endswith("date") and text:
            parsed["publication_date"] = text[:10]
        elif tag.endswith("subject") and text:
            parsed["tags"].append(text)
        elif tag.endswith("meta"):
            name_attr = elem.attrib.get("name", "")
            prop_attr = elem.attrib.get("property", "")
            content_attr = elem.attrib.get("content", "")
            meta_id = elem.attrib.get("id", "")
            refines = elem.attrib.get("refines", "")

            if name_attr == "cover":
                parsed["cover_id"] = content_attr
            elif name_attr in ("calibre:series", "series"):
                parsed["series"] = content_attr or text
            elif name_attr in ("calibre:series_index", "series_index"):
                try:
                    parsed["series_index"] = float(content_attr or text)
                except ValueError:
                    pass
            elif prop_attr == "belongs-to-collection" and text:
                parsed["collections"][meta_id or "default"] = text
            elif prop_attr == "group-position" and text:
                target_id = refines.lstrip("#") or "default"
                try:
                    parsed["group_positions"][target_id] = float(text)
                except ValueError:
                    pass
            elif prop_attr in ("schema:series", "series") and text:
                parsed["series"] = text

    return parsed


def _resolve_series(parsed: dict, title: str, stem: str) -> None:
    """Populates series/series_index from collections or title/filename heuristics."""
    if not parsed["series"] and parsed["collections"]:
        first_key = next(iter(parsed["collections"]))
        parsed["series"] = parsed["collections"][first_key]
        if first_key in parsed["group_positions"]:
            parsed["series_index"] = parsed["group_positions"][first_key]
        elif "default" in parsed["group_positions"]:
            parsed["series_index"] = parsed["group_positions"]["default"]

    if not parsed["series"]:
        s_name, s_idx, _ = extract_series_from_title(title)
        if not s_name:
            s_name, s_idx, _ = extract_series_from_title(stem)
        if s_name:
            parsed["series"] = s_name
            if parsed["series_index"] is None:
                parsed["series_index"] = s_idx


def _locate_cover_href(opf_root, cover_id: str | None) -> str | None:
    """Finds the cover image href from the OPF manifest."""
    manifest_elem = opf_root.find(".//{http://www.idpf.org/2007/opf}manifest")
    if manifest_elem is None:
        manifest_elem = opf_root.find(".//manifest")

    cover_href: str | None = None
    if manifest_elem is not None:
        for item in manifest_elem:
            item_id = item.attrib.get("id", "")
            href = item.attrib.get("href", "")
            props = item.attrib.get("properties", "")
            media_type = item.attrib.get("media-type", "")

            if "cover-image" in props:
                cover_href = href
                break
            elif cover_id and item_id == cover_id:
                cover_href = href
                break
            elif "cover" in item_id.lower() and media_type.startswith("image/"):
                if not cover_href:
                    cover_href = href
    return cover_href


def _read_cover_bytes(
    zf: zipfile.ZipFile, opf_dir: str, cover_href: str | None, file_path: Path
) -> bytes | None:
    """Reads the cover image bytes from the archive, if present."""
    if not cover_href:
        return None
    full_cover_path = posixpath.join(opf_dir, cover_href) if opf_dir else cover_href
    full_cover_path = posixpath.normpath(full_cover_path)
    try:
        return zf.read(full_cover_path)
    except Exception:
        logger.debug(
            "Failed to read EPUB cover %s from %s: %s",
            full_cover_path,
            file_path,
            exc_info=True,
        )
        return None


def parse_epub(file_path: Path) -> ParsedBookMetadata | None:
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            opf_path = _locate_opf_path(zf)
            if not opf_path or opf_path not in zf.namelist():
                s_name, s_idx, clean_t = extract_series_from_title(file_path.stem)
                return ParsedBookMetadata(
                    title=clean_t,
                    series=s_name,
                    series_index=s_idx,
                    file_format="epub",
                )

            opf_dir = posixpath.dirname(opf_path)
            opf_data = zf.read(opf_path)
            opf_root = ET.fromstring(opf_data)

            metadata_elem = opf_root.find(".//{http://www.idpf.org/2007/opf}metadata")
            if metadata_elem is None:
                metadata_elem = opf_root.find(".//metadata")

            parsed = _parse_opf_metadata(metadata_elem, file_path.stem)
            _resolve_series(parsed, parsed["title"], file_path.stem)

            cover_href = _locate_cover_href(opf_root, parsed["cover_id"])
            cover_bytes = _read_cover_bytes(zf, opf_dir, cover_href, file_path)

            return ParsedBookMetadata(
                title=parsed["title"],
                authors=parsed["authors"] if parsed["authors"] else ["Unknown Author"],
                description=parsed["description"],
                publisher=parsed["publisher"],
                language=parsed["language"],
                isbn=parsed["isbn"],
                publication_date=parsed["publication_date"],
                series=parsed["series"],
                series_index=parsed["series_index"],
                tags=parsed["tags"],
                cover_bytes=cover_bytes,
                file_format="epub",
            )
    except Exception:
        s_name, s_idx, clean_t = extract_series_from_title(file_path.stem)
        return ParsedBookMetadata(
            title=clean_t,
            authors=["Unknown Author"],
            series=s_name,
            series_index=s_idx,
            file_format="epub",
        )
