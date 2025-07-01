"""Unit tests for configuration module."""

import tempfile
from pathlib import Path

from fast_intercom_mcp.config import Config


def test_config_defaults():
    """Test Config default values."""
    config = Config(intercom_token="test_token")  # Required field
    assert config.intercom_token == "test_token"
    assert config.database_path is None  # Default
    assert config.initial_sync_days == 30
    assert config.log_level == "INFO"
    assert config.max_sync_age_minutes == 5
    assert config.background_sync_interval_minutes == 10


def test_config_from_env(monkeypatch):
    """Test Config loading from environment variables."""
    monkeypatch.setenv("INTERCOM_ACCESS_TOKEN", "test_token_123")
    monkeypatch.setenv("FASTINTERCOM_DB_PATH", "/tmp/test.db")
    monkeypatch.setenv("FASTINTERCOM_INITIAL_SYNC_DAYS", "7")
    monkeypatch.setenv("FASTINTERCOM_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("FASTINTERCOM_MAX_SYNC_AGE_MINUTES", "10")

    config = Config.load()
    assert config.intercom_token == "test_token_123"
    assert config.database_path == "/tmp/test.db"
    assert config.initial_sync_days == 7
    assert config.log_level == "DEBUG"
    assert config.max_sync_age_minutes == 10


def test_config_save_load(monkeypatch):
    """Test saving and loading config from file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_path = Path(f.name)

    try:
        # Set required env var for loading
        monkeypatch.setenv("INTERCOM_ACCESS_TOKEN", "save_test_token")

        # Create and save config (token won't be saved to file)
        config = Config(
            intercom_token="save_test_token",
            database_path="/tmp/save_test.db",
            initial_sync_days=14,
            log_level="WARNING",
        )
        config.save(str(config_path))

        # Load config - token comes from env
        loaded_config = Config.load(str(config_path))
        assert loaded_config.intercom_token == "save_test_token"  # From env
        assert loaded_config.database_path == "/tmp/save_test.db"
        assert loaded_config.initial_sync_days == 14
        assert loaded_config.log_level == "WARNING"
    finally:
        config_path.unlink()
