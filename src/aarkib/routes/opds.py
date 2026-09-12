"""OPDS 1.2 & 2.0 protocol controller re-export for backward compatibility."""

from __future__ import annotations

from aarkib.plugins.opds import (
    OPDS_ACQ_TYPE,
    OPDS_AUTH_TYPE,
    OPDS_JSON_TYPE,
    OPDS_NAV_TYPE,
    OPDS_PROGRESSION_TYPE,
    OPDS_READABLE_TYPES,
    OPENSEARCH_TYPE,
    PRESET_RULE,
    PROBLEM_JSON_TYPE,
    OPDSProtocolPlugin,
    format_progression_document,
    get_opds_user,
    make_opds_auth_document,
    opds_auth_required,
    opds_bp,
    opds_readable_filter,
)

__all__ = [
    "opds_bp",
    "OPDS_NAV_TYPE",
    "OPDS_ACQ_TYPE",
    "OPENSEARCH_TYPE",
    "OPDS_PROGRESSION_TYPE",
    "OPDS_AUTH_TYPE",
    "OPDS_JSON_TYPE",
    "PROBLEM_JSON_TYPE",
    "PRESET_RULE",
    "OPDS_READABLE_TYPES",
    "opds_readable_filter",
    "get_opds_user",
    "opds_auth_required",
    "make_opds_auth_document",
    "format_progression_document",
    "OPDSProtocolPlugin",
]
