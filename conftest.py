"""Test configuration and fixtures for fast-intercom-mcp."""

import asyncio
import os
import tempfile
from datetime import datetime, timedelta

try:
    from datetime import UTC
except ImportError:
    # Python < 3.11 compatibility
    from datetime import timezone

    UTC = timezone.utc
from unittest.mock import AsyncMock, Mock

import pytest

from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.intercom_client import IntercomClient
from fast_intercom_mcp.models import Conversation, Message, SyncStats
from fast_intercom_mcp.sync_service import SyncService


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_db():
    """Provide a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        yield db_path
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


@pytest.fixture
def database_manager(temp_db):
    """Provide a DatabaseManager instance with temporary database."""
    db_manager = DatabaseManager(db_path=temp_db)
    yield db_manager
    # Ensure proper cleanup
    db_manager.close()


@pytest.fixture
def test_conversations():
    """Provide test conversation data for sync verification."""
    return [
        Conversation(
            id="test_conv_1",
            created_at=datetime.now(UTC) - timedelta(hours=2),
            updated_at=datetime.now(UTC) - timedelta(hours=1),
            customer_email="user1@example.com",
            tags=["support", "high-priority"],
            messages=[
                Message(
                    id="msg_1",
                    author_type="user",
                    body="I need help with my account",
                    created_at=datetime.now(UTC) - timedelta(hours=2),
                    part_type="comment",
                ),
                Message(
                    id="msg_2",
                    author_type="admin",
                    body="I'd be happy to help you with that",
                    created_at=datetime.now(UTC) - timedelta(hours=2, minutes=5),
                    part_type="comment",
                ),
                Message(
                    id="msg_3",
                    author_type="user",
                    body="Thank you for the quick response",
                    created_at=datetime.now(UTC) - timedelta(hours=1),
                    part_type="comment",
                ),
            ],
        ),
        Conversation(
            id="test_conv_2",
            created_at=datetime.now(UTC) - timedelta(hours=3),
            updated_at=datetime.now(UTC) - timedelta(hours=3),
            customer_email="user2@example.com",
            tags=["billing"],
            messages=[
                Message(
                    id="msg_4",
                    author_type="user",
                    body="Question about my invoice",
                    created_at=datetime.now(UTC) - timedelta(hours=3),
                    part_type="comment",
                ),
                Message(
                    id="msg_5",
                    author_type="admin",
                    body="Let me look into that for you",
                    created_at=datetime.now(UTC) - timedelta(hours=3, minutes=2),
                    part_type="comment",
                ),
            ],
        ),
        Conversation(
            id="test_conv_3_long",
            created_at=datetime.now(UTC) - timedelta(hours=4),
            updated_at=datetime.now(UTC) - timedelta(minutes=30),
            customer_email="user3@example.com",
            tags=["feature-request"],
            messages=[
                Message(
                    id=f"msg_{i}",
                    author_type="user" if i % 2 == 0 else "admin",
                    body=f"Message {i} content",
                    created_at=datetime.now(UTC) - timedelta(hours=4, minutes=i * 5),
                    part_type="comment",
                )
                for i in range(25)  # Long conversation with 25 messages
            ],
        ),
    ]


@pytest.fixture
def mock_intercom_client(test_conversations):
    """Provide a mock IntercomClient for testing."""
    client = Mock(spec=IntercomClient)

    # Use AsyncMock for proper assertion support
    client.fetch_conversations_for_period = AsyncMock(return_value=test_conversations)
    client.fetch_conversations_incremental = AsyncMock(
        return_value=SyncStats(
            total_conversations=1,
            new_conversations=1,
            updated_conversations=0,
            total_messages=2,
            duration_seconds=1.0,
            api_calls_made=1,
        )
    )
    client.fetch_complete_conversation_thread = AsyncMock()
    client.fetch_individual_conversations = AsyncMock(return_value=test_conversations)
    client.get_app_id = AsyncMock(return_value="test_app_123")
    client.test_connection = AsyncMock(return_value=True)
    return client


@pytest.fixture
def sync_service(database_manager, mock_intercom_client):
    """Provide a SyncService instance for testing."""
    service = SyncService(database_manager, mock_intercom_client)
    yield service
    # Note: No cleanup needed for unit tests as service doesn't start background tasks


@pytest.fixture
def test_sync_stats():
    """Provide test sync statistics."""
    return SyncStats(
        total_conversations=3,
        new_conversations=2,
        updated_conversations=1,
        total_messages=30,
        duration_seconds=5.5,
        api_calls_made=3,
    )


@pytest.fixture
def mock_progress_callback():
    """Provide a mock progress callback for testing."""
    return Mock()
