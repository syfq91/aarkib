from __future__ import annotations

import time
from unittest.mock import MagicMock

from watchdog.events import FileSystemEvent

from aarkib.services.watcher import (
    DebouncedLibraryChangeHandler,
    start_library_watcher,
    stop_library_watcher,
)


def test_debounced_library_change_handler_debounce(app, tmp_path):
    # Test that rapid events within debounce window trigger only once
    lib_dir = tmp_path / "media"
    lib_dir.mkdir(parents=True, exist_ok=True)
    target = lib_dir / "test.epub"
    target.write_text("dummy")

    handler = DebouncedLibraryChangeHandler(app, debounce_seconds=0.1)
    mock_trigger = MagicMock()
    handler._trigger_index = mock_trigger

    event = FileSystemEvent(str(target))
    event.is_directory = False

    # Rapid calls
    handler.on_created(event)
    handler.on_modified(event)
    handler.on_modified(event)

    # Trigger should not have run yet
    assert mock_trigger.call_count == 0

    # Wait for debounce window
    time.sleep(0.25)
    assert mock_trigger.call_count == 1
    mock_trigger.assert_called_once_with(str(target))


def test_debounced_library_change_handler_cancel_all(app, tmp_path):
    target = tmp_path / "test2.epub"
    target.write_text("dummy")

    handler = DebouncedLibraryChangeHandler(app, debounce_seconds=1.0)
    mock_trigger = MagicMock()
    handler._trigger_index = mock_trigger

    event = FileSystemEvent(str(target))
    event.is_directory = False
    handler.on_created(event)

    assert len(handler._timers) == 1
    handler.cancel_all()
    assert len(handler._timers) == 0

    time.sleep(0.1)
    assert mock_trigger.call_count == 0


def test_start_and_stop_library_watcher(app, tmp_path):
    app.config["WATCH_LIBRARY"] = True
    app.config["MEDIA_DIR"] = str(tmp_path / "watch_test")
    app.config["COVERS_DIR"] = str(tmp_path / "covers")

    observer = start_library_watcher(app)
    assert observer is not None
    assert observer.is_alive()

    stop_library_watcher(app)
    assert not observer.is_alive()
