"""Centralized authorization service for role-based and profile library ACL access control."""

from __future__ import annotations

import logging
from typing import Any

from flask import g, has_request_context, request, session
from flask_login import current_user
from sqlalchemy import select

from aarkib.extensions import db
from aarkib.models import Library, MediaItem, Profile, ProfileLibraryAccess, User

logger = logging.getLogger(__name__)

RESTRICTED_CHILD_TAGS = {"nsfw", "explicit", "adult", "18+", "mature", "r-rated"}


class AuthorizationService:
    """Unified policy authorization and access control provider."""

    @staticmethod
    def can(
        subject: User | Profile | None,
        action: str,
        resource: Any = None,
    ) -> bool:
        """Determines whether subject is permitted to perform action on resource.

        Supported actions:
        - 'admin': system configuration and privileged administration.
        - 'metadata.edit': editing catalog metadata, locking, matching.
        - 'library.read': reading / listing media within a library.
        - 'library.download': downloading media files from a library.
        - 'media.stream': streaming audio/video or viewing books/comics.
        - 'media.transcode': generating or requesting transcoded streams.
        """
        if subject is None:
            return False

        # --- ADMIN / PRIVILEGED ACTIONS ---
        if action in ("admin", "metadata.edit"):
            if isinstance(subject, User):
                return bool(subject.is_admin)
            if isinstance(subject, Profile):
                return bool(
                    subject.user and subject.user.is_admin and not subject.is_child
                )
            return False

        # Normalize Profile from User if subject is a User
        if isinstance(subject, User):
            if subject.is_admin:
                return True
            # For standard user, evaluate using their default profile if one exists
            default_prof = subject.default_profile
            if default_prof:
                return AuthorizationService.can(default_prof, action, resource)
            # If no profile exists yet, allow standard access by default
            return True

        # Subject is a Profile:
        # --- LIBRARY READ ACTION ---
        if action == "library.read":
            lib_id = AuthorizationService._extract_library_id(resource)
            if lib_id is None:
                return True

            acl = db.session.scalar(
                select(ProfileLibraryAccess).where(
                    ProfileLibraryAccess.profile_id == subject.id,
                    ProfileLibraryAccess.library_id == lib_id,
                )
            )
            if acl is not None:
                return bool(acl.can_read)
            # Open by default if no explicit ACL rule is configured
            return True

        # --- LIBRARY DOWNLOAD ACTION ---
        if action == "library.download":
            # Child profiles are denied download by default unless explicitly allowed
            lib_id = AuthorizationService._extract_library_id(resource)
            if lib_id is None:
                return not subject.is_child

            acl = db.session.scalar(
                select(ProfileLibraryAccess).where(
                    ProfileLibraryAccess.profile_id == subject.id,
                    ProfileLibraryAccess.library_id == lib_id,
                )
            )
            if acl is not None:
                return bool(acl.can_download)
            return not subject.is_child

        # --- MEDIA STREAM / READ ACTION ---
        if action in ("media.stream", "media.transcode"):
            if isinstance(resource, MediaItem):
                # Verify library read access
                if resource.library_id and not AuthorizationService.can(
                    subject, "library.read", resource.library_id
                ):
                    return False

                # Child safety content filtering
                if subject.is_child and resource.tags:
                    for tag in resource.tags:
                        if tag.name and tag.name.lower() in RESTRICTED_CHILD_TAGS:
                            logger.info(
                                "Profile '%s' blocked from mature item %s (tag: %s)",
                                subject.name,
                                resource.id,
                                tag.name,
                            )
                            return False
            return True

        logger.warning("Unrecognized authorization action: '%s'", action)
        return False

    @staticmethod
    def _extract_library_id(resource: Any) -> int | None:
        """Extracts integer library_id from a Library, MediaItem, or int."""
        if resource is None:
            return None
        if isinstance(resource, int):
            return resource
        if isinstance(resource, Library):
            return getattr(resource, "id", None)
        if isinstance(resource, MediaItem):
            return getattr(resource, "library_id", None)
        if isinstance(resource, str) and resource.isdigit():
            return int(resource)
        return None

    def get_active_profile(self, user: User | None = None) -> Profile | None:
        """Resolves the active Profile for the current execution context."""
        effective_user = user
        if effective_user is None and has_request_context():
            if current_user and current_user.is_authenticated:
                effective_user = current_user

        if has_request_context():
            # 1. Cached in flask.g
            cached = getattr(g, "active_profile", None)
            if cached is not None:
                if effective_user is None or cached.user_id == effective_user.id:
                    return cached

            # 2. Header: X-Aarkib-Profile-Id or X-Profile-ID
            prof_id_raw = request.headers.get(
                "X-Aarkib-Profile-Id"
            ) or request.headers.get("X-Profile-ID")

            # 3. Query param: profile_id
            if not prof_id_raw:
                prof_id_raw = request.args.get("profile_id")

            # 4. Session cookie
            if not prof_id_raw and session:
                prof_id_raw = session.get("profile_id")

            if prof_id_raw and str(prof_id_raw).isdigit():
                prof = db.session.get(Profile, int(prof_id_raw))
                if prof:
                    if effective_user is None or prof.user_id == effective_user.id:
                        g.active_profile = prof
                        return prof

        if effective_user is not None:
            default_prof = effective_user.default_profile
            if default_prof and has_request_context():
                g.active_profile = default_prof
            return default_prof

        return None


authorization = AuthorizationService()
