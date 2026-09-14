"""DeviceToken model for scoped, revocable API and client access."""

from __future__ import annotations

import datetime
import hashlib
import json
import secrets
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.user import User


class DeviceToken(db.Model):
    """Represents a scoped, revocable device/API token for client authentication."""

    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    token_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    scopes_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC), nullable=False
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="tokens")

    STANDARD_SCOPES = [
        "*",
        "media:read",
        "media:stream",
        "progress:write",
        "admin",
    ]

    @property
    def scopes(self) -> list[str]:
        """Returns decoded scopes as a list of strings."""
        try:
            val = json.loads(self.scopes_json or "[]")
            return val if isinstance(val, list) else []
        except json.JSONDecodeError, TypeError:
            return []

    @scopes.setter
    def scopes(self, value: list[str]) -> None:
        """Serializes list of scopes to JSON string."""
        self.scopes_json = json.dumps(value or [])

    def has_scope(self, required_scope: str) -> bool:
        """Returns True if this token grants the required scope (or '*' wildcard)."""
        token_scopes = self.scopes
        if not token_scopes or "*" in token_scopes:
            return True
        return required_scope in token_scopes

    @classmethod
    def hash_token(cls, raw_token: str) -> str:
        """Computes SHA-256 hash of a raw token string."""
        return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()

    @classmethod
    def create_token(
        cls,
        user_id: int,
        name: str,
        scopes: list[str] | None = None,
        expires_in_days: int | None = None,
    ) -> tuple[DeviceToken, str]:
        """Creates a new DeviceToken and returns (token_instance, raw_secret_token)."""
        raw_secret = f"ark_{secrets.token_hex(24)}"
        token_hash = cls.hash_token(raw_secret)
        token_prefix = raw_secret[:10] + "..."

        expires_at = None
        if expires_in_days and expires_in_days > 0:
            expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
                days=expires_in_days
            )

        token_obj = cls(
            user_id=user_id,
            name=name.strip(),
            token_hash=token_hash,
            token_prefix=token_prefix,
            scopes_json=json.dumps(scopes or []),
            expires_at=expires_at,
        )
        return token_obj, raw_secret

    def to_dict(self) -> dict[str, Any]:
        """Serializes token metadata (excluding sensitive hash) for display."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "token_prefix": self.token_prefix,
            "scopes": self.scopes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat()
            if self.last_used_at
            else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_expired": bool(
                self.expires_at
                and self.expires_at.replace(tzinfo=datetime.UTC)
                < datetime.datetime.now(datetime.UTC)
            )
            if self.expires_at
            else False,
        }

    def __repr__(self) -> str:
        return f"<DeviceToken id={self.id} name='{self.name}' user_id={self.user_id}>"


class DevicePairingCode(db.Model):
    """Temporary pairing code for TV/10-foot interfaces and quick remote login."""

    __tablename__ = "device_pairing_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    user_code: Mapped[str] = mapped_column(
        String(16), unique=True, nullable=False, index=True
    )
    device_name: Mapped[str] = mapped_column(String(128), default="TV Device")
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC),
        nullable=False,
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    raw_token: Mapped[str | None] = mapped_column(String(128), nullable=True)

    user: Mapped[User | None] = relationship("User")

    def is_expired(self) -> bool:
        """Check if pairing code has expired."""
        exp = (
            self.expires_at.replace(tzinfo=datetime.UTC)
            if self.expires_at.tzinfo is None
            else self.expires_at
        )
        return exp < datetime.datetime.now(datetime.UTC)

    def __repr__(self) -> str:
        return (
            f"<DevicePairingCode id={self.id} user_code='{self.user_code}' "
            f"authorized={self.is_authorized}>"
        )
