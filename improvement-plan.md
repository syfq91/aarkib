AI Agent Plan: Evolve Aarkib with improvement Goal

Improve Aarkib's media architecture

The agent should implement these four capabilities:

    Client capability detection

    Playback planning

    Reliable background jobs/reconciliation

    Profile-based access control

Metadata matching can follow as a second phase.
Phase 0 — Establish guardrails

Before modifying code, the agent should inspect:

    AGENTS.md

    ARCHITECTURE.md

    README.md

    pyproject.toml

    src/aarkib/models/

    src/aarkib/services/

    src/aarkib/plugins/

    src/aarkib/routes/

    existing tests

The agent should create a short internal map:

models
  ↓
services
  ↓
plugins
  ↓
routes/API
  ↓
UI / external clients

Constraints

The agent must preserve:

    SQLite

    single-node deployment

    existing plugin architecture

    original media files

    existing API compatibility

    OPDS compatibility

    Subsonic compatibility

    Jellyfin compatibility

    current reader functionality

    existing migrations/data

Do not introduce PostgreSQL, Redis, Celery, RPC, or a frontend rewrite.
Phase 1 — Client capabilities

Create a generic capability model.

Something conceptually like:

ClientCapabilities
├── client_type
├── video
│   ├── codecs
│   ├── containers
│   ├── max_resolution
│   └── hdr
├── audio
│   ├── codecs
│   └── containers
├── subtitles
├── streaming
└── device

Don't hardcode browser/user-agent checks throughout the application.

Instead:

capabilities = capability_service.detect(request)

Then consumers use:

capabilities.supports_video("h264")

rather than:

if "Chrome" in user_agent:
    ...

Tests

Add tests for:

    browser

    Jellyfin client

    unknown client

    explicit capability overrides

    malformed capability data

Phase 2 — Playback planner

Create:

services/
    playback_service.py

models/
    playback.py

The important abstraction is:

MediaItem
     +
ClientCapabilities
     ↓
PlaybackService
     ↓
PlaybackPlan

A PlaybackPlan should describe what Aarkib intends to deliver, not perform the delivery itself.

For example:

{
  "mode": "direct",
  "container": "mp4",
  "video_codec": "h264",
  "audio_codec": "aac"
}

or:

{
  "mode": "transcode",
  "container": "mp4",
  "video_codec": "h264",
  "audio_codec": "aac",
  "resolution": "1080p"
}

Possible modes:

DIRECT
REMUX
TRANSCODE
OPTIMIZE

The planner should be deterministic.

same media
+
same capabilities
=
same PlaybackPlan

unless explicit server configuration changes.
Important separation

Do not put FFmpeg logic inside the planner.

PlaybackService
      │
      ▼
PlaybackPlan
      │
      ▼
Transcoder

This separation will make the system much easier to test.
Phase 3 — Make streaming use the planner

Find every current path that decides:

    direct file delivery

    streaming

    transcoding

    media conversion

Refactor them so the flow becomes:

HTTP request
     ↓
authenticate
     ↓
load MediaItem
     ↓
detect capabilities
     ↓
PlaybackService.plan()
     ↓
PlaybackPlan
     ↓
executor

The executor then does the actual work.

PlaybackPlan
   │
   ├── DIRECT → file/range response
   ├── REMUX  → FFmpeg remux
   └── TRANSCODE → FFmpeg transcode

This should be done without breaking existing endpoints.
Phase 4 — Hardware acceleration

Once the playback planner is stable, add hardware acceleration.

Create a capability/configuration abstraction:

TranscodeCapabilities
├── software
├── vaapi
├── qsv

Detect available hardware at startup.

Expose configuration:

Transcoding
  Backend:
    Auto
    Software
    VA-API
    QSV

The planner chooses:

PlaybackPlan
    ↓
TranscodeProfile
    ↓
FFmpeg command

Safety requirement

FFmpeg invocation must remain:

subprocess.run([...], shell=False)

Never construct shell commands from media filenames or user input.
Phase 5 — Job system upgrade

Don't replace Aarkib's existing job manager.

Improve it.

Introduce explicit states:

QUEUED
  ↓
RUNNING
  ├──→ SUCCEEDED
  ├──→ FAILED
  └──→ CANCELLED

Add:

progress
message
error
retry_count
started_at
finished_at
cancel_requested

The agent should identify existing jobs and migrate them into this model rather than creating a parallel job system.
Phase 6 — Library reconciliation

Improve scanning reliability.

Current conceptual flow:

filesystem
     ↓
scanner
     ↓
database

Make it:

filesystem
     ↓
scan snapshot
     ↓
compare
 ┌───┼────┐
new changed missing
 │     │     │
 ▼     ▼     ▼
add   update reconcile

Critical safety mechanism

Never immediately interpret an empty scan as:

    "Everything was deleted."

Require filesystem availability validation first.

For example:

library path unavailable
        ↓
ABORT reconciliation
        ↓
keep existing records

This protects users from NAS/network mount failures.
Phase 7 — Profiles

Extend the existing user system rather than replacing it.

Target model:

Account
   │
   ├── Profile
   │     ├── library access
   │     ├── progress
   │     ├── bookmarks
   │     └── preferences
   │
   └── Profile

Initially keep it simple.
Profile permissions

Support:

library.read
library.download
media.stream
media.transcode
metadata.edit
admin

Then library-level ACLs:

Profile A
  ├── Books       ✓
  ├── Comics      ✓
  ├── Movies      ✗
  └── Adult       ✗

Don't implement parental filtering through scattered route checks.

Create one authorization service:

authorization.can(profile, action, resource)

Phase 8 — Metadata matching

This should come after the architecture above.

Introduce:

MetadataProvider
        │
        ├── OpenLibrary
        ├── existing providers
        └── future providers

And:

MetadataMatcher
        │
        ▼
Candidate[]
        │
        ▼
confidence
        │
        ▼
selected match

Store provenance:

title
value
source
source_id
confidence
updated_at

Don't overwrite manually edited metadata.

Distinguish:

automatic metadata
manual metadata
derived metadata

Phase 9 — API

Expose the new concepts cleanly.

Potential API:

GET /api/v1/media/:id
GET /api/v1/media/:id/playback-plan

GET /api/v1/profiles
POST /api/v1/profiles
PATCH /api/v1/profiles/:id

GET /api/v1/jobs
GET /api/v1/jobs/:id
POST /api/v1/jobs/:id/cancel

GET /api/v1/libraries/:id/reconcile

Don't expose internal implementation details.

For example, clients should request:

/playback-plan

rather than asking:

/use-nvenc

The server should remain responsible for making the decision.
Phase 10 — UI

Only after the backend is stable.

Add:
Playback diagnostics

Playback
────────────────────
Client: Chrome
Resolution: 4K

✓ H264
✓ AAC
✓ MP4

Decision: Direct Play

For transcoding:

Decision: Transcode

Reason:
Audio codec unsupported

Video: H264
Audio: AAC

Hardware: VA-API

This will be extremely useful for debugging.
Jobs

Background Jobs

Library scan       ██████████ 100% ✓
Metadata matching  ██████░░░░  61%
EPUB optimization  queued

Profiles

Profiles

Alice
  Books ✓
  Comics ✓
  Movies ✓

Kids
  Books ✓
  Comics ✓
  Movies ✗
