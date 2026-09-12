"""Subsonic REST API 1.16.1 compatible controller re-export for backward compatibility."""

from __future__ import annotations

from aarkib.plugins.subsonic import (
    SERVER_VERSION,
    SUBSONIC_VERSION,
    SubsonicProtocolPlugin,
    get_authenticated_user,
    subsonic_auth,
    subsonic_bp,
    subsonic_response,
)

__all__ = [
    "subsonic_bp",
    "SUBSONIC_VERSION",
    "SERVER_VERSION",
    "subsonic_response",
    "subsonic_auth",
    "get_authenticated_user",
    "SubsonicProtocolPlugin",
]
