"""
Unit tests for the logging configuration module.

Tests setup_logging function and JSON formatter.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSetupLogging:
    """Tests for the setup_logging function."""

    def test_default_setup(self) -> None:
        """Should configure logging with default settings."""
        from src.utils.logging_config import setup_logging

        with patch("logging.getLogger") as mock_getlogger, \
             patch("structlog.configure"):
            mock_logger = MagicMock()
            mock_getlogger.return_value = mock_logger
            setup_logging()
            mock_getlogger.assert_called_once_with("merge-engine")
            mock_logger.setLevel.assert_called_once_with(logging.INFO)

    def test_custom_level(self) -> None:
        """Should respect custom log level."""
        from src.utils.logging_config import setup_logging

        with patch("logging.getLogger") as mock_getlogger, \
             patch("structlog.configure"):
            mock_logger = MagicMock()
            mock_getlogger.return_value = mock_logger
            setup_logging(level="DEBUG")
            mock_logger.setLevel.assert_called_once_with(logging.DEBUG)

    def test_warning_level(self) -> None:
        """Should handle WARNING level."""
        from src.utils.logging_config import setup_logging

        with patch("logging.getLogger") as mock_getlogger, \
             patch("structlog.configure"):
            mock_logger = MagicMock()
            mock_getlogger.return_value = mock_logger
            setup_logging(level="WARNING")
            mock_logger.setLevel.assert_called_once_with(logging.WARNING)

    def test_error_level(self) -> None:
        """Should handle ERROR level."""
        from src.utils.logging_config import setup_logging

        with patch("logging.getLogger") as mock_getlogger, \
             patch("structlog.configure"):
            mock_logger = MagicMock()
            mock_getlogger.return_value = mock_logger
            setup_logging(level="ERROR")
            mock_logger.setLevel.assert_called_once_with(logging.ERROR)

    def test_invalid_level_defaults_to_info(self) -> None:
        """Invalid level string should default to INFO."""
        from src.utils.logging_config import setup_logging

        with patch("logging.getLogger") as mock_getlogger, \
             patch("structlog.configure"):
            mock_logger = MagicMock()
            mock_getlogger.return_value = mock_logger
            setup_logging(level="INVALID_LEVEL_NAME")
            mock_logger.setLevel.assert_called_once_with(logging.INFO)

    def test_custom_app_name(self) -> None:
        """Should use custom application name."""
        from src.utils.logging_config import setup_logging

        with patch("logging.getLogger") as mock_getlogger, \
             patch("structlog.configure"):
            mock_logger = MagicMock()
            mock_getlogger.return_value = mock_logger
            setup_logging(app_name="my-custom-app")
            mock_getlogger.assert_called_once_with("my-custom-app")

    def test_json_formatter_enabled(self) -> None:
        """Should use JSONFormatter when json=True."""
        from src.utils.logging_config import setup_logging

        with patch("logging.getLogger") as mock_getlogger, \
             patch("sys.stdout", MagicMock()), \
             patch("structlog.configure"):
            mock_logger = MagicMock()
            mock_getlogger.return_value = mock_logger
            setup_logging(json=True)
            handlers = mock_logger.addHandler.call_args_list
            assert len(handlers) >= 1

    def test_log_file_handler(self, tmp_path: Path) -> None:
        """Should create file handler when log_file is provided."""
        from src.utils.logging_config import setup_logging

        log_file = str(tmp_path / "logs" / "app.log")

        with patch("logging.getLogger") as mock_getlogger, \
             patch("structlog.configure"):
            mock_logger = MagicMock()
            mock_getlogger.return_value = mock_logger
            setup_logging(log_file=log_file)
            handler_calls = mock_logger.addHandler.call_args_list
            assert len(handler_calls) >= 2

    def test_log_file_creates_directory(self, tmp_path: Path) -> None:
        """Should create log directory if it doesn't exist."""
        from src.utils.logging_config import setup_logging

        log_file = str(tmp_path / "nested" / "dirs" / "app.log")

        with patch("logging.getLogger") as mock_getlogger, \
             patch("structlog.configure"):
            mock_logger = MagicMock()
            mock_getlogger.return_value = mock_logger
            setup_logging(log_file=log_file)
            log_dir = Path(log_file).parent
            assert log_dir.exists()

    def test_console_handler_added(self) -> None:
        """Should always add a console handler."""
        from src.utils.logging_config import setup_logging

        with patch("logging.getLogger") as mock_getlogger, \
             patch("sys.stdout", MagicMock()), \
             patch("structlog.configure"):
            mock_logger = MagicMock()
            mock_getlogger.return_value = mock_logger
            setup_logging()
            mock_logger.addHandler.assert_called()

    def test_multiple_level_options(self) -> None:
        """Should handle all standard logging levels."""
        from src.utils.logging_config import setup_logging

        levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        expected = [
            logging.DEBUG, logging.INFO, logging.WARNING,
            logging.ERROR, logging.CRITICAL,
        ]

        for level_str, expected_level in zip(levels, expected):
            with patch("logging.getLogger") as mock_getlogger, \
                 patch("structlog.configure"):
                mock_logger = MagicMock()
                mock_getlogger.return_value = mock_logger
                setup_logging(level=level_str)
                mock_logger.setLevel.assert_called_with(expected_level)


class TestJSONFormatter:
    """Tests for the _JSONFormatter class."""

    def test_json_formatter_basic(self) -> None:
        """Should produce valid JSON output."""
        import json
        from src.utils.logging_config import _JSONFormatter

        formatter = _JSONFormatter(app_name="test-app")
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "Test message"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert parsed["app"] == "test-app"
        assert parsed["module"] == "test"
        assert parsed["line"] == 42

    def test_json_formatter_custom_app_name(self) -> None:
        """Should use custom app name."""
        import json
        from src.utils.logging_config import _JSONFormatter

        formatter = _JSONFormatter(app_name="custom-app")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="test", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["app"] == "custom-app"

    def test_json_formatter_with_extra_data(self) -> None:
        """Should include extra data in JSON output."""
        import json
        from src.utils.logging_config import _JSONFormatter

        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="test", args=(), exc_info=None,
        )
        record.extra_data = {"request_id": "abc-123", "user": "alice"}
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["request_id"] == "abc-123"
        assert parsed["user"] == "alice"

    def test_json_formatter_contains_timestamp(self) -> None:
        """JSON output should contain a timestamp."""
        import json
        from src.utils.logging_config import _JSONFormatter

        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="test", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "timestamp" in parsed

    def test_json_formatter_contains_function(self) -> None:
        """JSON output should contain function info."""
        import json
        from src.utils.logging_config import _JSONFormatter

        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=42, msg="test", args=(), exc_info=None,
        )
        record.funcName = "test_function"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["function"] == "test_function"

    def test_json_formatter_different_levels(self) -> None:
        """Should handle different log levels."""
        import json
        from src.utils.logging_config import _JSONFormatter

        formatter = _JSONFormatter()

        for level_name, level_num in [
            ("DEBUG", logging.DEBUG),
            ("INFO", logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
            ("CRITICAL", logging.CRITICAL),
        ]:
            record = logging.LogRecord(
                name="test", level=level_num, pathname="test.py",
                lineno=1, msg="test", args=(), exc_info=None,
            )
            output = formatter.format(record)
            parsed = json.loads(output)
            assert parsed["level"] == level_name

    def test_json_formatter_with_args(self) -> None:
        """Should format message with args."""
        import json
        from src.utils.logging_config import _JSONFormatter

        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="Hello %s", args=("world",), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "Hello world"

    def test_json_formatter_error_level(self) -> None:
        """Should handle ERROR level records."""
        import json
        from src.utils.logging_config import _JSONFormatter

        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="test.py",
            lineno=100, msg="Something failed", args=(), exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "ERROR"
        assert parsed["message"] == "Something failed"

    def test_json_formatter_exception_info(self) -> None:
        """Should handle records with exception info."""
        import json
        from src.utils.logging_config import _JSONFormatter

        formatter = _JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="test.py",
            lineno=1, msg="An error occurred", args=(), exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "An error occurred"