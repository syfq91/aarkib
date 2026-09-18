from aarkib.services.authorization import (
    AuthorizationService,
    authorization,
)
from aarkib.services.capability_service import (
    CapabilityService,
    capability_service,
    detect_client_capabilities,
)
from aarkib.services.parsers.base import (
    BaseParsedMetadata,
    ParsedBookMetadata,
    ParsedVideoMetadata,
    extract_metadata_from_file,
)
from aarkib.services.playback_service import (
    PlaybackService,
    plan_playback,
    playback_service,
)
from aarkib.services.scanner import (
    compute_fast_fingerprint,
    compute_sha256,
    index_media_file,
    scan_library,
    start_library_watcher,
    validate_library_availability,
)
from aarkib.services.thumbnail import generate_cover_webp
from aarkib.services.transcoder import (
    TranscodeCapabilities,
    TranscodeProfile,
    detect_transcode_capabilities,
    resolve_transcode_profile,
    transcode_supervisor,
)

__all__ = [
    "BaseParsedMetadata",
    "ParsedBookMetadata",
    "ParsedVideoMetadata",
    "extract_metadata_from_file",
    "compute_fast_fingerprint",
    "compute_sha256",
    "index_media_file",
    "scan_library",
    "start_library_watcher",
    "validate_library_availability",
    "generate_cover_webp",
    "AuthorizationService",
    "authorization",
    "CapabilityService",
    "capability_service",
    "detect_client_capabilities",
    "PlaybackService",
    "playback_service",
    "plan_playback",
    "TranscodeCapabilities",
    "TranscodeProfile",
    "detect_transcode_capabilities",
    "resolve_transcode_profile",
    "transcode_supervisor",
]
