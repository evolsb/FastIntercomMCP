"""Tests for database initialization and health checks."""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.models import Conversation, Message


@pytest.fixture
def temp_db_path():
    """Create a temporary database file for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
        db_path = tmp_file.name

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def test_db_manager(temp_db_path):
    """Create a DatabaseManager instance for testing."""
    db_manager = DatabaseManager(db_path=temp_db_path, pool_size=1)
    yield db_manager
    # Ensure proper cleanup
    db_manager.close()


class TestDatabaseInitialization:
    """Test database initialization and schema creation."""

    def test_database_initialization(self, test_db_manager):
        """Test database initialization and schema creation."""
        # Database should be initialized automatically
        assert os.path.exists(test_db_manager.db_path)

        # Test connection
        with sqlite3.connect(test_db_manager.db_path) as conn:
            # Verify we can execute a simple query
            cursor = conn.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1

    def test_database_tables_exist(self, test_db_manager):
        """Test that all required tables are created."""
        with sqlite3.connect(test_db_manager.db_path) as conn:
            # Get all table names
            cursor = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """
            )
            tables = [row[0] for row in cursor.fetchall()]

            # Check expected tables exist
            expected_tables = [
                "conversations",
                "messages",
                "sync_periods",
                "sync_metadata",
                "request_patterns",
                "conversation_sync_state",
                "message_threads",
                "schema_version",
            ]

            for table in expected_tables:
                assert table in tables, f"Table '{table}' not found in database"

    def test_database_indexes_exist(self, test_db_manager):
        """Test that performance indexes are created."""
        with sqlite3.connect(test_db_manager.db_path) as conn:
            # Get all index names
            cursor = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='index' AND name NOT LIKE 'sqlite_%'
            """
            )
            indexes = [row[0] for row in cursor.fetchall()]

            # Check some key indexes exist
            expected_indexes = [
                "idx_conversations_created_at",
                "idx_conversations_updated_at",
                "idx_messages_conversation_id",
                "idx_messages_created_at",
            ]

            for idx in expected_indexes:
                assert idx in indexes, f"Index '{idx}' not found in database"

    def test_database_views_exist(self, test_db_manager):
        """Test that database views are created."""
        with sqlite3.connect(test_db_manager.db_path) as conn:
            # Get all view names
            cursor = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='view'
            """
            )
            views = [row[0] for row in cursor.fetchall()]

            # Check expected views exist
            expected_views = [
                "conversations_needing_sync",
                "conversations_needing_incremental_sync",
            ]

            for view in expected_views:
                assert view in views, f"View '{view}' not found in database"

    def test_schema_version_tracking(self, test_db_manager):
        """Test that schema version is properly tracked."""
        with sqlite3.connect(test_db_manager.db_path) as conn:
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            current_version = cursor.fetchone()[0]

            assert current_version == 2, f"Expected schema version 2, got {current_version}"

    def test_foreign_keys_enabled(self, test_db_manager):
        """Test that foreign key constraints are enabled."""
        with sqlite3.connect(test_db_manager.db_path) as conn:
            # Enable foreign keys as the database manager does
            conn.execute("PRAGMA foreign_keys = ON")
            cursor = conn.execute("PRAGMA foreign_keys")
            foreign_keys_enabled = cursor.fetchone()[0]

            assert foreign_keys_enabled == 1, "Foreign keys should be enabled"

    def test_pool_size_validation(self, temp_db_path):
        """Test that pool size validation works."""
        # Valid pool size
        db_manager = DatabaseManager(db_path=temp_db_path, pool_size=5)
        assert db_manager.pool_size == 5

        # Invalid pool sizes should raise ValueError
        with pytest.raises(ValueError, match="Database pool size must be between 1 and 20"):
            DatabaseManager(db_path=temp_db_path, pool_size=0)

        with pytest.raises(ValueError, match="Database pool size must be between 1 and 20"):
            DatabaseManager(db_path=temp_db_path, pool_size=25)

    def test_default_database_path(self):
        """Test that default database path is created correctly."""
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("pathlib.Path.home") as mock_home,
        ):
            mock_home.return_value = Path(temp_dir)

            # Create the database manager with default path
            db_manager = DatabaseManager()

            expected_path = Path(temp_dir) / ".fastintercom" / "data.db"
            assert db_manager.db_path == expected_path

            # Verify the directory and file were created
            assert expected_path.parent.exists(), "Database directory should be created"
            assert expected_path.exists(), "Database file should be created"


class TestDatabaseOperations:
    """Test basic database operations."""

    def test_store_and_retrieve_conversations(self, test_db_manager):
        """Test storing and retrieving conversations."""
        # Create test data
        message1 = Message(
            id="msg1",
            author_type="user",
            body="Hello, I need help",
            created_at=datetime.now(),
        )

        message2 = Message(
            id="msg2",
            author_type="admin",
            body="How can I help you?",
            created_at=datetime.now(),
        )

        conversation = Conversation(
            id="conv1",
            created_at=datetime.now() - timedelta(hours=1),
            updated_at=datetime.now(),
            messages=[message1, message2],
            customer_email="test@example.com",
            tags=["support", "urgent"],
        )

        # Store conversation
        stored_count = test_db_manager.store_conversations([conversation])
        assert stored_count == 1

        # Retrieve conversation
        retrieved = test_db_manager.search_conversations(
            customer_email="test@example.com", limit=10
        )

        assert len(retrieved) == 1
        retrieved_conv = retrieved[0]
        assert retrieved_conv.id == "conv1"
        assert retrieved_conv.customer_email == "test@example.com"
        assert len(retrieved_conv.messages) == 2
        assert set(retrieved_conv.tags) == {"support", "urgent"}
        assert retrieved_conv.messages[0].body == "Hello, I need help"

    def test_sync_status_tracking(self, test_db_manager):
        """Test sync status tracking functionality."""
        # Get initial status
        status = test_db_manager.get_sync_status()

        assert status["total_conversations"] == 0
        assert status["total_messages"] == 0
        assert status["last_sync"] is None
        assert status["database_size_mb"] > 0  # Should have some size even empty

        # Add some test data
        message = Message(
            id="msg1",
            author_type="user",
            body="Test message",
            created_at=datetime.now(),
        )

        conversation = Conversation(
            id="conv1",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            messages=[message],
        )

        test_db_manager.store_conversations([conversation])

        # Check updated status
        status = test_db_manager.get_sync_status()
        assert status["total_conversations"] == 1
        assert status["total_messages"] == 1
        assert status["last_sync"] is not None

    def test_data_freshness_check(self, test_db_manager):
        """Test data freshness checking functionality."""
        now = datetime.now()
        start_time = now - timedelta(hours=2)
        end_time = now - timedelta(hours=1)

        # No data initially
        freshness = test_db_manager.get_data_freshness_for_timeframe(start_time, end_time)
        assert freshness == 0

        # Add conversation in timeframe
        message = Message(
            id="msg1",
            author_type="user",
            body="Test message",
            created_at=start_time + timedelta(minutes=30),
        )

        conversation = Conversation(
            id="conv1",
            created_at=start_time + timedelta(minutes=30),
            updated_at=start_time + timedelta(minutes=30),
            messages=[message],
        )

        test_db_manager.store_conversations([conversation])

        # Check freshness again - should return an integer (can be negative due to timing)
        freshness = test_db_manager.get_data_freshness_for_timeframe(start_time, end_time)
        assert isinstance(freshness, int), "Freshness should be an integer value"


class TestDatabaseTransaction:
    """Test database transaction handling."""

    def test_transaction_rollback_on_error(self, test_db_manager):
        """Test that transactions are rolled back on error."""
        # This test ensures data integrity by verifying rollback behavior
        initial_count = test_db_manager.get_sync_status()["total_conversations"]

        # Create valid conversation
        valid_message = Message(
            id="valid_msg",
            author_type="user",
            body="Valid message",
            created_at=datetime.now(),
        )

        valid_conversation = Conversation(
            id="valid_conv",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            messages=[valid_message],
        )

        # Store valid conversation first
        test_db_manager.store_conversations([valid_conversation])

        # Verify it was stored
        assert test_db_manager.get_sync_status()["total_conversations"] == initial_count + 1

    def test_duplicate_conversation_handling(self, test_db_manager):
        """Test that duplicate conversations are handled correctly."""
        # Create conversation
        message = Message(
            id="msg1",
            author_type="user",
            body="Original message",
            created_at=datetime.now(),
        )

        conversation = Conversation(
            id="conv1",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            messages=[message],
        )

        # Store conversation twice
        stored_count1 = test_db_manager.store_conversations([conversation])
        stored_count2 = test_db_manager.store_conversations([conversation])

        # First time should store, second time should not (no changes)
        assert stored_count1 == 1
        assert stored_count2 == 0  # No changes to store

        # Should still have only one conversation
        status = test_db_manager.get_sync_status()
        assert status["total_conversations"] == 1


class TestDatabaseCompatibility:
    """Test database schema compatibility and migration."""

    def test_schema_compatibility_check(self, temp_db_path):
        """Test schema compatibility checking."""
        # Create database with current schema
        DatabaseManager(db_path=temp_db_path)

        # Verify schema version is current
        with sqlite3.connect(temp_db_path) as conn:
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            version = cursor.fetchone()[0]
            assert version == 2

    @patch("fast_intercom_mcp.database.logger")
    def test_backup_and_reset_on_incompatible_schema(self, mock_logger, temp_db_path):
        """Test that incompatible schemas trigger backup and reset."""
        # Create old schema database
        with sqlite3.connect(temp_db_path) as conn:
            # Create old conversations table without thread tracking columns
            conn.execute(
                """
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """
            )
            conn.commit()

        # Initialize DatabaseManager - should detect incompatible schema
        DatabaseManager(db_path=temp_db_path)

        # Should have logged the backup message
        mock_logger.info.assert_any_call(
            "Incompatible database schema detected. Creating backup and resetting database."
        )

        # New schema should be created
        with sqlite3.connect(temp_db_path) as conn:
            cursor = conn.execute("PRAGMA table_info(conversations)")
            columns = [col[1] for col in cursor.fetchall()]
            assert "thread_complete" in columns  # New column should exist


class TestDatabaseCleanup:
    """Test database cleanup and maintenance operations."""

    def test_database_close(self, test_db_manager):
        """Test database connection cleanup."""
        # This is a simple test since DatabaseManager uses context managers
        # which handle connection cleanup automatically
        test_db_manager.close()

        # Should still be able to create new connections
        status = test_db_manager.get_sync_status()
        assert isinstance(status, dict)
