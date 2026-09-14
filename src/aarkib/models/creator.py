from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.media_item import MediaItem

media_creators = Table(
    "media_creators",
    db.Model.metadata,
    Column(
        "media_item_id",
        Integer,
        ForeignKey("media_items.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "creator_id",
        Integer,
        ForeignKey("creators.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Creator(db.Model):
    """Catalog model representing authors, artists, musicians, directors, or performers."""

    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    sort_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )

    # Relationships
    media_items: Mapped[list[MediaItem]] = relationship(
        "MediaItem", secondary=media_creators, back_populates="creators"
    )

    def __repr__(self) -> str:
        return f"<Creator {self.name}>"
