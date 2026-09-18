"""Apprise Push Notification Domain Service for Aarkib."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger("aarkib.notifier")

try:
    import apprise

    APPRISE_AVAILABLE = True
except ImportError:
    apprise = None  # type: ignore[assignment]
    APPRISE_AVAILABLE = False


@dataclass
class NotificationMessage:
    """Represents a queued push notification message."""

    title: str
    body: str
    notification_type: str = "info"  # "info", "success", "warning", "failure"
    body_format: str = "markdown"  # "markdown" or "text"
    attach_url: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class NotificationService:
    """Manages Apprise instances, background dispatch queues, and debounced media digests."""

    def __init__(self) -> None:
        self._queue: queue.Queue[NotificationMessage | None] = queue.Queue(maxsize=1000)
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._pending_media_batch: list[dict[str, Any]] = []
        self._media_batch_timer: threading.Timer | None = None
        self._app: Flask | None = None
        self._stats: dict[str, int] = {"sent": 0, "failed": 0, "dropped": 0}

    def init_app(self, app: Flask) -> None:
        """Stores reference to Flask app for configuration access."""
        self._app = app

    def start(self) -> None:
        """Starts the background notification worker thread."""
        if not APPRISE_AVAILABLE:
            return

        with self._lock:
            if self._worker_thread and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._process_queue,
                name="AarkibNotifierWorker",
                daemon=True,
            )
            self._worker_thread.start()

    def stop(self) -> None:
        """Stops the worker thread and flushes any pending debounced media batch."""
        self._stop_event.set()
        with self._lock:
            if self._media_batch_timer:
                self._media_batch_timer.cancel()
                self._media_batch_timer = None

        self.flush_media_batch()

        # Unblock worker queue
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

        with self._lock:
            thread = self._worker_thread
            self._worker_thread = None

        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def _get_configured_apprise(
        self, custom_urls: list[str] | None = None
    ) -> Any | None:
        """Constructs and populates an Apprise instance with configured or custom URLs."""
        if not APPRISE_AVAILABLE or apprise is None:
            return None

        ap = apprise.Apprise()

        if custom_urls is not None:
            urls_to_load = custom_urls
        elif self._app:
            raw = self._app.config.get("NOTIFICATION_URLS", "") or ""
            urls_to_load = [line.strip() for line in str(raw).splitlines()]
        else:
            urls_to_load = []

        valid_count = 0
        for u in urls_to_load:
            u_clean = u.strip()
            if not u_clean or u_clean.startswith("#"):
                continue
            try:
                if ap.add(u_clean):
                    valid_count += 1
            except Exception as exc:
                logger.debug("Failed adding Apprise URL: %s", exc)

        return ap

    def get_endpoints_count(self, custom_urls: list[str] | None = None) -> int:
        """Returns the number of valid registered Apprise notification endpoints."""
        ap = self._get_configured_apprise(custom_urls=custom_urls)
        return len(ap) if ap is not None else 0

    def send(
        self,
        title: str,
        body: str,
        notification_type: str = "info",
        attach_url: str | None = None,
    ) -> bool:
        """Enqueues a notification message for asynchronous delivery."""
        if not APPRISE_AVAILABLE:
            return False

        # Ensure worker is started
        self.start()

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
            with self._lock:
                self._stats["dropped"] += 1
            return False

    def send_test(self, custom_urls: list[str] | None = None) -> tuple[bool, str, int]:
        """Synchronously tests Apprise URLs (used by admin WebUI test button).

        Returns:
            (success, status_message, servers_notified_count)
        """
        if not APPRISE_AVAILABLE or apprise is None:
            return False, "Apprise package is not installed on the server.", 0

        ap = self._get_configured_apprise(custom_urls=custom_urls)
        if ap is None or len(ap) == 0:
            return (
                False,
                "No valid notification URLs configured or provided.",
                0,
            )

        title = "🔔 Aarkib Test Notification"
        timestamp_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        body = (
            f"**Notification delivery confirmed!**\n\n"
            f"• **Server:** Aarkib Media Server\n"
            f"• **Active Endpoints:** {len(ap)}\n"
            f"• **Timestamp:** {timestamp_str}\n\n"
            f"Your push notification configuration is working correctly."
        )

        try:
            delivered = ap.notify(
                title=title,
                body=body,
                notify_type=apprise.NotifyType.INFO,
                body_format=apprise.NotifyFormat.MARKDOWN,
            )
            count = len(ap)
            if delivered:
                return (
                    True,
                    f"Test notification successfully dispatched to {count} service(s).",
                    count,
                )
            else:
                return (
                    False,
                    f"Failed dispatching to one or more configured endpoints ({count} total). Check server logs for provider errors.",
                    count,
                )
        except Exception as exc:
            logger.error("Exception during test notification: %s", exc)
            return False, f"Delivery error: {exc}", len(ap)

    def queue_media_item(
        self, item_data: dict[str, Any], digest_seconds: int = 60
    ) -> None:
        """Buffers a media item into the pending batch and arms the debounced digest timer."""
        if digest_seconds <= 0:
            # Instant delivery without debouncing
            self._dispatch_single_media(item_data)
            return

        with self._lock:
            self._pending_media_batch.append(item_data)
            if self._media_batch_timer is None:
                timer = threading.Timer(float(digest_seconds), self.flush_media_batch)
                timer.name = "AarkibMediaBatchTimer"
                timer.daemon = True
                self._media_batch_timer = timer
                timer.start()

    def _dispatch_single_media(self, item: dict[str, Any]) -> None:
        """Dispatches an immediate notification for a single media discovery."""
        title_str = item.get("title") or "New Media Item"
        creator_str = item.get("creator") or item.get("author") or ""
        media_type = (item.get("media_type") or "media").title()

        title = f"📚 New {media_type} Added: {title_str}"
        body_lines = [f"**{title_str}**"]
        if creator_str:
            body_lines.append(f"by *{creator_str}*")
        body_lines.append(f"Category: {media_type}")

        self.send(
            title=title,
            body="\n".join(body_lines),
            notification_type="info",
        )

    def flush_media_batch(self) -> None:
        """Flushes buffered media items into a consolidated Markdown digest notification."""
        with self._lock:
            if self._media_batch_timer:
                self._media_batch_timer.cancel()
                self._media_batch_timer = None
            items = list(self._pending_media_batch)
            self._pending_media_batch.clear()

        if not items:
            return

        total_count = len(items)
        if total_count == 1:
            self._dispatch_single_media(items[0])
            return

        # Breakdown by media type
        type_counts: dict[str, int] = {}
        for it in items:
            t = (it.get("media_type") or "item").title()
            type_counts[t] = type_counts.get(t, 0) + 1

        breakdown = ", ".join(
            f"{count} {mtype}s" for mtype, count in sorted(type_counts.items())
        )

        # Top 5 items list
        sample_lines = []
        for it in items[:5]:
            t_name = it.get("title") or "Untitled"
            creator = it.get("creator") or it.get("author")
            m_type = (it.get("media_type") or "Item").title()
            if creator:
                sample_lines.append(f"• *{t_name}* by {creator} ({m_type})")
            else:
                sample_lines.append(f"• *{t_name}* ({m_type})")

        if total_count > 5:
            remaining = total_count - 5
            sample_lines.append(f"*(and {remaining} more items...)*")

        title = f"📚 New Media Added to Aarkib ({total_count} items)"
        body = (
            f"**{total_count} new items** added to your Aarkib library ({breakdown}):\n\n"
            + "\n".join(sample_lines)
        )

        self.send(
            title=title,
            body=body,
            notification_type="info",
        )

    def _process_queue(self) -> None:
        """Worker loop processing notification messages from the queue."""
        while not self._stop_event.is_set():
            try:
                msg = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if msg is None:
                self._queue.task_done()
                break

            try:
                ap = self._get_configured_apprise()
                if ap is None or len(ap) == 0:
                    self._queue.task_done()
                    continue

                notify_type = apprise.NotifyType.INFO
                nt_str = (msg.notification_type or "info").lower()
                if nt_str in ("success", "ok"):
                    notify_type = apprise.NotifyType.SUCCESS
                elif nt_str in ("warning", "warn"):
                    notify_type = apprise.NotifyType.WARNING
                elif nt_str in ("failure", "failed", "error", "critical"):
                    notify_type = apprise.NotifyType.FAILURE

                delivered = ap.notify(
                    title=msg.title,
                    body=msg.body,
                    notify_type=notify_type,
                    body_format=apprise.NotifyFormat.MARKDOWN,
                    attach=msg.attach_url,
                )

                with self._lock:
                    if delivered:
                        self._stats["sent"] += 1
                    else:
                        self._stats["failed"] += 1

            except Exception as exc:
                logger.error("Error dispatching notification via Apprise: %s", exc)
                with self._lock:
                    self._stats["failed"] += 1
            finally:
                self._queue.task_done()

    def get_stats(self) -> dict[str, Any]:
        """Returns service statistics and health metrics."""
        with self._lock:
            return {
                "apprise_installed": APPRISE_AVAILABLE,
                "worker_alive": bool(
                    self._worker_thread and self._worker_thread.is_alive()
                ),
                "queue_size": self._queue.qsize(),
                "pending_batch_size": len(self._pending_media_batch),
                "sent_count": self._stats["sent"],
                "failed_count": self._stats["failed"],
                "dropped_count": self._stats["dropped"],
            }


# Global singleton instance
notification_service = NotificationService()

__all__ = [
    "APPRISE_AVAILABLE",
    "NotificationMessage",
    "NotificationService",
    "notification_service",
]
