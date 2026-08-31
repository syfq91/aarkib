from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path

import defusedxml.ElementTree as ET

from buukuu.services.parsers.base import ParsedBookMetadata

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


def parse_epub(file_path: Path) -> ParsedBookMetadata | None:
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            # 1. Locate rootfile from META-INF/container.xml
            try:
                container_data = zf.read("META-INF/container.xml")
                root = ET.fromstring(container_data)
                rootfile_elem = root.find(
                    ".//container:rootfile", namespaces=NAMESPACES
                )
                if rootfile_elem is not None:
                    opf_path = rootfile_elem.attrib.get("full-path", "")
                else:
                    opf_path = ""
            except Exception:
                opf_path = ""

            if not opf_path:
                # Fallback: search for first .opf file in archive
                for name in zf.namelist():
                    if name.lower().endswith(".opf"):
                        opf_path = name
                        break

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

            title = file_path.stem
            authors: list[str] = []
            description: str | None = None
            publisher: str | None = None
            language: str | None = "en"
            isbn: str | None = None
            publication_date: str | None = None
            series: str | None = None
            series_index: float | None = None
            tags: list[str] = []
            cover_id: str | None = None

            collections: dict[str, str] = {}
            group_positions: dict[str, float] = {}

            if metadata_elem is not None:
                for elem in metadata_elem:
                    tag = elem.tag
                    text = (elem.text or "").strip()

                    if tag.endswith("title") and text:
                        title = text
                    elif tag.endswith("creator") and text:
                        authors.append(text)
                    elif tag.endswith("description") and text:
                        description = text
                    elif tag.endswith("publisher") and text:
                        publisher = text
                    elif tag.endswith("language") and text:
                        language = text
                    elif tag.endswith("identifier") and text:
                        scheme = elem.attrib.get("scheme", "").upper()
                        if "ISBN" in scheme or "isbn" in text.lower():
                            isbn = text
                        elif not isbn:
                            isbn = text
                    elif tag.endswith("date") and text:
                        publication_date = text[:10]
                    elif tag.endswith("subject") and text:
                        tags.append(text)
                    elif tag.endswith("meta"):
                        name_attr = elem.attrib.get("name", "")
                        prop_attr = elem.attrib.get("property", "")
                        content_attr = elem.attrib.get("content", "")
                        meta_id = elem.attrib.get("id", "")
                        refines = elem.attrib.get("refines", "")

                        if name_attr == "cover":
                            cover_id = content_attr
                        elif name_attr in ("calibre:series", "series"):
                            series = content_attr or text
                        elif name_attr in ("calibre:series_index", "series_index"):
                            try:
                                series_index = float(content_attr or text)
                            except ValueError:
                                pass
                        # EPUB 3 collection properties
                        elif prop_attr == "belongs-to-collection" and text:
                            collections[meta_id or "default"] = text
                        elif prop_attr == "group-position" and text:
                            target_id = refines.lstrip("#") or "default"
                            try:
                                group_positions[target_id] = float(text)
                            except ValueError:
                                pass
                        elif prop_attr in ("schema:series", "series") and text:
                            series = text

            # Resolve EPUB 3 collection to series if not found via Calibre tags
            if not series and collections:
                first_key = next(iter(collections))
                series = collections[first_key]
                if first_key in group_positions:
                    series_index = group_positions[first_key]
                elif "default" in group_positions:
                    series_index = group_positions["default"]

            # If series still not found, check title and filename heuristics
            if not series:
                s_name, s_idx, _ = extract_series_from_title(title)
                if not s_name:
                    s_name, s_idx, _ = extract_series_from_title(file_path.stem)
                if s_name:
                    series = s_name
                    if series_index is None:
                        series_index = s_idx

            # Extract manifest items to locate cover
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

            cover_bytes: bytes | None = None
            if cover_href:
                full_cover_path = (
                    posixpath.join(opf_dir, cover_href) if opf_dir else cover_href
                )
                full_cover_path = posixpath.normpath(full_cover_path)
                try:
                    cover_bytes = zf.read(full_cover_path)
                except Exception:
                    pass

            return ParsedBookMetadata(
                title=title,
                authors=authors if authors else ["Unknown Author"],
                description=description,
                publisher=publisher,
                language=language,
                isbn=isbn,
                publication_date=publication_date,
                series=series,
                series_index=series_index,
                tags=tags,
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
