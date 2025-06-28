"""Configuration management for FastIntercom MCP server."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

from .logging import setup_enhanced_logging


@dataclass
class Config:
    """FastIntercom configuration."""

    intercom_token: str
    database_path: str | None = None
    connection_pool_size: int = 5
    log_level: str = "INFO"
    max_sync_age_minutes: int = 5
    background_sync_interval_minutes: int = 10
    initial_sync_days: int = 30  # 0 means ALL history

    @classmethod
    def load(cls, config_path: str | None = None) -> "Config":
        """Load configuration from file or environment variables."""
        # Load .env file if it exists
        load_dotenv()

        if config_path is None:
            config_path = cls.get_default_config_path()

        config_data = {}

        # Load from file if it exists
        if Path(config_path).exists():
            with open(config_path) as f:
                config_data = json.load(f)

        # Override with environment variables
        env_overrides = {
            "intercom_token": os.getenv("INTERCOM_ACCESS_TOKEN"),
            "database_path": os.getenv("FASTINTERCOM_DB_PATH"),
            "connection_pool_size": os.getenv("FASTINTERCOM_DB_POOL_SIZE"),
            "log_level": os.getenv("FASTINTERCOM_LOG_LEVEL"),
            "max_sync_age_minutes": os.getenv("FASTINTERCOM_MAX_SYNC_AGE_MINUTES"),
            "background_sync_interval_minutes": os.getenv(
                "FASTINTERCOM_BACKGROUND_SYNC_INTERVAL"
            ),
            "initial_sync_days": os.getenv("FASTINTERCOM_INITIAL_SYNC_DAYS"),
        }

        for key, value in env_overrides.items():
            if value is not None:
                if key in [
                    "connection_pool_size",
                    "max_sync_age_minutes",
                    "background_sync_interval_minutes",
                    "initial_sync_days",
                ]:
                    config_data[key] = int(value)
                else:
                    config_data[key] = value

        # Validate pool size
        if (
            "connection_pool_size" in config_data
            and config_data["connection_pool_size"] > 20
        ):
            raise ValueError("Database connection pool size cannot exceed 20")

        # Validate required fields
        if not config_data.get("intercom_token"):
            raise ValueError(
                "Intercom access token is required. Set INTERCOM_ACCESS_TOKEN environment variable "
                "or include 'intercom_token' in config file."
            )

        return cls(**config_data)

    def save(self, config_path: str | None = None):
        """Save configuration to file."""
        if config_path is None:
            config_path = self.get_default_config_path()

        # Ensure config directory exists
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)

        # Don't save the token to file for security
        config_data = asdict(self)
        config_data.pop("intercom_token", None)

        with open(config_path, "w") as f:
            json.dump(config_data, f, indent=2)

    @staticmethod
    def get_default_config_path() -> str:
        """Get the default configuration file path."""
        return str(Path.home() / ".fastintercom" / "config.json")

    @staticmethod
    def get_default_data_dir() -> str:
        """Get the default data directory."""
        return str(Path.home() / ".fastintercom")


def setup_logging(log_level: str = "INFO"):
    """Setup logging configuration with enhanced 3-file structure."""
    # Determine log directory - handle Docker environment
    if os.getenv("FASTINTERCOM_DATA_DIR"):
        # Docker environment
        log_dir = Path(os.getenv("FASTINTERCOM_DATA_DIR")) / "logs"
    else:
        # Local environment
        log_dir = Path.home() / ".fastintercom" / "logs"

    # Check if JSON logging is enabled
    enable_json = os.getenv("FASTINTERCOM_JSON_LOGGING", "").lower() in (
        "true",
        "1",
        "yes",
    )

    try:
        return setup_enhanced_logging(str(log_dir), log_level, enable_json)
    except (PermissionError, OSError):
        # Fallback to basic logging if setup fails
        import logging

        logging.basicConfig(
            level=getattr(logging, log_level.upper()),
            format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        )
        return {"log_dir": "console", "config": "basic"}
