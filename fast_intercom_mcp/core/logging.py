"""Enhanced logging system with JSON format for FastIntercom MCP."""

import json
import logging
import logging.config
import logging.handlers
from datetime import datetime
from pathlib import Path
from typing import Any


class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        if hasattr(record, "extra_data"):
            log_data.update(record.extra_data)

        return json.dumps(log_data)


def setup_enhanced_logging(
    log_dir: str, log_level: str, enable_json: bool = False
) -> dict[str, Any]:
    """
    Setup enhanced logging with 3-file structure and optional JSON formatting.

    Args:
        log_dir: Directory to store log files
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        enable_json: Whether to use JSON formatting

    Returns:
        Dict with logging configuration info
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Define log files
    main_log = log_path / "main.log"
    sync_log = log_path / "sync.log"
    errors_log = log_path / "errors.log"

    # Choose formatter
    if enable_json:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s:%(funcName)s:%(lineno)d - %(message)s"
        )

    # Create handlers
    handlers = {
        "console": logging.StreamHandler(),
        "main_file": logging.handlers.RotatingFileHandler(
            main_log, maxBytes=10 * 1024 * 1024, backupCount=5
        ),
        "sync_file": logging.handlers.RotatingFileHandler(
            sync_log, maxBytes=10 * 1024 * 1024, backupCount=5
        ),
        "error_file": logging.handlers.RotatingFileHandler(
            errors_log, maxBytes=10 * 1024 * 1024, backupCount=5
        ),
    }

    # Set formatters
    for handler in handlers.values():
        handler.setFormatter(formatter)

    # Set levels
    level = getattr(logging, log_level.upper())
    handlers["console"].setLevel(level)
    handlers["main_file"].setLevel(level)
    handlers["sync_file"].setLevel(level)
    handlers["error_file"].setLevel(logging.ERROR)

    # Configure loggers
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json" if enable_json else "standard": {
                "()": JSONFormatter if enable_json else logging.Formatter,
                "format": "%(asctime)s [%(levelname)s] "
                "%(name)s:%(funcName)s:%(lineno)d - %(message)s",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level.upper(),
                "formatter": "json" if enable_json else "standard",
            },
            "main_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(main_log),
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 5,
                "level": log_level.upper(),
                "formatter": "json" if enable_json else "standard",
            },
            "sync_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(sync_log),
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 5,
                "level": log_level.upper(),
                "formatter": "json" if enable_json else "standard",
            },
            "error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(errors_log),
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 5,
                "level": "ERROR",
                "formatter": "json" if enable_json else "standard",
            },
        },
        "loggers": {
            "": {  # Root logger
                "handlers": ["console", "main_file", "error_file"],
                "level": log_level.upper(),
                "propagate": False,
            },
            "fast_intercom_mcp.sync_service": {
                "handlers": ["console", "sync_file", "error_file"],
                "level": log_level.upper(),
                "propagate": False,
            },
            "fast_intercom_mcp.background_sync": {
                "handlers": ["console", "sync_file", "error_file"],
                "level": log_level.upper(),
                "propagate": False,
            },
        },
    }

    # Apply configuration
    logging.config.dictConfig(logging_config)

    # Reduce noise from httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)

    return {
        "log_dir": log_dir,
        "main_log": str(main_log),
        "sync_log": str(sync_log),
        "errors_log": str(errors_log),
        "json_enabled": enable_json,
        "level": log_level,
    }
