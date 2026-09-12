"""Jellyfin REST API controller re-export for backward compatibility."""

from __future__ import annotations

from aarkib.plugins.jellyfin import (
    JELLYFIN_SERVER_VERSION,
    SERVER_NAME,
    JellyfinProtocolPlugin,
    extract_jellyfin_token,
    from_jellyfin_id,
    generate_jellyfin_token,
    get_authenticated_user,
    get_server_id,
    jellyfin_auth,
    jellyfin_bp,
    to_jellyfin_id,
    verify_jellyfin_token,
)

__all__ = [
    "jellyfin_bp",
    "JELLYFIN_SERVER_VERSION",
    "SERVER_NAME",
    "get_server_id",
    "to_jellyfin_id",
    "from_jellyfin_id",
    "generate_jellyfin_token",
    "verify_jellyfin_token",
    "extract_jellyfin_token",
    "get_authenticated_user",
    "jellyfin_auth",
    "JellyfinProtocolPlugin",
]
