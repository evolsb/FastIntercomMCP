"""Basic database functionality tests."""

import os
import sqlite3
import tempfile
from datetime import datetime

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
    db_manager.close()


class TestDatabase:
    """Test basic database functionality."""

    def test_database_can_be_created(self, test_db_manager):
        """Test database can be created and initialized."""
        assert os.path.exists(test_db_manager.db_path)

        # Test basic connection
        with sqlite3.connect(test_db_manager.db_path) as conn:
            cursor = conn.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1

    def test_database_tables_exist(self, test_db_manager):
        """Test that required tables are created."""
        with sqlite3.connect(test_db_manager.db_path) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """)
            tables = [row[0] for row in cursor.fetchall()]

            # Check key tables exist
            expected_tables = ["conversations", "messages"]
            for table in expected_tables:
                assert table in tables, f"Table '{table}' not found in database"

    def test_database_basic_operations(self, test_db_manager):
        """Test basic store and retrieve operations."""
        # Create test data
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
            customer_email="test@example.com",
        )

        # Store conversation
        stored_count = test_db_manager.store_conversations([conversation])
        assert stored_count == 1

        # Get sync status
        status = test_db_manager.get_sync_status()
        assert status["total_conversations"] == 1
        assert status["total_messages"] == 1