"""Lightweight background maintenance scheduler for Aarkib."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from flask import Flask

logger = logging.getLogger("aarkib.scheduler")

_SCHEDULER_INSTANCE: SchedulerService | None = None
_SCHEDULER_LOCK = threading.Lock()


class SchedulerService:
    """Lightweight background maintenance scheduler for Aarkib."""

    def __init__(self, app: Flask, tick_interval_seconds: float = 60.0):
        self.app = app
        self.tick_interval_seconds = tick_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Timestamps of last runs (UTC)
        self.last_backup_time: datetime | None = None
        self.last_rescan_time: datetime | None = None
        self.last_reap_time: datetime | None = None

    def start(self) -> None:
        """Starts the scheduler background daemon thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="aarkib-scheduler",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "Aarkib scheduler started (tick interval: %ss)",
                self.tick_interval_seconds,
            )

    def stop(self) -> None:
        """Signals the scheduler thread to stop and joins."""
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return
            self._stop_event.set()
        self._thread.join(timeout=2.0)
        try:
            logger.info("Aarkib scheduler stopped")
        except Exception:
            pass

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def reconfigure(self, app: Flask) -> None:
        """Updates internal application reference."""
        with self._lock:
            self.app = app
        logger.debug("Aarkib scheduler reconfigured with updated settings")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_pending()
            except Exception as e:
                logger.error("Error running scheduler tasks: %s", e, exc_info=True)

            if self._stop_event.wait(timeout=self.tick_interval_seconds):
                break

    def run_pending(self, now: datetime | None = None) -> dict[str, bool]:
        """Checks and triggers due maintenance jobs. Returns dict of actions taken."""
        if now is None:
            now = datetime.now(UTC)

        actions: dict[str, bool] = {
            "backup_triggered": False,
            "rescan_triggered": False,
            "reap_triggered": False,
        }

        # 1. Scheduled Backups
        backup_schedule = str(
            self.app.config.get("BACKUP_SCHEDULE", "disabled")
        ).lower()
        if backup_schedule in ("daily", "weekly"):
            due_delta = (
                timedelta(days=1) if backup_schedule == "daily" else timedelta(days=7)
            )
            if (
                self.last_backup_time is None
                or (now - self.last_backup_time) >= due_delta
            ):
                if self._trigger_backup(now):
                    self.last_backup_time = now
                    actions["backup_triggered"] = True

        # 2. Periodic Library Rescan
        try:
            rescan_hours = int(self.app.config.get("PERIODIC_RESCAN_HOURS", 0))
        except ValueError, TypeError:
            rescan_hours = 0
        if rescan_hours > 0:
            rescan_delta = timedelta(hours=rescan_hours)
            if (
                self.last_rescan_time is None
                or (now - self.last_rescan_time) >= rescan_delta
            ):
                if self._trigger_rescan(now):
                    self.last_rescan_time = now
                    actions["rescan_triggered"] = True

        # 3. Transcode & Cache Reaper
        try:
            reap_hours = int(self.app.config.get("CACHE_REAP_HOURS", 24))
        except ValueError, TypeError:
            reap_hours = 24
        if reap_hours > 0:
            reap_delta = timedelta(hours=reap_hours)
            if self.last_reap_time is None or (now - self.last_reap_time) >= reap_delta:
                if self._trigger_reap(now):
                    self.last_reap_time = now
                    actions["reap_triggered"] = True

        return actions

    def _trigger_backup(self, now: datetime) -> bool:
        from aarkib.services.backup import create_backup
        from aarkib.services.job_manager import job_manager

        try:
            job_manager.submit_job(
                "scheduled_backup",
                create_backup,
                self.app,
            )
            logger.info("Scheduled backup job submitted successfully")
            return True
        except Exception as e:
            logger.error("Failed to submit scheduled backup job: %s", e)
            return False

    def _trigger_rescan(self, now: datetime) -> bool:
        from aarkib.services.job_manager import job_manager
        from aarkib.services.scanner import scan_library

        try:
            job_manager.submit_job(
                "scheduled_scan",
                scan_library,
                self.app,
            )
            logger.info("Periodic library rescan job submitted successfully")
            return True
        except Exception as e:
            logger.error("Failed to submit scheduled library rescan job: %s", e)
            return False

    def _trigger_reap(self, now: datetime) -> bool:
        from pathlib import Path

        from aarkib.services.transcoder import transcode_supervisor

        try:
            raw_dir: Path | str = self.app.config.get("TRANSCODE_DIR", "data/transcode")
            transcode_dir = Path(raw_dir)
            transcode_supervisor.clean_stale_directories(transcode_dir)
            logger.info("Cache reaper executed successfully")
            return True
        except Exception as e:
            logger.error("Failed executing cache reaper: %s", e)
            return False

    def get_status(self) -> dict[str, Any]:
        """Returns diagnostic status of the scheduler."""
        return {
            "is_running": self.is_running(),
            "tick_interval_seconds": self.tick_interval_seconds,
            "backup_schedule": self.app.config.get("BACKUP_SCHEDULE", "disabled"),
            "backup_retention_count": self.app.config.get("BACKUP_RETENTION_COUNT", 7),
            "periodic_rescan_hours": self.app.config.get("PERIODIC_RESCAN_HOURS", 0),
            "cache_reap_hours": self.app.config.get("CACHE_REAP_HOURS", 24),
            "last_backup_time": (
                self.last_backup_time.isoformat() if self.last_backup_time else None
            ),
            "last_rescan_time": (
                self.last_rescan_time.isoformat() if self.last_rescan_time else None
            ),
            "last_reap_time": (
                self.last_reap_time.isoformat() if self.last_reap_time else None
            ),
        }


def get_scheduler() -> SchedulerService | None:
    """Returns the active global scheduler instance, if initialized."""
    return _SCHEDULER_INSTANCE


def start_scheduler(
    app: Flask, tick_interval_seconds: float = 60.0
) -> SchedulerService:
    """Initializes and starts the global maintenance scheduler daemon."""
    global _SCHEDULER_INSTANCE
    with _SCHEDULER_LOCK:
        if _SCHEDULER_INSTANCE is None:
            _SCHEDULER_INSTANCE = SchedulerService(
                app, tick_interval_seconds=tick_interval_seconds
            )
            _SCHEDULER_INSTANCE.start()
        return _SCHEDULER_INSTANCE


def stop_scheduler() -> None:
    """Stops and tears down the global maintenance scheduler daemon."""
    global _SCHEDULER_INSTANCE
    with _SCHEDULER_LOCK:
        if _SCHEDULER_INSTANCE is not None:
            _SCHEDULER_INSTANCE.stop()
            _SCHEDULER_INSTANCE = None
