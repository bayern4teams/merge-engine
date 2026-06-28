"""
Tests for src/utils/logging_config.py - Structured logging configuration.
"""

import json
import logging
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.logging_config import setup_logging, _JSONFormatter


class TestSetupLogging:
    """Tests for setup_logging()."""

    def test_setup_logging_default_level(self):
        """Default logging level should be INFO."""
        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            setup_logging()
            mock_logger.setLevel.assert_called_with(logging.INFO)

    def test_setup_logging_custom_level(self):
        """Custom logging level should be applied."""
        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            setup_logging(level="DEBUG")
            mock_logger.setLevel.assert_called_with(logging.DEBUG)

    def test_setup_logging_error_level(self):
        """ERROR level should be applied."""
        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            setup_logging(level="ERROR")
            mock_logger.setLevel.assert_called_with(logging.ERROR)

    def test_setup_logging_with_console_handler(self):
        """Console handler should be added."""
        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            setup_logging()
            # Check that addHandler was called for console handler
            handler_calls = mock_logger.addHandler.call_count
            assert handler_calls >= 1

    def test_setup_logging_custom_app_name(self):
        """Custom app name should be used as logger name prefix."""
        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            setup_logging(app_name="custom-app")
            mock_get_logger.assert_called_with("custom-app")

    def test_setup_logging_with_file_handler(self, tmp_path):
        """File handler should be added when log_file is specified."""
        log_file = str(tmp_path / "test.log")
        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            setup_logging(log_file=log_file)
            # Should have both console and file handlers
            assert mock_logger.addHandler.call_count >= 2

    def test_setup_logging_creates_log_directory(self, tmp_path):
        """Log directory should be created if it doesn't exist."""
        log_file = str(tmp_path / "subdir" / "nested" / "test.log")
        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            setup_logging(log_file=log_file)
            assert Path(tmp_path / "subdir" / "nested").exists()

    def test_setup_logging_json_mode(self):
        """JSON mode should use JSONFormatter."""
        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            setup_logging(json=True)
            # Check handler was added with JSON formatter
            handlers = [
                call.args[0] for call in mock_logger.addHandler.call_args_list
            ]
            # At least one handler should use JSONFormatter
            json_handlers = [
                h for h in handlers
                if isinstance(h.formatter, _JSONFormatter)
            ]
            assert len(json_handlers) >= 1


class TestJSONFormatter:
    """Tests for _JSONFormatter class."""

    def test_json_formatter_init(self):
        """_JSONFormatter should initialize with default app name."""
        formatter = _JSONFormatter()
        assert formatter.app_name == "merge-engine"

    def test_json_formatter_custom_app_name(self):
        """_JSONFormatter should accept custom app name."""
        formatter = _JSONFormatter(app_name="test-app")
        assert formatter.app_name == "test-app"

    def test_json_formatter_output_structure(self):
        """JSON output should contain standard fields."""
        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="test-logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = json.loads(formatter.format(record))
        assert "timestamp" in output
        assert output["level"] == "INFO"
        assert output["logger"] == "test-logger"
        assert output["message"] == "Test message"
        assert output["module"] == "test"
        # funcName is None for manually created LogRecord without funcName
        assert output["function"] is None or output["function"] == "<module>"
        assert output["line"] == 42
        assert output["app"] == "merge-engine"

    def test_json_formatter_with_extra_data(self):
        """JSON output should include extra_data if present."""
        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="test-logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.extra_data = {"user_id": 123, "action": "login"}
        output = json.loads(formatter.format(record))
        assert output["user_id"] == 123
        assert output["action"] == "login"

    def test_json_formatter_escapes_special_chars(self):
        """JSON output should properly escape special characters."""
        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="test-logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg='Message with "quotes" and \\backslash',
            args=(),
            exc_info=None,
        )
        output = json.loads(formatter.format(record))
        assert '"' in output["message"] or '""' in output["message"]
