# 🔔 Apprise Notification Subsystem Architectural Plan (`apprise.md`)

This document outlines the architectural blueprint, data flow, event triggers, configuration models, WebUI integration, and implementation plan for introducing multi-channel push notification support into **Aarkib** using [Apprise](https://github.com/caronc/apprise).

---

## 🏛️ 1. Executive Summary & Philosophy

**Apprise** is a lightweight, battle-tested notification framework supporting over 80+ notification services (Discord, Telegram, Pushover, Gotify, Ntfy, Slack, Matrix, Email/SMTP, Webhooks, Pushbullet, Twilio, and more) via simple, uniform URLs (e.g., `discord://webhook_id/webhook_token`, `tgram://bot_token/chat_id`, `gotify://hostname/token`).

```mermaid
flowchart TD
    subgraph Aarkib ["Aarkib Media Server"]
        Core[Flask Services & Background Jobs]
        
        subgraph Triggers ["Event Triggers"]
            ScanEvent["Library Scanner: Media Added / Scan Complete"]
            BackupEvent["Backup Service: Backup Created / Failed"]
            SecurityEvent["Auth Rate Limiter: IP Lockout Alert"]
            SystemEvent["System / Job Manager: Unhandled Job Crash"]
        end
        
        subgraph Notifier ["Notification Subsystem (notifier.py)"]
            EventDispatcher[Event Debouncer & Batch Digest Queue]
            AppriseWorker[Asynchronous Notification Worker Thread]
            AppriseInstance[Apprise Engine: apprise.Apprise]
        end
        
        DB[(SQLite WAL: aarkib.db)]
    end

    subgraph Targets ["Supported Apprise Notification Services"]
        Discord["Discord Webhooks (discord://...)"]
        Telegram["Telegram Bots (tgram://...)"]
        Pushover["Pushover (pover://...)"]
        Ntfy["Ntfy / Gotify (ntfy://..., gotify://...)"]
        Email["Email / SMTP (mailto://...)"]
        Webhooks["Custom Webhooks (json://..., post://...)"]
    end

    ScanEvent --> EventDispatcher
    BackupEvent --> EventDispatcher
    SecurityEvent --> EventDispatcher
    SystemEvent --> EventDispatcher
    
    EventDispatcher -->|Buffered Batch Digest| AppriseWorker
    AppriseWorker --> AppriseInstance
    AppriseInstance -.->|Non-blocking HTTP / Socket| Discord
    AppriseInstance -.->|Non-blocking HTTP / Socket| Telegram
    AppriseInstance -.->|Non-blocking HTTP / Socket| Pushover
    AppriseInstance -.->|Non-blocking HTTP / Socket| Ntfy
    AppriseInstance -.->|Non-blocking HTTP / Socket| Email
    AppriseInstance -.->|Non-blocking HTTP / Socket| Webhooks
```

### Core Invariants & Safety Rules
1. **Non-Blocking Execution**: External notification network I/O must never delay HTTP requests, media streaming, scanner crawls, or SQLite writes. Notifications are queued in-memory and dispatched on a dedicated background worker thread.
2. **Database Isolation**: Notifications must be triggered **after** database transactions are committed. Never hold SQLite transaction locks or database cursors open while invoking Apprise.
3. **Notification Debouncing & Batching**: When a scanner indexes 500 new books or songs in a single crawl, Aarkib must **not** fire 500 individual push notifications. Rapid discovery events are automatically debounced into a consolidated digest (e.g., *"Aarkib added 42 new books and 12 comics"*).
4. **Secret Protection**: Apprise URLs embed sensitive API keys, bot tokens, and passwords. URLs must be treated as secrets (`is_secret=True`), masked in WebUI forms, and scrubbed from server log output.
5. **Multi-Arch & Zero Compilation**: `apprise` is 100% pure Python, ensuring native portability across Linux `x86_64` (amd64) and `ARM64` (aarch64/Raspberry Pi) without C-compiler dependencies.

---

## 🎯 2. Notification Event Triggers

| Event ID | Event Category | Default Enabled | Description | Payload Data |
| :--- | :--- | :--- | :--- | :--- |
| `MEDIA_ADDED` | Media Discovery | Yes | New media item(s) indexed during a library scan. | Titles, authors/artists, media types, cover art URL, count. |
| `SCAN_COMPLETED` | Library Operations | Yes | Summary sent upon finishing a manual or scheduled library crawl. | New items count, modified items, crawl duration, total library size. |
| `BACKUP_COMPLETED` | Maintenance | Yes | Hot SQLite snapshot and cover archive created successfully. | Backup filename, archive size (MB), execution duration. |
| `BACKUP_FAILED` | Maintenance | Yes (High Priority) | Automated or manual backup failure. | Error message, stack trace summary. |
| `SECURITY_LOCKOUT` | Security | Yes (Critical) | Client IP blocked due to repeated failed authentication attempts. | Client IP, username attempted, lockout timestamp. |
| `JOB_FAILED` | Diagnostics | No | Background job error (transcoder crash, enrichment timeout). | Job UUID, job type, error message. |
| `TEST_NOTIFICATION` | Testing | Manual | On-demand test triggered by administrator in WebUI. | Server version, timestamp, active endpoints count. |

---

## ⚙️ 3. Configuration & Dynamic Settings

Settings are integrated into Aarkib's [`MANAGED_SETTINGS`](file:///home/syafiq/code/aarkib/src/aarkib/services/settings_service.py) framework and persisted in SQLite (`SystemSetting`), allowing hot-reconfiguration without server restarts.

### 3.1 Managed Setting Definitions

```python
# Added to src/aarkib/services/settings_service.py: MANAGED_SETTINGS

"NOTIFICATIONS_ENABLED": SettingDefinition(
    key="NOTIFICATIONS_ENABLED",
    type=bool,
    default=False,
    display_name="Enable Push Notifications",
    description="Enable push notifications for library updates, completed backups, and security alerts.",
    category="Notifications & Alerts",
),
"NOTIFICATION_URLS": SettingDefinition(
    key="NOTIFICATION_URLS",
    type=str,
    default="",
    display_name="Notification Service URLs",
    description="Newline-separated list of Apprise service URLs (e.g. discord://..., tgram://..., pover://...).",
    category="Notifications & Alerts",
    is_secret=True,
),
"NOTIFY_ON_MEDIA_ADDED": SettingDefinition(
    key="NOTIFY_ON_MEDIA_ADDED",
    type=bool,
    default=True,
    display_name="Notify on New Media",
    description="Send notification digest when new books, comics, audio, or videos are added.",
    category="Notifications & Alerts",
),
"NOTIFY_ON_SCAN_COMPLETED": SettingDefinition(
    key="NOTIFY_ON_SCAN_COMPLETED",
    type=bool,
    default=False,
    display_name="Notify on Scan Completion",
    description="Send a summary notification when a library crawl finishes.",
    category="Notifications & Alerts",
),
"NOTIFY_ON_BACKUP": SettingDefinition(
    key="NOTIFY_ON_BACKUP",
    type=bool,
    default=True,
    display_name="Notify on Backup Events",
    description="Send alert on backup success or failure.",
    category="Notifications & Alerts",
),
"NOTIFY_ON_SECURITY": SettingDefinition(
    key="NOTIFY_ON_SECURITY",
    type=bool,
    default=True,
    display_name="Notify on Security Alerts",
    description="Send critical alert on failed login lockouts.",
    category="Notifications & Alerts",
),
"NOTIFICATION_DIGEST_SECONDS": SettingDefinition(
    key="NOTIFICATION_DIGEST_SECONDS",
    type=int,
    default=60,
    display_name="Media Notification Digest Window (Seconds)",
    description="Buffer window to aggregate rapid media discoveries into a single notification digest (0 for instant).",
    category="Notifications & Alerts",
    min_value=0,
    max_value=3600,
),
```

---

## 🛠️ 4. Subsystem Architecture (`src/aarkib/services/notifier.py`)

### 4.1 Service Core Interface
```python
"""Apprise Notification Domain Service for Aarkib."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import apprise

logger = logging.getLogger("aarkib.notifier")


@dataclass
class NotificationMessage:
    title: str
    body: str
    notification_type: apprise.NotifyType = apprise.NotifyType.INFO
    body_format: apprise.NotifyFormat = apprise.NotifyFormat.MARKDOWN
    attach_url: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class NotificationService:
    """Manages Apprise instances, background dispatch queues, and debounced media digests."""

    def __init__(self) -> None:
        self._queue: queue.Queue[NotificationMessage | None] = queue.Queue(maxsize=1000)
        self._worker_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending_media_batch: list[dict[str, Any]] = []
        self._media_batch_timer: threading.Timer | None = None

    def start(self) -> None:
        """Starts the background worker thread."""
        with self._lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._worker_thread = threading.Thread(
                    target=self._process_queue,
                    name="AarkibNotifierWorker",
                    daemon=True,
                )
                self._worker_thread.start()

    def send(
        self,
        title: str,
        body: str,
        notification_type: apprise.NotifyType = apprise.NotifyType.INFO,
        attach_url: str | None = None,
    ) -> bool:
        """Enqueues a notification message for asynchronous delivery."""
        msg = NotificationMessage(
            title=title,
            body=body,
            notification_type=notification_type,
            attach_url=attach_url,
        )
        try:
            self._queue.put_nowait(msg)
            return True
        except queue.Full:
            logger.warning("Notification queue full, dropping alert: %s", title)
            return False

    def send_test(self, custom_urls: list[str] | None = None) -> tuple[bool, str]:
        """Synchronously tests Apprise URLs (used by admin WebUI test button)."""
        # Validates syntax and delivers test ping
        ...
```

### 4.2 Debouncing & Digest Strategy
When a library crawl discovers 50 files in succession:
1. `on_media_indexed(item)` appends metadata to `_pending_media_batch`.
2. A timer for `NOTIFICATION_DIGEST_SECONDS` is scheduled.
3. Upon expiry, the batch is synthesized into a Markdown digest:
   ```markdown
   **📚 New Media Added to Aarkib**
   Added **42** new items to your library:
   - *Project Hail Mary* by Andy Weir (Book)
   - *Batman: The Long Halloween* by Jeph Loeb (Comic)
   - *Random Access Memories* by Daft Punk (Music)
   *(and 39 more items)*
   ```
4. The digest is enqueued as a single push notification with the primary item's cover artwork.

---

## 🌐 5. REST API & WebUI Integration

### 5.1 REST Endpoints (`src/aarkib/routes/api.py`)

| Endpoint | Method | Auth Level | Description |
| :--- | :--- | :--- | :--- |
| `/api/notifications/test` | `POST` | Admin Only | Validates URLs and sends an immediate test notification. Returns status and error log if any provider fails. |
| `/api/notifications/services` | `GET` | Admin Only | Returns supported Apprise schemas, URL syntax examples, and documentation links. |
| `/api/notifications/status` | `GET` | Admin Only | Returns current configuration state and configured endpoints count. |

### 5.2 Settings WebUI Tab (`/settings/notifications`)

A clean Obsidian-themed settings panel in `src/aarkib/templates/settings/notifications.html`:

```html
<!-- Notifications Settings Tab -->
<div class="settings-card">
  <h3>🔔 Push Notifications (Apprise)</h3>
  <p class="text-muted">
    Deliver instant alerts to Discord, Telegram, Pushover, Gotify, Ntfy, Email, and 80+ other services.
  </p>

  <form id="notifications-form" method="POST" action="/settings/notifications">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

    <!-- Master Toggle -->
    <div class="form-group toggle-group">
      <label for="NOTIFICATIONS_ENABLED">Enable Notifications</label>
      <input type="checkbox" id="NOTIFICATIONS_ENABLED" name="NOTIFICATIONS_ENABLED" {% if settings.NOTIFICATIONS_ENABLED %}checked{% endif %}>
    </div>

    <!-- Multi-line URL Editor -->
    <div class="form-group">
      <label for="NOTIFICATION_URLS">Notification Service Endpoints (one per line)</label>
      <textarea id="NOTIFICATION_URLS" name="NOTIFICATION_URLS" rows="4" class="input-text" placeholder="discord://webhook_id/webhook_token&#10;tgram://bot_token/chat_id&#10;pover://user_key@token">{{ settings.NOTIFICATION_URLS }}</textarea>
      <small class="text-muted">Enter Apprise URLs. Passwords and tokens are securely encrypted in the database.</small>
    </div>

    <!-- Event Category Toggles -->
    <div class="grid-2">
      <label><input type="checkbox" name="NOTIFY_ON_MEDIA_ADDED" {% if settings.NOTIFY_ON_MEDIA_ADDED %}checked{% endif %}> New Media Added</label>
      <label><input type="checkbox" name="NOTIFY_ON_SCAN_COMPLETED" {% if settings.NOTIFY_ON_SCAN_COMPLETED %}checked{% endif %}> Library Scan Summary</label>
      <label><input type="checkbox" name="NOTIFY_ON_BACKUP" {% if settings.NOTIFY_ON_BACKUP %}checked{% endif %}> Database Backups</label>
      <label><input type="checkbox" name="NOTIFY_ON_SECURITY" {% if settings.NOTIFY_ON_SECURITY %}checked{% endif %}> Security & Lockout Alerts</label>
    </div>

    <div class="button-row" style="margin-top: 1.5rem; display: flex; gap: 1rem;">
      <button type="submit" class="btn btn-primary">Save Settings</button>
      <button type="button" id="btn-test-notification" class="btn btn-secondary">Send Test Notification</button>
    </div>
  </form>
</div>
```

---

## 📋 6. Common Apprise URL Cheatsheet for Aarkib Users

| Provider | URL Schema Example |
| :--- | :--- |
| **Discord** | `discord://webhook_id/webhook_token` |
| **Telegram** | `tgram://bot_token/chat_id` |
| **Pushover** | `pover://user_key@app_token` |
| **Gotify** | `gotify://hostname/app_token` |
| **Ntfy** | `ntfy://topic` or `ntfys://username:password@ntfy.sh/topic` |
| **Slack** | `slack://tokenA/tokenB/tokenC/channel` |
| **Matrix** | `matrixs://user:password@matrix.org/#channel:matrix.org` |
| **Email (SMTP)** | `mailto://user:pass@smtp.gmail.com:587?to=recipient@example.com` |
| **Custom Webhook** | `json://example.com/webhook` |

---

## 🚀 7. Step-by-Step Implementation Roadmap

```mermaid
gantt
    title Apprise Notification Subsystem Roadmap
    dateFormat  YYYY-MM-DD
    section 1. Dependencies & Foundation
    Add apprise via uv add apprise            :d1, 2026-10-01, 1d
    Managed Settings & Configuration         :d2, after d1, 1d
    section 2. Notifier Service
    Implement NotificationService (notifier.py) :d3, after d2, 2d
    Digest Debouncer & Worker Queue          :d4, after d3, 1d
    section 3. Event Hook Wiring
    Scanner & Media Event Hooks               :d5, after d4, 1d
    Backup Service Alert Hooks                :d6, after d5, 1d
    Security Rate Limiter Alert Hooks         :d7, after d6, 1d
    section 4. API & UI
    REST Test Endpoint (/api/notifications/test) :d8, after d7, 1d
    Settings Notifications WebUI View         :d9, after d8, 2d
    section 5. Testing & Verification
    Pytest Suite with Mocked Apprise Dispatch :d10, after d9, 2d
    Documentation & AGENTS.md Invariants      :d11, after d10, 1d
```

### Phase 1: Dependency & Core Service
1. Run `uv add apprise` to install pure-Python Apprise dependency and update `pyproject.toml` + `uv.lock`.
2. Register `NOTIFICATIONS_ENABLED` and related keys in [`MANAGED_SETTINGS`](file:///home/syafiq/code/aarkib/src/aarkib/services/settings_service.py).
3. Create `src/aarkib/services/notifier.py` containing `NotificationService`.

### Phase 2: Event Integration
1. **Library Scanner** ([`scanner.py`](file:///home/syafiq/code/aarkib/src/aarkib/services/scanner.py)): Wire batch debouncer into `scan_library()` to dispatch media discovery summaries.
2. **Backup Service** ([`backup.py`](file:///home/syafiq/code/aarkib/src/aarkib/services/backup.py)): Hook `create_backup()` to send success (with archive size) and failure notifications.
3. **Security Limiter** ([`auth.py`](file:///home/syafiq/code/aarkib/src/aarkib/routes/auth.py)): Hook `AuthRateLimiter` lockouts to fire security alerts.

### Phase 3: WebUI & Testing Endpoints
1. Add `/api/notifications/test` endpoint in [`src/aarkib/routes/api.py`](file:///home/syafiq/code/aarkib/src/aarkib/routes/api.py).
2. Add the Notifications category to Settings in [`src/aarkib/routes/ui.py`](file:///home/syafiq/code/aarkib/src/aarkib/routes/ui.py) and create `src/aarkib/templates/settings/notifications.html`.

### Phase 4: Deterministic Testing Suite
1. Create `tests/test_notifier.py` with 100% deterministic unit tests.
2. Mock `apprise.Apprise.notify` using `unittest.mock.patch` to verify zero network requests occur during tests.
3. Verify queue behavior, debouncing timer, secret masking, and invalid URL handling.
