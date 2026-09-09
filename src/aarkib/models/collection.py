from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.media_item import MediaItem


class Collection(db.Model):
    """Catalog model representing series, franchises, comic runs, or music albums."""

    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    media_items: Mapped[list[MediaItem]] = relationship(
        "MediaItem", back_populates="collection", order_by="MediaItem.series_index"
    )

    # Synonyms for multi-media & legacy references
    items = synonym("media_items")
    books = synonym("media_items")

    def __repr__(self) -> str:
        return f"<Collection {self.name}>"


Series = Collection
