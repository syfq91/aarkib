"""Unit and integration tests for the Apprise push notification subsystem and plugin."""

from __future__ import annotations

import time
from unittest.mock import patch

from flask import Flask

from aarkib.plugins import plugin_registry
from aarkib.plugins.notifier import NotificationPlugin
from aarkib.services.events import (
    EVENT_SECURITY_LOCKOUT,
    event_bus,
)
from aarkib.services.notifier import (
    NotificationService,
    notification_service,
)
from aarkib.services.security import AuthRateLimiter
from aarkib.services.settings_service import (
    MANAGED_SETTINGS,
    get_effective_settings,
    update_settings,
)


def test_notification_plugin_registered(app: Flask):
    """Verify NotificationPlugin is registered in plugin_registry."""
    plugin = plugin_registry.get_plugin("notifications")
    assert plugin is not None
    assert isinstance(plugin, NotificationPlugin)
    assert plugin.name == "notifications"
    assert plugin.plugin_type == "integration"
    assert "ENABLE_NOTIFICATIONS" in plugin.config_keys
    assert "NOTIFICATION_URLS" in plugin.config_keys
    assert "NOTIFY_ON_MEDIA_ADDED" in plugin.config_keys


def test_notification_plugin_health_check(app: Flask):
    """Verify plugin health check structure and metrics."""
    plugin = NotificationPlugin()
    plugin._app = app
    health = plugin.check_health()
    assert health["plugin"] == "notifications"
    assert health["type"] == "integration"
    assert "apprise_installed" in health
    assert "endpoints_count" in health
    assert "service_metrics" in health


def test_managed_settings_notification_definitions():
    """Verify notification settings are defined with correct types and security flags."""
    assert "ENABLE_NOTIFICATIONS" in MANAGED_SETTINGS
    assert MANAGED_SETTINGS["ENABLE_NOTIFICATIONS"].type is bool
    assert MANAGED_SETTINGS["ENABLE_NOTIFICATIONS"].default is False

    assert "NOTIFICATION_URLS" in MANAGED_SETTINGS
    assert MANAGED_SETTINGS["NOTIFICATION_URLS"].is_secret is True

    assert "NOTIFY_ON_MEDIA_ADDED" in MANAGED_SETTINGS
    assert "NOTIFY_ON_BACKUP" in MANAGED_SETTINGS
    assert "NOTIFY_ON_SECURITY" in MANAGED_SETTINGS
    assert "NOTIFICATION_DIGEST_SECONDS" in MANAGED_SETTINGS
    assert MANAGED_SETTINGS["NOTIFICATION_DIGEST_SECONDS"].min_value == 0


def test_notification_service_send_and_worker(app: Flask):
    """Verify NotificationService enqueues and asynchronously dispatches notifications."""
    svc = NotificationService()
    svc.init_app(app)
    app.config["NOTIFICATION_URLS"] = "json://localhost/webhook"

    with patch("apprise.Apprise.notify", return_value=True) as mock_notify:
        sent = svc.send(
            title="Test Title",
            body="Test Body",
            notification_type="info",
        )
        assert sent is True

        # Wait briefly for background worker to process queue
        timeout = 2.0
        start = time.time()
        while svc._queue.qsize() > 0 and time.time() - start < timeout:
            time.sleep(0.05)

        svc.stop()
        mock_notify.assert_called()
        assert mock_notify.call_args[1]["title"] == "Test Title"


def test_notification_service_media_batching(app: Flask):
    """Verify rapid media items are debounced and consolidated into a single digest."""
    svc = NotificationService()
    svc.init_app(app)

    items = [
        {"title": f"Book {i}", "creator": "Author X", "media_type": "book"}
        for i in range(1, 8)
    ]

    with patch.object(svc, "send") as mock_send:
        # Queue multiple items with digest buffer
        for it in items:
            svc.queue_media_item(it, digest_seconds=60)

        assert len(svc._pending_media_batch) == 7
        # Flush the batch explicitly
        svc.flush_media_batch()
        assert len(svc._pending_media_batch) == 0

        # Exactly 1 consolidated digest notification should be sent
        assert mock_send.call_count == 1
        call_kwargs = mock_send.call_args[1]
        assert "7 items" in call_kwargs["title"]
        assert "Book 1" in call_kwargs["body"]
        assert "more items" in call_kwargs["body"]

    svc.stop()


def test_notification_service_single_media_instant(app: Flask):
    """Verify instant delivery when digest_seconds is 0."""
    svc = NotificationService()
    svc.init_app(app)

    with patch.object(svc, "send") as mock_send:
        svc.queue_media_item(
            {"title": "Solo Book", "creator": "Solo Author", "media_type": "book"},
            digest_seconds=0,
        )
        assert mock_send.call_count == 1
        call_kwargs = mock_send.call_args[1]
        assert "Solo Book" in call_kwargs["title"]

    svc.stop()


def test_notification_service_send_test(app: Flask):
    """Verify send_test works synchronously with provided or configured URLs."""
    svc = NotificationService()
    svc.init_app(app)

    # When no URLs provided or configured
    app.config["NOTIFICATION_URLS"] = ""
    ok, msg, count = svc.send_test(custom_urls=[])
    assert ok is False
    assert count == 0

    # With mocked successful delivery
    with patch("apprise.Apprise.notify", return_value=True):
        ok, msg, count = svc.send_test(
            custom_urls=["discord://webhook_id/webhook_token"]
        )
        assert ok is True
        assert count == 1
        assert "successfully dispatched" in msg


def test_notification_plugin_event_handling(app: Flask):
    """Verify NotificationPlugin handles domain events correctly."""
    plugin = NotificationPlugin()
    plugin.init_app(app)
    plugin.enabled = True
    app.config["ENABLE_NOTIFICATIONS"] = True
    app.config["NOTIFY_ON_MEDIA_ADDED"] = True
    app.config["NOTIFY_ON_SCAN_COMPLETED"] = True
    app.config["NOTIFY_ON_BACKUP"] = True
    app.config["NOTIFY_ON_SECURITY"] = True

    with (
        patch.object(notification_service, "send") as mock_send,
        patch.object(notification_service, "queue_media_item") as mock_queue,
    ):
        # 1. Media Added Event
        plugin._on_media_added({"title": "Test Comic", "media_type": "comic"})
        mock_queue.assert_called_once()

        # 2. Scan Finished Event
        plugin._on_scan_finished(
            {"added": 5, "updated": 1, "errors": 0, "elapsed_seconds": 2.5}
        )
        assert mock_send.call_count == 1
        assert "Scan Completed" in mock_send.call_args[1]["title"]

        # 3. Backup Finished Event (Success)
        plugin._on_backup_finished(
            {
                "success": True,
                "filename": "backup-2026.zip",
                "size_mb": 12.5,
                "media_count": 50,
            }
        )
        assert mock_send.call_count == 2
        assert "Backup Created Successfully" in mock_send.call_args[1]["title"]

        # 4. Backup Finished Event (Failure)
        plugin._on_backup_finished(
            {
                "success": False,
                "filename": "backup-failed.zip",
                "error": "Disk full",
            }
        )
        assert mock_send.call_count == 3
        assert "Backup Creation Failed" in mock_send.call_args[1]["title"]

        # 5. Security Lockout Event
        plugin._on_security_lockout(
            {
                "ip": "192.168.1.50",
                "attempts": 5,
                "username": "admin",
            }
        )
        assert mock_send.call_count == 4
        assert "Security Alert" in mock_send.call_args[1]["title"]

        # 6. Job Failed Event
        plugin._on_job_failed(
            {
                "job_id": "job-123",
                "job_type": "transcode",
                "error": "Codec error",
            }
        )
        assert mock_send.call_count == 5
        assert "Background Task Failed" in mock_send.call_args[1]["title"]

    plugin.stop()


def test_api_notification_endpoints(client, app: Flask):
    """Verify REST API notification test and status endpoints."""
    # Status endpoint
    res = client.get("/api/notifications/status")
    assert res.status_code == 200
    data = res.get_json()
    assert "enabled" in data
    assert "apprise_installed" in data
    assert "endpoints_count" in data

    # Test endpoint with mocked dispatch
    with patch(
        "aarkib.services.notifier.NotificationService.send_test",
        return_value=(True, "Dispatched to 1 endpoint", 1),
    ):
        test_res = client.post(
            "/api/notifications/test",
            json={"urls": "discord://test/token"},
        )
        assert test_res.status_code == 200
        test_data = test_res.get_json()
        assert test_data["success"] is True
        assert test_data["servers_notified"] == 1


def test_api_notification_unauthorized(unauth_client):
    """Verify non-admin / unauthenticated requests cannot trigger test notifications."""
    res = unauth_client.post(
        "/api/notifications/test",
        json={"urls": "discord://test/token"},
    )
    assert res.status_code in (401, 403, 302)


def test_settings_secret_masking(app: Flask):
    """Verify NOTIFICATION_URLS is properly masked as a secret in effective settings."""
    with app.app_context():
        update_settings(
            app,
            {
                "ENABLE_NOTIFICATIONS": True,
                "NOTIFICATION_URLS": "discord://12345/supersecrettoken",
            },
        )
        effective = get_effective_settings(app)
        urls_info = effective["settings"]["NOTIFICATION_URLS"]

        assert urls_info["is_secret"] is True
        assert urls_info["is_set"] is True
        assert "supersecrettoken" not in urls_info["value"]
        assert urls_info["value"].startswith("••••")

        # Submitting masked value should preserve underlying secret
        update_settings(
            app,
            {
                "NOTIFICATION_URLS": "••••••••oken",
            },
        )
        assert app.config["NOTIFICATION_URLS"] == "discord://12345/supersecrettoken"


def test_security_lockout_trigger(app: Flask):
    """Verify that repeated failed attempts on AuthRateLimiter fire EVENT_SECURITY_LOCKOUT."""
    app.config["AUTH_RATE_LIMIT_ENABLED"] = True
    app.config["AUTH_RATE_LIMIT_MAX_ATTEMPTS"] = 3
    limiter = AuthRateLimiter(max_attempts=3, window_seconds=60)
    test_ip = "203.0.113.195"

    received_events: list[dict] = []

    def _handler(payload):
        received_events.append(payload)

    event_bus.subscribe(EVENT_SECURITY_LOCKOUT, _handler)
    try:
        limiter.record_failure(test_ip, username="testuser")
        assert len(received_events) == 0

        limiter.record_failure(test_ip, username="testuser")
        assert len(received_events) == 0

        # Third failure reaches max_attempts (3)
        limiter.record_failure(test_ip, username="testuser")
        assert len(received_events) == 1
        assert received_events[0]["ip"] == test_ip
        assert received_events[0]["attempts"] == 3
        assert received_events[0]["username"] == "testuser"
    finally:
        event_bus.unsubscribe(EVENT_SECURITY_LOCKOUT, _handler)
