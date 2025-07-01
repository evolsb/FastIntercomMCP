"""Unit tests for configuration module."""

import tempfile
from pathlib import Path

from fast_intercom_mcp.config import Config


def test_config_defaults():
    """Test Config default values."""
    config = Config()
    assert config.intercom_token == ""
    assert config.db_path == "~/.config/fast-intercom-mcp/intercom_data.db"
    assert config.initial_sync_days == 30
    assert config.mcp_host == "localhost"
    assert config.mcp_port == 8080
    assert config.log_level == "INFO"


def test_config_from_env(monkeypatch):
    """Test Config loading from environment variables."""
    monkeypatch.setenv("INTERCOM_TOKEN", "test_token_123")
    monkeypatch.setenv("DB_PATH", "/tmp/test.db")
    monkeypatch.setenv("INITIAL_SYNC_DAYS", "7")
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9000")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    config = Config.from_env()
    assert config.intercom_token == "test_token_123"
    assert config.db_path == "/tmp/test.db"
    assert config.initial_sync_days == 7
    assert config.mcp_host == "0.0.0.0"
    assert config.mcp_port == 9000
    assert config.log_level == "DEBUG"


def test_config_save_load():
    """Test saving and loading config from file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        config_path = Path(f.name)

    try:
        # Create and save config
        config = Config(
            intercom_token="save_test_token", db_path="/tmp/save_test.db", initial_sync_days=14, log_level="WARNING"
        )
        config.save(config_path)

        # Load config
        loaded_config = Config.load(config_path)
        assert loaded_config.intercom_token == "save_test_token"
        assert loaded_config.db_path == "/tmp/save_test.db"
        assert loaded_config.initial_sync_days == 14
        assert loaded_config.log_level == "WARNING"
    finally:
        config_path.unlink()
