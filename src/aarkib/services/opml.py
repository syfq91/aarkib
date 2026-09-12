"""OPML (Outline Processor Markup Language) parser and podcast show importer."""

from __future__ import annotations

import logging
from typing import Any

import defusedxml.ElementTree as ET
from sqlalchemy import or_, select

from aarkib.extensions import db
from aarkib.models import Collection, MediaItem

logger = logging.getLogger(__name__)


def parse_opml(xml_content: str | bytes) -> list[dict[str, Any]]:
    """Safely parses an OPML document and extracts podcast subscription outlines.

    Guards against XML entity expansion (Billion Laughs) and XXE vulnerabilities
    using defusedxml.
    """
    if isinstance(xml_content, str):
        xml_bytes = xml_content.strip().encode("utf-8")
    else:
        xml_bytes = xml_content.strip()

    if not xml_bytes:
        return []

    try:
        root = ET.fromstring(xml_bytes)
    except Exception as exc:
        logger.warning("Failed to parse OPML XML content: %s", exc)
        raise ValueError(f"Malformed or invalid OPML XML: {exc}") from exc

    body = root.find("body")
    if body is None:
        return []

    feeds: list[dict[str, Any]] = []

    def _process_outline(node, parent_category: str | None = None) -> None:
        attrs = node.attrib
        xml_url = attrs.get("xmlUrl") or attrs.get("xmlurl") or attrs.get("url")
        html_url = attrs.get("htmlUrl") or attrs.get("htmlurl")
        title = attrs.get("text") or attrs.get("title") or ""
        description = attrs.get("description")

        # If it has an xmlUrl attribute, it's a feed subscription outline
        if xml_url:
            feeds.append(
                {
                    "title": title.strip(),
                    "xml_url": xml_url.strip(),
                    "html_url": html_url.strip() if html_url else None,
                    "description": description.strip() if description else None,
                    "category": parent_category,
                }
            )
        else:
            # Folder or category node containing child outlines
            folder_title = title.strip() or parent_category
            for child in node.findall("outline"):
                _process_outline(child, parent_category=folder_title)

    for top_outline in body.findall("outline"):
        _process_outline(top_outline)

    return feeds


def import_opml_channels(
    opml_feeds: list[dict[str, Any]],
    session: Any = None,
) -> dict[str, Any]:
    """Imports parsed OPML podcast feeds into Collections and associates local episodes."""
    sess = session or db.session

    total = len(opml_feeds)
    created_count = 0
    existing_count = 0
    matched_episodes = 0
    summaries: list[dict[str, Any]] = []

    for feed in opml_feeds:
        show_title = feed.get("title")
        if not show_title:
            continue

        xml_url = feed.get("xml_url")
        desc = feed.get("description")

        existing_col = sess.scalar(
            select(Collection).where(Collection.name == show_title)
        )

        if not existing_col:
            existing_col = Collection(name=show_title, description=desc)
            sess.add(existing_col)
            sess.flush()
            created_count += 1
            status = "created"
        else:
            if not existing_col.description and desc:
                existing_col.description = desc
            existing_count += 1
            status = "existing"

        # Match unassigned or loosely matching podcast media items
        # Matches by show name in original_file_path or title or series
        episodes = sess.scalars(
            select(MediaItem).where(
                or_(
                    MediaItem.collection_id == existing_col.id,
                    MediaItem.collection_id.is_(None),
                ),
                or_(
                    MediaItem.media_type == "podcast",
                    MediaItem.original_file_path.ilike(f"%{show_title}%"),
                ),
            )
        ).all()

        show_matched = 0
        for ep in episodes:
            if ep.collection_id is None:
                # Check path or title match
                if show_title.lower() in ep.original_file_path.lower() or (
                    ep.album and ep.album.lower() == show_title.lower()
                ):
                    ep.collection_id = existing_col.id
                    ep.media_type = "podcast"
                    if xml_url and not ep.podcast_feed_url:
                        ep.podcast_feed_url = xml_url
                    show_matched += 1
                    matched_episodes += 1

        summaries.append(
            {
                "id": existing_col.id,
                "title": show_title,
                "xml_url": xml_url,
                "status": status,
                "matched_episodes": show_matched,
            }
        )

    sess.commit()

    return {
        "total_feeds": total,
        "created_shows": created_count,
        "existing_shows": existing_count,
        "matched_episodes": matched_episodes,
        "shows": summaries,
    }
