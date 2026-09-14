from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from flask_login import UserMixin
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.playlist import Playlist, UserFavorite
    from aarkib.models.progress import Bookmark, UserProgress


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    progress_records: Mapped[list[UserProgress]] = relationship(
        "UserProgress", back_populates="user", cascade="all, delete-orphan"
    )
    bookmarks: Mapped[list[Bookmark]] = relationship(
        "Bookmark", back_populates="user", cascade="all, delete-orphan"
    )
    favorites: Mapped[list[UserFavorite]] = relationship(
        "UserFavorite", back_populates="user", cascade="all, delete-orphan"
    )
    playlists: Mapped[list[Playlist]] = relationship(
        "Playlist", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def has_password(self) -> bool:
        """Returns True if this user account has a password set."""
        return bool(self.password_hash)

    def set_password(self, password: str | None) -> None:
        """Sets a password hash or clears the password if None/empty."""
        if not password or not password.strip():
            self.password_hash = None
        else:
            self.password_hash = generate_password_hash(password.strip())

    def check_password(
        self,
        password: str | None,
        client_ip: str | None = None,
        allow_remote_passwordless: bool = False,
    ) -> bool:
        """Verifies password. If the user has no password set, entering nothing matches.

        Passwordless access is strictly restricted to private/local network clients (RFC 1918)
        unless allow_remote_passwordless is explicitly enabled.
        """
        if not self.has_password:
            from aarkib.services.security import is_private_or_local_ip

            if (
                not allow_remote_passwordless
                and client_ip
                and not is_private_or_local_ip(client_ip)
            ):
                return False
            return not password or not password.strip()
        if not password:
            return False
        return check_password_hash(self.password_hash, password)

    def __repr__(self) -> str:
        return f"<User {self.username}>"
