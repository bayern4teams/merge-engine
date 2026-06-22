"""
Structured logging configuration for merge-engine.

Uses structlog for consistent, JSON-structured logs suitable for production
log aggregation systems (e.g., Datadog, Splunk, CloudWatch).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog


def setup_logging(
    level: str = "INFO",
    json: bool = False,
    log_file: str | None = None,
    app_name: str = "merge-engine",
) -> None:
    """Configure structured logging for the application.

    Parameters
    ----------
    level : str
        Logging level string (e.g. "DEBUG", "INFO", "WARNING", "ERROR").
    json : bool
        If True, use JSONFormatter for machine-readable output.
    log_file : str, optional
        If provided, also write logs to this file path.
    app_name : str
        Application name used as a logger name prefix.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Base structlog configuration
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="%Y-%m-%dT%H:%M:%S.%z", utc=False),
            structlog.dev.ConsoleRenderer() if not json else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory() if not json else structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    # Configure stdlib logger for compatibility
    stdlib_logger = logging.getLogger(app_name)
    stdlib_logger.setLevel(log_level)

    formatter: logging.Formatter
    if json:
        formatter = _JSONFormatter(app_name=app_name)
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)
    stdlib_logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(log_level)
        stdlib_logger.addHandler(file_handler)


# ---------------------------------------------------------------------------
# JSON formatter (stdlib-compatible, no structlib dependency required)
# ---------------------------------------------------------------------------


class _JSONFormatter(logging.Formatter):
    """Minimal JSON formatter for stdlib logging records."""

    def __init__(self, app_name: str = "merge-engine") -> None:
        super().__init__()
        self.app_name = app_name

    def format(self, record: logging.LogRecord) -> str:
        import json

        return json.dumps(
            {
                "timestamp": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
                "app": self.app_name,
                **getattr(record, "extra_data", {}),
            }
        )