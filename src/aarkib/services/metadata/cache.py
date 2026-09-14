from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from flask import current_app, has_app_context
from sqlalchemy import delete

from aarkib.extensions import db
from aarkib.models.metadata_cache import MetadataCacheEntry

logger = logging.getLogger(__name__)


class MetadataCacheManager:
    """Manages persistent SQLite response caching for external metadata queries."""

    @staticmethod
    def compute_cache_key(
        provider: str, endpoint: str, params: dict[str, Any] | None = None
    ) -> str:
        """Generates a deterministic SHA-256 cache key from query dimensions."""
        normalized_params = json.dumps(
            params or {}, sort_keys=True, separators=(",", ":")
        )
        raw = f"{provider.lower().strip()}:{endpoint.strip()}:{normalized_params}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def get(
        cls,
        provider: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Any | None:
        """Retrieves cached payload if present and not expired."""
        if not has_app_context():
            return None

        cache_key = cls.compute_cache_key(provider, endpoint, params)
        try:
            entry = db.session.get(MetadataCacheEntry, cache_key)
            if entry and not entry.is_expired:
                return entry.get_data()
        except Exception as exc:
            logger.debug("Metadata cache get error: %s", exc)
        return None

    @classmethod
    def set(
        cls,
        provider: str,
        endpoint: str,
        params: dict[str, Any] | None,
        data: Any,
        ttl_days: int | None = None,
    ) -> None:
        """Stores or updates cached payload with TTL."""
        if not has_app_context() or data is None:
            return

        if ttl_days is None:
            ttl_days = int(current_app.config.get("METADATA_CACHE_TTL_DAYS", 30))

        cache_key = cls.compute_cache_key(provider, endpoint, params)
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=ttl_days)
        params_str = json.dumps(params or {}, sort_keys=True) if params else None

        try:
            payload_str = json.dumps(data)
            entry = db.session.get(MetadataCacheEntry, cache_key)
            if entry:
                entry.response_json = payload_str
                entry.created_at = now
                entry.expires_at = expires_at
                entry.params = params_str
            else:
                entry = MetadataCacheEntry(
                    cache_key=cache_key,
                    provider=provider.lower().strip(),
                    endpoint=endpoint.strip(),
                    params=params_str,
                    response_json=payload_str,
                    created_at=now,
                    expires_at=expires_at,
                )
                db.session.add(entry)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.debug("Metadata cache set error: %s", exc)

    @classmethod
    def prune_expired(cls) -> int:
        """Prunes all expired cache entries from SQLite."""
        if not has_app_context():
            return 0
        try:
            now = datetime.now(UTC)
            stmt = delete(MetadataCacheEntry).where(
                MetadataCacheEntry.expires_at <= now
            )
            res = db.session.execute(stmt)
            db.session.commit()
            return res.rowcount or 0
        except Exception as exc:
            db.session.rollback()
            logger.debug("Metadata cache prune error: %s", exc)
            return 0
