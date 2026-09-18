from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from aarkib.plugins import plugin_registry
from aarkib.plugins.mqtt import MqttPlugin
from aarkib.services.events import (
    EVENT_BACKUP_FINISHED,
    EVENT_BACKUP_STARTED,
    EVENT_MEDIA_ADDED,
    EVENT_PLAYBACK_UPDATED,
    EVENT_SCAN_PROGRESS,
    EVENT_SCAN_STARTED,
    event_bus,
)


def test_mqtt_plugin_registered(app):
    """Verify MqttPlugin is registered and discoverable in plugin_registry."""
    plugin = plugin_registry.get_plugin("mqtt")
    assert plugin is not None
    assert isinstance(plugin, MqttPlugin)
    assert plugin.name == "mqtt"
    assert plugin.plugin_type == "integration"
    assert "ENABLE_MQTT" in plugin.config_keys
    assert "MQTT_BROKER_HOST" in plugin.config_keys


def test_mqtt_plugin_health_check(app):
    """Verify health check returns valid structure and fields."""
    plugin = MqttPlugin()
    plugin._app = app
    health = plugin.check_health()
    assert health["plugin"] == "mqtt"
    assert health["type"] == "integration"
    assert "paho_installed" in health
    assert "connected" in health
    assert health["broker"] == app.config.get("MQTT_BROKER_HOST", "localhost")


def test_mqtt_discovery_payloads_structure(app):
    """Verify that Home Assistant discovery payloads adhere to HA MQTT specifications."""
    plugin = MqttPlugin()
    plugin._app = app
    mock_client = MagicMock()

    plugin._publish_discovery_payloads(
        mock_client, prefix="aarkib", discovery_prefix="homeassistant"
    )

    published_topics = [call[0][0] for call in mock_client.publish.call_args_list]
    published_payloads = {
        call[0][0]: json.loads(call[0][1])
        for call in mock_client.publish.call_args_list
    }

    # Verify discovery topics were published
    assert any(
        "homeassistant/binary_sensor/aarkib/status/config" in t
        for t in published_topics
    )
    assert any(
        "homeassistant/sensor/aarkib/total_media/config" in t for t in published_topics
    )
    assert any(
        "homeassistant/button/aarkib/scan_libraries/config" in t
        for t in published_topics
    )
    assert any(
        "homeassistant/switch/aarkib/realtime_watcher/config" in t
        for t in published_topics
    )
    assert any(
        "homeassistant/media_player/aarkib/server/config" in t for t in published_topics
    )

    # Check status binary sensor schema
    status_doc = published_payloads["homeassistant/binary_sensor/aarkib/status/config"]
    assert status_doc["name"] == "Server Status"
    assert status_doc["unique_id"] == "aarkib_status"
    assert status_doc["device_class"] == "connectivity"
    assert status_doc["state_topic"] == "aarkib/status"
    assert "device" in status_doc
    assert status_doc["device"]["identifiers"] == ["aarkib_server"]

    # Check total media sensor schema
    media_doc = published_payloads["homeassistant/sensor/aarkib/total_media/config"]
    assert media_doc["name"] == "Total Media Items"
    assert media_doc["unique_id"] == "aarkib_total_media"
    assert media_doc["unit_of_measurement"] == "items"
    assert media_doc["state_topic"] == "aarkib/metrics/counts"
    assert media_doc["value_template"] == "{{ value_json.total }}"

    # Check scan button schema
    btn_doc = published_payloads["homeassistant/button/aarkib/scan_libraries/config"]
    assert btn_doc["command_topic"] == "aarkib/command/scan"
    assert btn_doc["payload_press"] == "PRESS"

    # Check switch schema
    switch_doc = published_payloads[
        "homeassistant/switch/aarkib/realtime_watcher/config"
    ]
    assert switch_doc["state_topic"] == "aarkib/switch/watcher/state"
    assert switch_doc["command_topic"] == "aarkib/switch/watcher/set"
    assert switch_doc["payload_on"] == "ON"
    assert switch_doc["payload_off"] == "OFF"

    # Check media player schema
    player_doc = published_payloads["homeassistant/media_player/aarkib/server/config"]
    assert player_doc["state_topic"] == "aarkib/media_player/state"
    assert player_doc["command_topic"] == "aarkib/media_player/command"
    assert player_doc["json_attributes_topic"] == "aarkib/media_player/attributes"


def test_mqtt_connect_callback_flow(app):
    """Verify that _on_mqtt_connect publishes birth message and subscribes to command topics."""
    plugin = MqttPlugin()
    plugin._app = app
    mock_client = MagicMock()

    plugin._on_mqtt_connect(mock_client, None, None, 0)

    assert plugin._connected is True

    # Check subscription to command topics
    subscribed = [call[0][0] for call in mock_client.subscribe.call_args_list]
    assert "aarkib/command/#" in subscribed
    assert "aarkib/switch/#" in subscribed
    assert "aarkib/media_player/command" in subscribed

    # Check birth message
    published = [
        (call[0][0], call[0][1]) for call in mock_client.publish.call_args_list
    ]
    assert ("aarkib/status", "online") in published


def test_mqtt_inbound_command_handling(app):
    """Verify that inbound MQTT commands are routed and executed cleanly."""
    plugin = MqttPlugin()
    plugin._app = app

    # 1. Test scan command triggers job_manager
    with patch("aarkib.services.job_manager.job_manager.submit_job") as mock_submit:
        msg = MagicMock()
        msg.topic = "aarkib/command/scan"
        msg.payload = b"PRESS"
        plugin._on_mqtt_message(None, None, msg)
        assert mock_submit.called

    # 2. Test clean cache command calls clean_stale_directories
    with patch(
        "aarkib.services.transcoder.transcode_supervisor.clean_stale_directories"
    ) as mock_clean:
        msg = MagicMock()
        msg.topic = "aarkib/command/clean_cache"
        msg.payload = b"PRESS"
        plugin._on_mqtt_message(None, None, msg)
        assert mock_clean.called

    # 3. Test switch command toggles setting
    with patch("aarkib.services.settings_service.update_settings") as mock_set:
        msg = MagicMock()
        msg.topic = "aarkib/switch/watcher/set"
        msg.payload = b"OFF"
        plugin._on_mqtt_message(None, None, msg)
        mock_set.assert_called_once_with(app, {"WATCH_LIBRARY": False})


def test_mqtt_event_bus_publishing(app):
    """Verify that internal events are captured and enqueued for MQTT publication."""
    plugin = MqttPlugin()
    plugin._app = app
    plugin.start()

    try:
        # 1. Scan started
        event_bus.emit(EVENT_SCAN_STARTED, {"library_id": "test"})
        topic, payload, qos, retain = plugin._publish_queue.get_nowait()
        assert topic == "aarkib/scanner/state"
        assert payload == "scanning"

        # 2. Scan progress
        event_bus.emit(EVENT_SCAN_PROGRESS, {"percentage": 50, "file": "book.epub"})
        topic, payload, qos, retain = plugin._publish_queue.get_nowait()
        assert topic == "aarkib/scanner/progress"
        assert "50" in payload

        # 3. Media added
        event_bus.emit(
            EVENT_MEDIA_ADDED, {"id": 1, "title": "Test Book", "media_type": "book"}
        )
        topic, payload, qos, retain = plugin._publish_queue.get_nowait()
        assert topic == "aarkib/sensor/last_added"
        assert "Test Book" in payload

        # 4. Backup started and finished
        event_bus.emit(EVENT_BACKUP_STARTED, {"archive_path": "test.zip"})
        topic, payload, qos, retain = plugin._publish_queue.get_nowait()
        assert topic == "aarkib/backup/state"
        assert payload == "backing_up"

        event_bus.emit(
            EVENT_BACKUP_FINISHED,
            {"archive_path": "test.zip", "size_mb": 12.3, "filename": "test.zip"},
        )
        topic, payload, qos, retain = plugin._publish_queue.get_nowait()
        assert topic == "aarkib/backup/state"
        assert payload == "idle"

        topic_lat, payload_lat, _, _ = plugin._publish_queue.get_nowait()
        assert topic_lat == "aarkib/backup/latest"
        assert "12.3" in payload_lat

        # 5. Playback updated
        event_bus.emit(
            EVENT_PLAYBACK_UPDATED,
            {
                "media_id": 99,
                "title": "Movie Night",
                "media_type": "video",
                "percentage": 25.0,
                "is_completed": False,
            },
        )
        topic, payload, qos, retain = plugin._publish_queue.get_nowait()
        assert topic == "aarkib/media_player/state"
        assert payload == "playing"

        topic_attr, payload_attr, _, _ = plugin._publish_queue.get_nowait()
        assert topic_attr == "aarkib/media_player/attributes"
        assert "Movie Night" in payload_attr

    finally:
        plugin.stop()
