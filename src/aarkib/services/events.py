"""Internal thread-safe event dispatcher for cross-service notifications and integrations."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("aarkib.events")

# Standard system event identifiers
EVENT_SCAN_STARTED = "scan.started"
EVENT_SCAN_PROGRESS = "scan.progress"
EVENT_SCAN_FINISHED = "scan.finished"
EVENT_MEDIA_ADDED = "media.added"

EVENT_BACKUP_STARTED = "backup.started"
EVENT_BACKUP_FINISHED = "backup.finished"

EVENT_PLAYBACK_UPDATED = "playback.updated"
EVENT_TRANSCODE_UPDATED = "transcode.updated"


class EventDispatcher:
    """Thread-safe in-memory publish-subscribe event dispatcher."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable[[Any], None]]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, event_name: str, handler: Callable[[Any], None]) -> None:
        """Registers a callback for the given event name."""
        with self._lock:
            if handler not in self._listeners[event_name]:
                self._listeners[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: Callable[[Any], None]) -> None:
        """Removes a registered callback."""
        with self._lock:
            if handler in self._listeners[event_name]:
                self._listeners[event_name].remove(handler)

    def emit(self, event_name: str, payload: Any = None) -> None:
        """Invokes all registered callbacks for the event. Catches and logs exceptions."""
        with self._lock:
            handlers = list(self._listeners.get(event_name, []))

        for handler in handlers:
            try:
                handler(payload)
            except Exception as exc:
                logger.debug(
                    "Error executing handler %s for event '%s': %s",
                    getattr(handler, "__name__", str(handler)),
                    event_name,
                    exc,
                )

    def clear(self) -> None:
        """Removes all registered listeners (primarily for testing)."""
        with self._lock:
            self._listeners.clear()


# Global singleton event bus
event_bus = EventDispatcher()

__all__ = [
    "EVENT_BACKUP_FINISHED",
    "EVENT_BACKUP_STARTED",
    "EVENT_MEDIA_ADDED",
    "EVENT_PLAYBACK_UPDATED",
    "EVENT_SCAN_FINISHED",
    "EVENT_SCAN_PROGRESS",
    "EVENT_SCAN_STARTED",
    "EVENT_TRANSCODE_UPDATED",
    "EventDispatcher",
    "event_bus",
]
