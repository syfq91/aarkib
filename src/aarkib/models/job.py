from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.library import Library


class JobRecord(db.Model):
    """Database model for persisted background job history."""

    __tablename__ = "job_history"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    library_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("libraries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", index=True
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    progress_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    library: Mapped[Library | None] = relationship("Library")

    def to_dict(self) -> dict[str, Any]:
        """Serializes the persisted job record to a JSON-compatible dictionary."""
        elapsed = None
        if self.started_at:
            end_time = self.finished_at or datetime.now(UTC)
            start = self.started_at
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=UTC)
            elapsed = round((end_time - start).total_seconds(), 2)

        result = None
        if self.result_payload:
            try:
                result = json.loads(self.result_payload)
            except json.JSONDecodeError, TypeError:
                result = {"raw": self.result_payload}

        created_iso = self.created_at.isoformat() if self.created_at else None
        started_iso = self.started_at.isoformat() if self.started_at else None
        finished_iso = self.finished_at.isoformat() if self.finished_at else None

        return {
            "id": self.id,
            "job_type": self.job_type,
            "status": self.status,
            "progress": round(self.progress, 1),
            "progress_message": self.progress_message or "",
            "created_at": created_iso,
            "started_at": started_iso,
            "finished_at": finished_iso,
            "completed_at": finished_iso,
            "elapsed_seconds": elapsed,
            "result": result,
            "error": self.error_message,
        }
