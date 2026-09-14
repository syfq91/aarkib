"""Tests for structured JSON logging and observability formatting."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Flask

from aarkib import JsonLogFormatter, configure_logging, create_app
from aarkib.config import TestConfig


def test_json_log_formatter() -> None:
    """Verify JsonLogFormatter produces valid single-line JSON with extra attributes."""
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Task completed successfully: %d items",
        args=(42,),
        exc_info=None,
    )
    # Add structured extra fields
    record.event = "task_done"
    record.duration_seconds = 1.25
    record.item_count = 42

    formatted = formatter.format(record)
    assert "\n" not in formatted

    data = json.loads(formatted)
    assert data["name"] == "test_logger"
    assert data["level"] == "INFO"
    assert data["message"] == "Task completed successfully: 42 items"
    assert data["event"] == "task_done"
    assert data["duration_seconds"] == 1.25
    assert data["item_count"] == 42
    assert "timestamp" in data


def test_configure_logging_json() -> None:
    """Verify configure_logging('json') applies JsonLogFormatter to root logger handlers."""
    configure_logging("json")
    root_logger = logging.getLogger()
    assert any(isinstance(h.formatter, JsonLogFormatter) for h in root_logger.handlers)

    # Revert to text
    configure_logging("text")
    assert not any(
        isinstance(h.formatter, JsonLogFormatter) for h in root_logger.handlers
    )


def test_app_initialization_with_json_log_format(tmp_path: Path) -> None:
    """Verify create_app initializes with JSON logging when configured."""

    class JsonLogConfig(TestConfig):
        LOG_FORMAT = "json"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'log_test.db'}"
        DATA_DIR = tmp_path

    app = create_app(JsonLogConfig)
    assert isinstance(app, Flask)
    root_logger = logging.getLogger()
    assert any(isinstance(h.formatter, JsonLogFormatter) for h in root_logger.handlers)

    # Clean up: revert logging
    configure_logging("text")
