from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.media_item import MediaItem


class Series(db.Model):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    books: Mapped[list[MediaItem]] = relationship(
        "MediaItem", back_populates="series", order_by="MediaItem.series_index"
    )

    # Generalized domain synonyms
    media_items = synonym("books")
    items = synonym("books")

    def __repr__(self) -> str:
        return f"<Series {self.name}>"
