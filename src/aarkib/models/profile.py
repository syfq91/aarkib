from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aarkib.extensions import db

if TYPE_CHECKING:
    from aarkib.models.library import Library
    from aarkib.models.progress import Bookmark, UserProgress
    from aarkib.models.user import User


class Profile(db.Model):
    """Sub-account profile belonging to a User with isolated progress and library ACLs."""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_child: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="profiles")
    library_access: Mapped[list[ProfileLibraryAccess]] = relationship(
        "ProfileLibraryAccess", back_populates="profile", cascade="all, delete-orphan"
    )
    progress_records: Mapped[list[UserProgress]] = relationship(
        "UserProgress", back_populates="profile", cascade="all, delete-orphan"
    )
    bookmarks: Mapped[list[Bookmark]] = relationship(
        "Bookmark", back_populates="profile", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict[str, Any]:
        """Serializes profile attributes and library permissions to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "is_child": self.is_child,
            "avatar_url": self.avatar_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "library_access": [la.to_dict() for la in self.library_access]
            if self.library_access
            else [],
        }

    def __repr__(self) -> str:
        return f"<Profile id={self.id} user_id={self.user_id} name={self.name!r} child={self.is_child}>"


class ProfileLibraryAccess(db.Model):
    """Explicit per-profile library access control list (ACL)."""

    __tablename__ = "profile_library_access"
    __table_args__ = (
        UniqueConstraint("profile_id", "library_id", name="uq_profile_library_access"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    library_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    can_read: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_download: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    profile: Mapped[Profile] = relationship("Profile", back_populates="library_access")
    library: Mapped[Library] = relationship("Library")

    def to_dict(self) -> dict[str, Any]:
        """Serializes library ACL entry to a dictionary."""
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "library_id": self.library_id,
            "can_read": self.can_read,
            "can_download": self.can_download,
        }

    def __repr__(self) -> str:
        return (
            f"<ProfileLibraryAccess profile={self.profile_id} lib={self.library_id} "
            f"read={self.can_read} download={self.can_download}>"
        )
