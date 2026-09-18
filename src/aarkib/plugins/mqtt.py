"""MQTT & Home Assistant Integration Plugin for Aarkib."""

from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from aarkib.extensions import db
from aarkib.models import MediaItem
from aarkib.plugins.base import BasePlugin
from aarkib.services.events import (
    EVENT_BACKUP_FINISHED,
    EVENT_BACKUP_STARTED,
    EVENT_MEDIA_ADDED,
    EVENT_PLAYBACK_UPDATED,
    EVENT_SCAN_FINISHED,
    EVENT_SCAN_PROGRESS,
    EVENT_SCAN_STARTED,
    event_bus,
)

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger("aarkib.mqtt")

try:
    import paho.mqtt.client as mqtt

    PAHO_AVAILABLE = True
except ImportError:
    mqtt = None  # type: ignore[assignment]
    PAHO_AVAILABLE = False


class MqttPlugin(BasePlugin):
    """Integrates Aarkib with Home Assistant and external MQTT brokers."""

    name = "mqtt"
    display_name = "MQTT & Home Assistant"
    plugin_type = "integration"
    description = "Home Assistant Auto-Discovery, real-time media player states, and MQTT command interface"
    csrf_exempt = True
    config_keys = [
        "ENABLE_MQTT",
        "MQTT_BROKER_HOST",
        "MQTT_BROKER_PORT",
        "MQTT_USERNAME",
        "MQTT_PASSWORD",
        "MQTT_TOPIC_PREFIX",
        "MQTT_DISCOVERY_PREFIX",
        "MQTT_CLIENT_ID",
        "MQTT_TLS_ENABLED",
    ]

    def __init__(self) -> None:
        self.enabled: bool = False
        self._app: Flask | None = None
        self._client: Any = None
        self._connected: bool = False
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._publish_queue: queue.Queue[tuple[str, str, int, bool]] = queue.Queue(
            maxsize=2000
        )
        self._lock = threading.Lock()
        self._last_playback_state: str = "idle"
        self._last_progress_time: float = 0.0

    def init_app(self, app: Flask) -> None:
        """Lifecycle hook invoked when Flask initializes."""
        self._app = app
        is_enabled = app.config.get("ENABLE_MQTT", False)
        if isinstance(is_enabled, str):
            is_enabled = is_enabled.lower() in ("true", "1", "yes", "on")

        self.enabled = bool(is_enabled)
        if not self.enabled or not PAHO_AVAILABLE:
            return

        self.start()
        atexit.register(self.stop)

    def start(self) -> None:
        """Initializes and connects the MQTT client in a background thread."""
        with self._lock:
            if self._worker_thread and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._run_mqtt_loop,
                name="AarkibMqttWorker",
                daemon=True,
            )
            self._worker_thread.start()

        # Subscribe to internal event bus
        event_bus.subscribe(EVENT_SCAN_STARTED, self._on_scan_started)
        event_bus.subscribe(EVENT_SCAN_PROGRESS, self._on_scan_progress)
        event_bus.subscribe(EVENT_SCAN_FINISHED, self._on_scan_finished)
        event_bus.subscribe(EVENT_MEDIA_ADDED, self._on_media_added)
        event_bus.subscribe(EVENT_BACKUP_STARTED, self._on_backup_started)
        event_bus.subscribe(EVENT_BACKUP_FINISHED, self._on_backup_finished)
        event_bus.subscribe(EVENT_PLAYBACK_UPDATED, self._on_playback_updated)

    def stop(self) -> None:
        """Gracefully disconnects the MQTT client and halts worker threads."""
        self._stop_event.set()

        # Unsubscribe from internal events
        event_bus.unsubscribe(EVENT_SCAN_STARTED, self._on_scan_started)
        event_bus.unsubscribe(EVENT_SCAN_PROGRESS, self._on_scan_progress)
        event_bus.unsubscribe(EVENT_SCAN_FINISHED, self._on_scan_finished)
        event_bus.unsubscribe(EVENT_MEDIA_ADDED, self._on_media_added)
        event_bus.unsubscribe(EVENT_BACKUP_STARTED, self._on_backup_started)
        event_bus.unsubscribe(EVENT_BACKUP_FINISHED, self._on_backup_finished)
        event_bus.unsubscribe(EVENT_PLAYBACK_UPDATED, self._on_playback_updated)

        with self._lock:
            client = self._client
            if client:
                try:
                    prefix = (
                        self._app.config.get("MQTT_TOPIC_PREFIX", "aarkib")
                        if self._app
                        else "aarkib"
                    )
                    client.publish(f"{prefix}/status", "offline", qos=1, retain=True)
                    client.loop_stop()
                    client.disconnect()
                except Exception as exc:
                    logger.debug("Error stopping MQTT client: %s", exc)
                self._client = None
                self._connected = False

    def check_health(self) -> dict[str, Any]:
        """Performs health check for the MQTT plugin."""
        health = super().check_health()
        health.update(
            {
                "paho_installed": PAHO_AVAILABLE,
                "connected": self._connected,
                "queue_size": self._publish_queue.qsize(),
                "broker": (
                    self._app.config.get("MQTT_BROKER_HOST", "localhost")
                    if self._app
                    else None
                ),
            }
        )
        return health

    # -------------------------------------------------------------------------
    # Internal Client Loop & Callbacks
    # -------------------------------------------------------------------------

    def _run_mqtt_loop(self) -> None:
        """Background thread running the paho-mqtt network loop and publisher queue."""
        if not self._app or not PAHO_AVAILABLE:
            return

        app = self._app
        host = app.config.get("MQTT_BROKER_HOST", "localhost")
        port = int(app.config.get("MQTT_BROKER_PORT", 1883))
        username = app.config.get("MQTT_USERNAME", "")
        password = app.config.get("MQTT_PASSWORD", "")
        client_id = app.config.get("MQTT_CLIENT_ID", "aarkib_server")
        tls_enabled = bool(app.config.get("MQTT_TLS_ENABLED", False))
        prefix = app.config.get("MQTT_TOPIC_PREFIX", "aarkib")

        try:
            # Support both paho-mqtt v1.x and v2.x CallbackAPIVersion
            if hasattr(mqtt, "CallbackAPIVersion"):
                client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
                    client_id=client_id,
                )
            else:
                client = mqtt.Client(client_id=client_id)  # type: ignore[call-arg]

            if username:
                client.username_pw_set(username, password=password or None)
            if tls_enabled:
                client.tls_set()

            # Set Last Will and Testament (LWT)
            client.will_set(f"{prefix}/status", "offline", qos=1, retain=True)

            client.on_connect = self._on_mqtt_connect
            client.on_disconnect = self._on_mqtt_disconnect
            client.on_message = self._on_mqtt_message

            with self._lock:
                self._client = client

            logger.info("Connecting to MQTT broker at %s:%d...", host, port)
            client.connect_async(host, port, keepalive=60)
            client.loop_start()

            # Queue processing loop
            while not self._stop_event.is_set():
                try:
                    topic, payload, qos, retain = self._publish_queue.get(timeout=0.5)
                    if self._connected:
                        client.publish(topic, payload, qos=qos, retain=retain)
                    self._publish_queue.task_done()
                except queue.Empty:
                    continue

        except Exception as exc:
            logger.error("Failed running MQTT client loop: %s", exc)
            self._connected = False

    def _on_mqtt_connect(
        self, client: Any, userdata: Any, flags: Any, rc: Any, *args: Any
    ) -> None:
        """Callback triggered on broker connection establishment."""
        rc_code = getattr(rc, "value", rc)
        if rc_code != 0:
            logger.warning("MQTT connection failed with return code: %s", rc_code)
            self._connected = False
            return

        self._connected = True
        logger.info("Connected successfully to MQTT broker.")

        if not self._app:
            return

        prefix = self._app.config.get("MQTT_TOPIC_PREFIX", "aarkib")
        discovery_prefix = self._app.config.get(
            "MQTT_DISCOVERY_PREFIX", "homeassistant"
        )

        # 1. Publish birth message
        client.publish(f"{prefix}/status", "online", qos=1, retain=True)

        # 2. Subscribe to command topics
        client.subscribe(f"{prefix}/command/#")
        client.subscribe(f"{prefix}/switch/#")
        client.subscribe(f"{prefix}/media_player/command")

        # 3. Publish Home Assistant Auto-Discovery payloads
        self._publish_discovery_payloads(client, prefix, discovery_prefix)

        # 4. Publish initial system metrics and switches
        self._publish_initial_state(client, prefix)

    def _on_mqtt_disconnect(
        self, client: Any, userdata: Any, rc: Any, *args: Any
    ) -> None:
        """Callback triggered upon disconnection."""
        self._connected = False
        logger.info("Disconnected from MQTT broker (code: %s)", rc)

    def _on_mqtt_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """Dispatches incoming command topic messages."""
        if not self._app:
            return

        topic = msg.topic
        payload_str = msg.payload.decode("utf-8", errors="replace").strip()
        logger.debug("Received MQTT command on %s: %s", topic, payload_str)

        prefix = self._app.config.get("MQTT_TOPIC_PREFIX", "aarkib")

        # Command routing
        if topic == f"{prefix}/command/scan":
            self._handle_command_scan()
        elif topic == f"{prefix}/command/backup":
            self._handle_command_backup()
        elif topic == f"{prefix}/command/clean_cache":
            self._handle_command_clean_cache()
        elif topic.startswith(f"{prefix}/switch/"):
            self._handle_switch_command(topic, payload_str, prefix)
        elif topic == f"{prefix}/media_player/command":
            self._handle_media_player_command(payload_str)

    # -------------------------------------------------------------------------
    # Inbound Command Handlers
    # -------------------------------------------------------------------------

    def _handle_command_scan(self) -> None:
        """Executes an asynchronous library crawl via JobManager."""
        if not self._app:
            return
        try:
            from aarkib.services.job_manager import job_manager
            from aarkib.services.scanner import scan_library

            job_manager.submit_job("library_scan", scan_library, self._app)
            logger.info("MQTT triggered library scan job successfully.")
        except Exception as exc:
            logger.error("Failed triggering scan from MQTT: %s", exc)

    def _handle_command_backup(self) -> None:
        """Executes hot SQLite backup snapshot in background thread."""
        if not self._app:
            return
        app = self._app

        def _do_backup() -> None:
            try:
                from aarkib.services.backup import create_backup

                create_backup(app)
                logger.info("MQTT triggered backup successfully.")
            except Exception as exc:
                logger.error("Failed executing backup from MQTT: %s", exc)

        threading.Thread(target=_do_backup, daemon=True).start()

    def _handle_command_clean_cache(self) -> None:
        """Sweeps stale HLS transcode sessions and orphan files."""
        if not self._app:
            return
        try:
            from aarkib.services.transcoder import transcode_supervisor

            base_dir = Path(
                self._app.config.get("TRANSCODE_DIR", "data/transcode")
            ).resolve()
            transcode_supervisor.clean_stale_directories(base_dir)
            logger.info("MQTT cleaned transcode cache successfully.")
        except Exception as exc:
            logger.error("Failed cleaning transcode cache from MQTT: %s", exc)

    def _handle_switch_command(
        self, topic: str, payload: str, topic_prefix: str
    ) -> None:
        """Updates persistent dynamic settings from Home Assistant switch toggles."""
        if not self._app:
            return

        is_on = payload.upper() in ("ON", "TRUE", "1")
        setting_key_map = {
            f"{topic_prefix}/switch/watcher/set": (
                "WATCH_LIBRARY",
                f"{topic_prefix}/switch/watcher/state",
            ),
            f"{topic_prefix}/switch/enrich/set": (
                "AUTO_ENRICH",
                f"{topic_prefix}/switch/enrich/state",
            ),
            f"{topic_prefix}/switch/optimizer/set": (
                "ENABLE_EINK_OPTIMIZER",
                f"{topic_prefix}/switch/optimizer/state",
            ),
        }

        entry = setting_key_map.get(topic)
        if not entry:
            return

        setting_key, state_topic = entry
        with self._app.app_context():
            from aarkib.services.settings_service import update_settings

            update_settings(self._app, {setting_key: is_on})
            self._enqueue_publish(state_topic, "ON" if is_on else "OFF", retain=True)
            logger.info("MQTT switch updated %s to %s", setting_key, is_on)

    def _handle_media_player_command(self, payload_str: str) -> None:
        """Parses and logs media player command."""
        try:
            data = json.loads(payload_str)
            cmd = data.get("command")
            logger.info("Received media_player command: %s (payload: %s)", cmd, data)
        except Exception as exc:
            logger.debug("Failed parsing media_player command payload: %s", exc)

    # -------------------------------------------------------------------------
    # Home Assistant Discovery & State Payloads
    # -------------------------------------------------------------------------

    def _publish_discovery_payloads(
        self, client: Any, prefix: str, discovery_prefix: str
    ) -> None:
        """Publishes Home Assistant MQTT Auto-Discovery payloads."""
        device = {
            "identifiers": ["aarkib_server"],
            "name": "Aarkib Media Server",
            "manufacturer": "Aarkib",
            "model": "Media & Book Server",
            "sw_version": "0.1.0",
        }
        availability = {
            "availability_topic": f"{prefix}/status",
            "payload_available": "online",
            "payload_not_available": "offline",
        }

        # 1. Binary Sensors
        binary_sensors = [
            ("status", "Server Status", "connectivity", f"{prefix}/status"),
            (
                "scanner_active",
                "Library Scanner",
                "running",
                f"{prefix}/scanner/state",
                "scanning",
                "idle",
            ),
            (
                "backup_active",
                "Database Backup",
                "running",
                f"{prefix}/backup/state",
                "backing_up",
                "idle",
            ),
        ]
        for item in binary_sensors:
            obj_id, name, dev_class, state_topic = item[:4]
            payload: dict[str, Any] = {
                "name": name,
                "unique_id": f"aarkib_{obj_id}",
                "device_class": dev_class,
                "state_topic": state_topic,
                "device": device,
                **availability,
            }
            if len(item) > 4:
                payload["payload_on"] = item[4]
                payload["payload_off"] = item[5]

            client.publish(
                f"{discovery_prefix}/binary_sensor/aarkib/{obj_id}/config",
                json.dumps(payload),
                qos=1,
                retain=True,
            )

        # 2. Metric Sensors
        sensors = [
            (
                "total_media",
                "Total Media Items",
                "items",
                "mdi:bookshelf",
                f"{prefix}/metrics/counts",
                "{{ value_json.total }}",
            ),
            (
                "books_count",
                "Books Count",
                "books",
                "mdi:book-open-page-variant",
                f"{prefix}/metrics/counts",
                "{{ value_json.books }}",
            ),
            (
                "comics_count",
                "Comics Count",
                "comics",
                "mdi:book-open-variant",
                f"{prefix}/metrics/counts",
                "{{ value_json.comics }}",
            ),
            (
                "audio_count",
                "Audio Tracks",
                "tracks",
                "mdi:music",
                f"{prefix}/metrics/counts",
                "{{ value_json.audio }}",
            ),
            (
                "video_count",
                "Videos Count",
                "videos",
                "mdi:movie",
                f"{prefix}/metrics/counts",
                "{{ value_json.video }}",
            ),
            (
                "podcasts_count",
                "Podcasts Count",
                "episodes",
                "mdi:podcast",
                f"{prefix}/metrics/counts",
                "{{ value_json.podcasts }}",
            ),
            (
                "storage_size",
                "Storage Size",
                "GB",
                "mdi:harddisk",
                f"{prefix}/metrics/storage",
                "{{ value_json.total_gb }}",
            ),
        ]
        for obj_id, name, unit, icon, state_topic, tmpl in sensors:
            payload = {
                "name": name,
                "unique_id": f"aarkib_{obj_id}",
                "unit_of_measurement": unit,
                "icon": icon,
                "state_topic": state_topic,
                "value_template": tmpl,
                "device": device,
                **availability,
            }
            client.publish(
                f"{discovery_prefix}/sensor/aarkib/{obj_id}/config",
                json.dumps(payload),
                qos=1,
                retain=True,
            )

        # 3. Action Buttons
        buttons = [
            (
                "scan_libraries",
                "Scan Media Libraries",
                "mdi:folder-sync",
                f"{prefix}/command/scan",
            ),
            (
                "create_backup",
                "Create Backup",
                "mdi:database-export",
                f"{prefix}/command/backup",
            ),
            (
                "clean_cache",
                "Clean Transcode Cache",
                "mdi:broom",
                f"{prefix}/command/clean_cache",
            ),
        ]
        for obj_id, name, icon, cmd_topic in buttons:
            payload = {
                "name": name,
                "unique_id": f"aarkib_button_{obj_id}",
                "icon": icon,
                "command_topic": cmd_topic,
                "payload_press": "PRESS",
                "device": device,
                **availability,
            }
            client.publish(
                f"{discovery_prefix}/button/aarkib/{obj_id}/config",
                json.dumps(payload),
                qos=1,
                retain=True,
            )

        # 4. Configuration Switches
        switches = [
            (
                "realtime_watcher",
                "Real-Time Folder Watcher",
                "mdi:eye",
                f"{prefix}/switch/watcher/state",
                f"{prefix}/switch/watcher/set",
            ),
            (
                "auto_enrichment",
                "Auto Metadata Enrichment",
                "mdi:auto-fix",
                f"{prefix}/switch/enrich/state",
                f"{prefix}/switch/enrich/set",
            ),
            (
                "eink_optimizer",
                "E-Ink Device Optimizer",
                "mdi:book-check",
                f"{prefix}/switch/optimizer/state",
                f"{prefix}/switch/optimizer/set",
            ),
        ]
        for obj_id, name, icon, state_t, cmd_t in switches:
            payload = {
                "name": name,
                "unique_id": f"aarkib_switch_{obj_id}",
                "icon": icon,
                "state_topic": state_t,
                "command_topic": cmd_t,
                "payload_on": "ON",
                "payload_off": "OFF",
                "device": device,
                **availability,
            }
            client.publish(
                f"{discovery_prefix}/switch/aarkib/{obj_id}/config",
                json.dumps(payload),
                qos=1,
                retain=True,
            )

        # 5. Media Player Entity
        player_payload = {
            "name": "Aarkib Media Player",
            "unique_id": "aarkib_media_player_server",
            "state_topic": f"{prefix}/media_player/state",
            "json_attributes_topic": f"{prefix}/media_player/attributes",
            "command_topic": f"{prefix}/media_player/command",
            "device": device,
            **availability,
        }
        client.publish(
            f"{discovery_prefix}/media_player/aarkib/server/config",
            json.dumps(player_payload),
            qos=1,
            retain=True,
        )

    def _publish_initial_state(self, client: Any, prefix: str) -> None:
        """Queries database for counts and publishes initial state messages."""
        if not self._app:
            return

        with self._app.app_context():
            # Initial states
            client.publish(f"{prefix}/scanner/state", "idle", qos=1, retain=True)
            client.publish(f"{prefix}/backup/state", "idle", qos=1, retain=True)
            client.publish(f"{prefix}/media_player/state", "idle", qos=1, retain=True)

            # Switches state
            watcher_on = self._app.config.get("WATCH_LIBRARY", True)
            enrich_on = self._app.config.get("AUTO_ENRICH", False)
            optimizer_on = self._app.config.get("ENABLE_EINK_OPTIMIZER", True)

            client.publish(
                f"{prefix}/switch/watcher/state",
                "ON" if watcher_on else "OFF",
                qos=1,
                retain=True,
            )
            client.publish(
                f"{prefix}/switch/enrich/state",
                "ON" if enrich_on else "OFF",
                qos=1,
                retain=True,
            )
            client.publish(
                f"{prefix}/switch/optimizer/state",
                "ON" if optimizer_on else "OFF",
                qos=1,
                retain=True,
            )

            # Metrics
            self._refresh_library_metrics(client, prefix)

    def _refresh_library_metrics(self, client: Any, prefix: str) -> None:
        """Computes and publishes media item count and storage metrics."""
        try:
            total = db.session.scalar(select(func.count(MediaItem.id))) or 0
            books = (
                db.session.scalar(
                    select(func.count(MediaItem.id)).where(
                        MediaItem.media_type.in_(("book", "document"))
                    )
                )
                or 0
            )
            comics = (
                db.session.scalar(
                    select(func.count(MediaItem.id)).where(
                        MediaItem.media_type == "comic"
                    )
                )
                or 0
            )
            audio = (
                db.session.scalar(
                    select(func.count(MediaItem.id)).where(
                        MediaItem.media_type.in_(("audio", "audiobook", "music"))
                    )
                )
                or 0
            )
            video = (
                db.session.scalar(
                    select(func.count(MediaItem.id)).where(
                        MediaItem.media_type == "video"
                    )
                )
                or 0
            )
            podcasts = (
                db.session.scalar(
                    select(func.count(MediaItem.id)).where(
                        MediaItem.media_type == "podcast"
                    )
                )
                or 0
            )

            total_bytes = db.session.scalar(select(func.sum(MediaItem.file_size))) or 0
            total_gb = round(total_bytes / (1024**3), 2)

            counts_payload = {
                "total": total,
                "books": books,
                "comics": comics,
                "audio": audio,
                "video": video,
                "podcasts": podcasts,
            }
            client.publish(
                f"{prefix}/metrics/counts",
                json.dumps(counts_payload),
                qos=1,
                retain=True,
            )

            storage_payload = {
                "total_bytes": total_bytes,
                "total_gb": total_gb,
            }
            client.publish(
                f"{prefix}/metrics/storage",
                json.dumps(storage_payload),
                qos=1,
                retain=True,
            )
        except Exception as exc:
            logger.debug("Failed computing library metrics for MQTT: %s", exc)

    def _enqueue_publish(
        self, topic: str, payload: str, qos: int = 1, retain: bool = False
    ) -> None:
        """Enqueues an outbound MQTT message safely without holding thread locks."""
        try:
            self._publish_queue.put_nowait((topic, payload, qos, retain))
        except queue.Full:
            logger.warning("MQTT publish queue full, message dropped: %s", topic)

    # -------------------------------------------------------------------------
    # Internal Event Bus Callbacks
    # -------------------------------------------------------------------------

    def _on_scan_started(self, payload: Any) -> None:
        prefix = (
            self._app.config.get("MQTT_TOPIC_PREFIX", "aarkib")
            if self._app
            else "aarkib"
        )
        self._enqueue_publish(f"{prefix}/scanner/state", "scanning", retain=True)

    def _on_scan_progress(self, payload: Any) -> None:
        prefix = (
            self._app.config.get("MQTT_TOPIC_PREFIX", "aarkib")
            if self._app
            else "aarkib"
        )
        if isinstance(payload, dict):
            self._enqueue_publish(
                f"{prefix}/scanner/progress", json.dumps(payload), retain=False
            )

    def _on_scan_finished(self, payload: Any) -> None:
        prefix = (
            self._app.config.get("MQTT_TOPIC_PREFIX", "aarkib")
            if self._app
            else "aarkib"
        )
        self._enqueue_publish(f"{prefix}/scanner/state", "idle", retain=True)
        if self._client and self._connected:
            self._refresh_library_metrics(self._client, prefix)

    def _on_media_added(self, payload: Any) -> None:
        prefix = (
            self._app.config.get("MQTT_TOPIC_PREFIX", "aarkib")
            if self._app
            else "aarkib"
        )
        if isinstance(payload, dict):
            self._enqueue_publish(
                f"{prefix}/sensor/last_added", json.dumps(payload), retain=True
            )

    def _on_backup_started(self, payload: Any) -> None:
        prefix = (
            self._app.config.get("MQTT_TOPIC_PREFIX", "aarkib")
            if self._app
            else "aarkib"
        )
        self._enqueue_publish(f"{prefix}/backup/state", "backing_up", retain=True)

    def _on_backup_finished(self, payload: Any) -> None:
        prefix = (
            self._app.config.get("MQTT_TOPIC_PREFIX", "aarkib")
            if self._app
            else "aarkib"
        )
        self._enqueue_publish(f"{prefix}/backup/state", "idle", retain=True)
        if isinstance(payload, dict):
            self._enqueue_publish(
                f"{prefix}/backup/latest", json.dumps(payload), retain=True
            )

    def _on_playback_updated(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return

        prefix = (
            self._app.config.get("MQTT_TOPIC_PREFIX", "aarkib")
            if self._app
            else "aarkib"
        )
        now = time.time()
        # Debounce rapid playback position updates to once every 2 seconds
        if now - self._last_progress_time < 2.0 and not payload.get("is_completed"):
            return
        self._last_progress_time = now

        is_completed = payload.get("is_completed", False)
        state = "idle" if is_completed else "playing"

        self._enqueue_publish(f"{prefix}/media_player/state", state, retain=True)
        self._enqueue_publish(
            f"{prefix}/media_player/attributes", json.dumps(payload), retain=True
        )


__all__ = ["MqttPlugin"]
