from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.media_item import MediaItem


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
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    media_items: Mapped[list[MediaItem]] = relationship(
        "MediaItem", back_populates="library"
    )
    books = synonym("media_items")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    @property
    def settings(self) -> dict[str, Any]:
        """Returns parsed JSON dictionary of per-library settings."""
        if not self.settings_json:
            return {}
        try:
            import json

            val = json.loads(self.settings_json)
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        import json

        curr = self.settings
        curr[key] = value
        self.settings_json = json.dumps(curr)

    @property
    def auto_enrich(self) -> bool | None:
        """Library-level override for automatic metadata enrichment."""
        return self.get_setting("auto_enrich", None)

    @property
    def metadata_provider(self) -> str | None:
        """Library-level preferred metadata provider."""
        return self.get_setting("metadata_provider", None)

    @property
    def language(self) -> str | None:
        """Library-level default language."""
        return self.get_setting("language", None)

    def to_dict(self, count: int = 0) -> dict[str, Any]:
        return {
            "id": self.slug,
            "db_id": self.id,
            "name": self.name,
            "path": self.path,
            "media_type": self.media_type,
            "settings": self.settings,
            "count": count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<Library {self.id}: {self.name} ({self.media_type}) -> {self.path}>"
