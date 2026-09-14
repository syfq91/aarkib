"""Tests for the Scheduled Maintenance & Automation Subsystem."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from aarkib import create_app
from aarkib.extensions import db
from aarkib.models import User
from aarkib.services.scheduler import (
    SchedulerService,
    get_scheduler,
    start_scheduler,
    stop_scheduler,
)
from aarkib.services.settings_service import update_settings


@pytest.fixture
def sched_app(tmp_path: Path) -> Flask:
    """Creates a test app configured for scheduler testing."""
    db_file = tmp_path / "sched_test.db"
    covers_dir = tmp_path / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    transcode_dir = tmp_path / "transcode"
    transcode_dir.mkdir(parents=True, exist_ok=True)

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_file}",
            "SECRET_KEY": "sched-secret",
            "WTF_CSRF_ENABLED": False,
            "COVERS_DIR": str(covers_dir),
            "BACKUPS_DIR": str(backups_dir),
            "TRANSCODE_DIR": str(transcode_dir),
            "BACKUP_SCHEDULE": "disabled",
            "BACKUP_RETENTION_COUNT": 7,
            "PERIODIC_RESCAN_HOURS": 0,
            "CACHE_REAP_HOURS": 24,
        }
    )

    with app.app_context():
        admin = User(username="admin", is_admin=True)
        admin.set_password("adminpass")
        db.session.add(admin)
        db.session.commit()

    return app


def test_scheduler_service_lifecycle(sched_app: Flask) -> None:
    """Verify SchedulerService starts, detects running state, and stops cleanly."""
    scheduler = SchedulerService(sched_app, tick_interval_seconds=0.1)
    assert not scheduler.is_running()

    scheduler.start()
    assert scheduler.is_running()

    # Idempotent start
    scheduler.start()
    assert scheduler.is_running()

    scheduler.stop()
    assert not scheduler.is_running()

    # Idempotent stop
    scheduler.stop()
    assert not scheduler.is_running()


def test_scheduler_global_start_stop(sched_app: Flask) -> None:
    """Verify global start_scheduler, get_scheduler, and stop_scheduler helpers."""
    sched = start_scheduler(sched_app, tick_interval_seconds=0.1)
    assert sched is not None
    assert sched.is_running()
    assert get_scheduler() is sched

    stop_scheduler()
    assert not sched.is_running()


def test_scheduler_daily_backup_trigger(sched_app: Flask) -> None:
    """Verify daily backup scheduling triggers on startup and after 24 hours."""
    sched_app.config["BACKUP_SCHEDULE"] = "daily"
    scheduler = SchedulerService(sched_app)

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    with patch.object(scheduler, "_trigger_backup", return_value=True) as mock_backup:
        # First execution triggers immediately
        res = scheduler.run_pending(now=t0)
        assert res["backup_triggered"] is True
        assert mock_backup.call_count == 1

        # 12 hours later: not due yet
        t1 = t0 + timedelta(hours=12)
        res1 = scheduler.run_pending(now=t1)
        assert res1["backup_triggered"] is False
        assert mock_backup.call_count == 1

        # 24 hours + 1 minute later: due again
        t2 = t0 + timedelta(hours=24, minutes=1)
        res2 = scheduler.run_pending(now=t2)
        assert res2["backup_triggered"] is True
        assert mock_backup.call_count == 2


def test_scheduler_weekly_backup_trigger(sched_app: Flask) -> None:
    """Verify weekly backup scheduling triggers on startup and after 7 days."""
    sched_app.config["BACKUP_SCHEDULE"] = "weekly"
    scheduler = SchedulerService(sched_app)

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    with patch.object(scheduler, "_trigger_backup", return_value=True) as mock_backup:
        # First execution triggers immediately
        res = scheduler.run_pending(now=t0)
        assert res["backup_triggered"] is True
        assert mock_backup.call_count == 1

        # 6 days later: not due yet
        t1 = t0 + timedelta(days=6)
        res1 = scheduler.run_pending(now=t1)
        assert res1["backup_triggered"] is False
        assert mock_backup.call_count == 1

        # 7 days + 1 minute later: due again
        t2 = t0 + timedelta(days=7, minutes=1)
        res2 = scheduler.run_pending(now=t2)
        assert res2["backup_triggered"] is True
        assert mock_backup.call_count == 2


def test_scheduler_disabled_backup(sched_app: Flask) -> None:
    """Verify backup is never triggered when BACKUP_SCHEDULE is 'disabled'."""
    sched_app.config["BACKUP_SCHEDULE"] = "disabled"
    scheduler = SchedulerService(sched_app)

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    with patch.object(scheduler, "_trigger_backup", return_value=True) as mock_backup:
        res = scheduler.run_pending(now=t0)
        assert res["backup_triggered"] is False
        assert mock_backup.call_count == 0

        # 30 days later: still not triggered
        t1 = t0 + timedelta(days=30)
        res1 = scheduler.run_pending(now=t1)
        assert res1["backup_triggered"] is False
        assert mock_backup.call_count == 0


def test_scheduler_periodic_rescan_trigger(sched_app: Flask) -> None:
    """Verify periodic rescan triggers when configured hours elapse."""
    sched_app.config["PERIODIC_RESCAN_HOURS"] = 6
    scheduler = SchedulerService(sched_app)

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    with patch.object(scheduler, "_trigger_rescan", return_value=True) as mock_rescan:
        # First execution triggers immediately
        res = scheduler.run_pending(now=t0)
        assert res["rescan_triggered"] is True
        assert mock_rescan.call_count == 1

        # 5 hours later: not due
        t1 = t0 + timedelta(hours=5)
        res1 = scheduler.run_pending(now=t1)
        assert res1["rescan_triggered"] is False
        assert mock_rescan.call_count == 1

        # 6 hours + 1 minute later: due
        t2 = t0 + timedelta(hours=6, minutes=1)
        res2 = scheduler.run_pending(now=t2)
        assert res2["rescan_triggered"] is True
        assert mock_rescan.call_count == 2


def test_scheduler_disabled_rescan(sched_app: Flask) -> None:
    """Verify periodic rescan never triggers when PERIODIC_RESCAN_HOURS is 0."""
    sched_app.config["PERIODIC_RESCAN_HOURS"] = 0
    scheduler = SchedulerService(sched_app)

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    with patch.object(scheduler, "_trigger_rescan", return_value=True) as mock_rescan:
        res = scheduler.run_pending(now=t0)
        assert res["rescan_triggered"] is False
        assert mock_rescan.call_count == 0


def test_scheduler_cache_reaper_trigger(sched_app: Flask) -> None:
    """Verify stale cache reaper triggers according to CACHE_REAP_HOURS."""
    sched_app.config["CACHE_REAP_HOURS"] = 12
    scheduler = SchedulerService(sched_app)

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    with patch.object(scheduler, "_trigger_reap", return_value=True) as mock_reap:
        # First execution triggers
        res = scheduler.run_pending(now=t0)
        assert res["reap_triggered"] is True
        assert mock_reap.call_count == 1

        # 6 hours later: not due
        t1 = t0 + timedelta(hours=6)
        res1 = scheduler.run_pending(now=t1)
        assert res1["reap_triggered"] is False
        assert mock_reap.call_count == 1

        # 12.5 hours later: due
        t2 = t0 + timedelta(hours=12, minutes=30)
        res2 = scheduler.run_pending(now=t2)
        assert res2["reap_triggered"] is True
        assert mock_reap.call_count == 2


def test_scheduler_get_status(sched_app: Flask) -> None:
    """Verify get_status returns dictionary with current diagnostic metrics."""
    sched_app.config["BACKUP_SCHEDULE"] = "weekly"
    sched_app.config["PERIODIC_RESCAN_HOURS"] = 24
    sched_app.config["CACHE_REAP_HOURS"] = 12
    scheduler = SchedulerService(sched_app)

    status = scheduler.get_status()
    assert status["is_running"] is False
    assert status["backup_schedule"] == "weekly"
    assert status["periodic_rescan_hours"] == 24
    assert status["cache_reap_hours"] == 12
    assert status["last_backup_time"] is None
    assert status["last_rescan_time"] is None
    assert status["last_reap_time"] is None


def test_scheduler_reconfigure(sched_app: Flask) -> None:
    """Verify reconfigure hot-updates scheduler configuration."""
    scheduler = SchedulerService(sched_app)
    assert scheduler.get_status()["backup_schedule"] == "disabled"

    sched_app.config["BACKUP_SCHEDULE"] = "daily"
    sched_app.config["PERIODIC_RESCAN_HOURS"] = 48
    scheduler.reconfigure(sched_app)

    status = scheduler.get_status()
    assert status["backup_schedule"] == "daily"
    assert status["periodic_rescan_hours"] == 48


def test_settings_service_hot_reconfigures_scheduler(sched_app: Flask) -> None:
    """Verify updating settings via settings_service propagates directly to scheduler."""
    scheduler = start_scheduler(sched_app)
    try:
        with sched_app.app_context():
            update_settings(
                sched_app,
                {
                    "BACKUP_SCHEDULE": "weekly",
                    "PERIODIC_RESCAN_HOURS": 12,
                    "BACKUP_RETENTION_COUNT": 14,
                },
            )

        status = scheduler.get_status()
        assert status["backup_schedule"] == "weekly"
        assert status["periodic_rescan_hours"] == 12
        assert status["backup_retention_count"] == 14
    finally:
        stop_scheduler()


def test_trigger_real_backup_and_rescan_job_submission(sched_app: Flask) -> None:
    """Verify run_pending submits background jobs via job_manager and updates timestamps."""
    from aarkib.services.job_manager import job_manager

    sched_app.config["BACKUP_SCHEDULE"] = "daily"
    sched_app.config["PERIODIC_RESCAN_HOURS"] = 6
    scheduler = SchedulerService(sched_app)
    now = datetime.now(UTC)

    with patch.object(job_manager, "submit_job", return_value=MagicMock()) as mock_sub:
        res = scheduler.run_pending(now=now)
        assert res["backup_triggered"] is True
        assert res["rescan_triggered"] is True
        assert scheduler.last_backup_time == now
        assert scheduler.last_rescan_time == now
        assert mock_sub.call_count == 2
