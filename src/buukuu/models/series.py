from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from buukuu.extensions import db

if TYPE_CHECKING:
    from buukuu.models.book import Book


class Series(db.Model):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    books: Mapped[list[Book]] = relationship(
        "Book", back_populates="series", order_by="Book.series_index"
    )

    def __repr__(self) -> str:
        return f"<Series {self.name}>"
