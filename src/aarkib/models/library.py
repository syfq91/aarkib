from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aarkib.extensions import db


class Library(db.Model):
    """Database model for configured media library folders."""

    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(
        String(1000), unique=True, nullable=False, index=True
    )
    # media_type can be: "all" (auto-detect), "book", "comic", "video", "audio"
    media_type: Mapped[str] = mapped_column(
        String(50), default="all", nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def to_dict(self, count: int = 0) -> dict[str, Any]:
        return {
            "id": self.slug,
            "db_id": self.id,
            "name": self.name,
            "path": self.path,
            "media_type": self.media_type,
            "count": count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<Library {self.id}: {self.name} ({self.media_type}) -> {self.path}>"
