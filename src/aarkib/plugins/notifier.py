"""Push Notification Plugin for Aarkib powered by Apprise."""

from __future__ import annotations

import atexit
import logging
from typing import TYPE_CHECKING, Any

from aarkib.plugins.base import BasePlugin
from aarkib.services.events import (
    EVENT_BACKUP_FINISHED,
    EVENT_JOB_FAILED,
    EVENT_MEDIA_ADDED,
    EVENT_SCAN_FINISHED,
    EVENT_SECURITY_LOCKOUT,
    event_bus,
)
from aarkib.services.notifier import APPRISE_AVAILABLE, notification_service

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger("aarkib.notifier.plugin")


class NotificationPlugin(BasePlugin):
    """Integrates Aarkib with Apprise for multi-channel push notifications."""

    name = "notifications"
    display_name = "Push Notifications (Apprise)"
    plugin_type = "integration"
    description = (
        "Multi-channel push notifications for Discord, Telegram, Pushover, "
        "Gotify, Ntfy, Email, and 80+ other services via Apprise."
    )
    csrf_exempt = False
    config_keys = [
        "ENABLE_NOTIFICATIONS",
        "NOTIFICATION_URLS",
        "NOTIFY_ON_MEDIA_ADDED",
        "NOTIFY_ON_SCAN_COMPLETED",
        "NOTIFY_ON_BACKUP",
        "NOTIFY_ON_SECURITY",
        "NOTIFICATION_DIGEST_SECONDS",
    ]

    def __init__(self) -> None:
        self.enabled: bool = False
        self._app: Flask | None = None

    def init_app(self, app: Flask) -> None:
        """Lifecycle hook invoked when Flask initializes."""
        self._app = app
        notification_service.init_app(app)

        is_enabled = app.config.get("ENABLE_NOTIFICATIONS", False)
        if isinstance(is_enabled, str):
            is_enabled = is_enabled.lower() in ("true", "1", "yes", "on")

        self.enabled = bool(is_enabled)
        if not self.enabled or not APPRISE_AVAILABLE:
            return

        self.start()
        atexit.register(self.stop)

    def start(self) -> None:
        """Starts notification service worker and subscribes to domain events."""
        if not APPRISE_AVAILABLE:
            logger.debug("Apprise is not available; NotificationPlugin cannot start.")
            return

        notification_service.start()

        # Subscribe to internal event bus
        event_bus.subscribe(EVENT_MEDIA_ADDED, self._on_media_added)
        event_bus.subscribe(EVENT_SCAN_FINISHED, self._on_scan_finished)
        event_bus.subscribe(EVENT_BACKUP_FINISHED, self._on_backup_finished)
        event_bus.subscribe(EVENT_SECURITY_LOCKOUT, self._on_security_lockout)
        event_bus.subscribe(EVENT_JOB_FAILED, self._on_job_failed)

    def stop(self) -> None:
        """Unsubscribes from events and stops the notification service worker."""
        event_bus.unsubscribe(EVENT_MEDIA_ADDED, self._on_media_added)
        event_bus.unsubscribe(EVENT_SCAN_FINISHED, self._on_scan_finished)
        event_bus.unsubscribe(EVENT_BACKUP_FINISHED, self._on_backup_finished)
        event_bus.unsubscribe(EVENT_SECURITY_LOCKOUT, self._on_security_lockout)
        event_bus.unsubscribe(EVENT_JOB_FAILED, self._on_job_failed)

        notification_service.stop()

    def _on_media_added(self, payload: Any) -> None:
        """Handles single media item addition with debounced digest aggregation."""
        if not self.enabled or not self._app:
            return
        if not self._app.config.get("NOTIFY_ON_MEDIA_ADDED", True):
            return

        if not isinstance(payload, dict):
            return

        digest_seconds = int(
            self._app.config.get("NOTIFICATION_DIGEST_SECONDS", 60) or 0
        )
        notification_service.queue_media_item(payload, digest_seconds=digest_seconds)

    def _on_scan_finished(self, payload: Any) -> None:
        """Handles library scan completion, flushing pending media and sending summary if configured."""
        if not self.enabled or not self._app:
            return

        # Flush any remaining media digest items discovered during this scan immediately
        notification_service.flush_media_batch()

        if not self._app.config.get("NOTIFY_ON_SCAN_COMPLETED", False):
            return

        if not isinstance(payload, dict):
            return

        added = payload.get("added", 0)
        updated = payload.get("updated", 0)
        errors = payload.get("errors", 0)
        elapsed = payload.get("elapsed_seconds", 0)
        lib_path = payload.get("library_path") or "All Libraries"

        title = "🔍 Library Scan Completed"
        body = (
            f"**Library crawl completed:** `{lib_path}`\n\n"
            f"• **New Items:** {added}\n"
            f"• **Updated Items:** {updated}\n"
            f"• **Errors:** {errors}\n"
            f"• **Elapsed Time:** {elapsed:.1f}s"
        )
        notification_service.send(title=title, body=body, notification_type="info")

    def _on_backup_finished(self, payload: Any) -> None:
        """Handles database and covers backup completion or failure."""
        if not self.enabled or not self._app:
            return
        if not self._app.config.get("NOTIFY_ON_BACKUP", True):
            return

        if not isinstance(payload, dict):
            return

        success = payload.get("success", True)
        filename = payload.get("filename", "unknown-backup.zip")

        if success:
            size_mb = payload.get("size_mb", 0.0)
            media_count = payload.get("media_count", 0)
            title = "💾 Backup Created Successfully"
            body = (
                f"**Aarkib database & cover snapshot completed.**\n\n"
                f"• **Archive File:** `{filename}`\n"
                f"• **Archive Size:** {size_mb} MB\n"
                f"• **Media Records Archived:** {media_count}"
            )
            notification_service.send(
                title=title, body=body, notification_type="success"
            )
        else:
            error = payload.get("error", "Unknown error")
            title = "⚠️ Backup Creation Failed"
            body = (
                f"**Failed creating backup archive:** `{filename}`\n\n"
                f"• **Error Message:** {error}\n"
                f"Check server logs for diagnostic details."
            )
            notification_service.send(
                title=title, body=body, notification_type="failure"
            )

    def _on_security_lockout(self, payload: Any) -> None:
        """Handles authentication rate limiter client lockout event."""
        if not self.enabled or not self._app:
            return
        if not self._app.config.get("NOTIFY_ON_SECURITY", True):
            return

        if not isinstance(payload, dict):
            return

        ip = payload.get("ip", "Unknown IP")
        attempts = payload.get("attempts", 5)
        username = payload.get("username") or "unknown"

        title = "🚨 Security Alert: IP Lockout"
        body = (
            f"**Authentication Rate Limit Triggered**\n\n"
            f"Client IP **{ip}** has been temporarily locked out after **{attempts}** failed login attempts.\n\n"
            f"• **Target Username:** `{username}`\n"
            f"• **Action Taken:** Client IP blocked from login for 60 seconds."
        )
        notification_service.send(title=title, body=body, notification_type="failure")

    def _on_job_failed(self, payload: Any) -> None:
        """Handles background task failure."""
        if not self.enabled or not self._app:
            return

        if not isinstance(payload, dict):
            return

        job_type = payload.get("job_type", "task")
        job_id = payload.get("job_id", "")
        error = payload.get("error", "Unknown failure")

        title = f"⚠️ Background Task Failed: {job_type}"
        body = (
            f"**Background job `{job_id}` encountered an error:**\n\n```\n{error}\n```"
        )
        notification_service.send(title=title, body=body, notification_type="warning")

    def check_health(self) -> dict[str, Any]:
        """Returns health metrics for the notification plugin."""
        health = super().check_health()
        health.update(
            {
                "apprise_installed": APPRISE_AVAILABLE,
                "endpoints_count": notification_service.get_endpoints_count(),
                "service_metrics": notification_service.get_stats(),
            }
        )
        return health


__all__ = ["NotificationPlugin"]
