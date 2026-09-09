from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.media_item import MediaItem

book_tags = Table(
    "book_tags",
    db.Model.metadata,
    Column(
        "book_id", Integer, ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    ),
    Column(
        "tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    ),
)


class Tag(db.Model):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )

    # Relationships
    books: Mapped[list[MediaItem]] = relationship(
        "MediaItem", secondary=book_tags, back_populates="tags"
    )

    # Generalized domain synonyms
    media_items = synonym("books")
    items = synonym("books")

    def __repr__(self) -> str:
        return f"<Tag {self.name}>"
