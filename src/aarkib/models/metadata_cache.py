from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from aarkib.extensions import db


class MetadataCacheEntry(db.Model):
    """Stores cached external metadata API responses with TTL expiration."""

    __tablename__ = "metadata_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    params: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    @property
    def is_expired(self) -> bool:
        """Returns True if this cache entry has expired."""
        now = datetime.now(UTC)
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return now >= exp

    def get_data(self) -> Any:
        """Parses and returns stored JSON response payload."""
        try:
            return json.loads(self.response_json)
        except json.JSONDecodeError, TypeError:
            return None

    def __repr__(self) -> str:
        return f"<MetadataCacheEntry {self.provider}:{self.endpoint} exp={self.expires_at}>"
