"""Unit tests for data models."""

from datetime import datetime

from fast_intercom_mcp.models import (
    Conversation,
    ConversationFilters,
    Message,
    ServerStatus,
    SyncStats,
)


def test_message_creation():
    """Test Message model creation."""
    msg = Message(id="test_id", author_type="user", body="Test message", created_at=datetime.now(), part_type="comment")
    assert msg.id == "test_id"
    assert msg.author_type == "user"
    assert msg.body == "Test message"
    assert msg.part_type == "comment"


def test_conversation_creation():
    """Test Conversation model creation."""
    conv = Conversation(
        id="conv_123",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        messages=[],
        customer_email="test@example.com",
        tags=["support", "urgent"],
    )
    assert conv.id == "conv_123"
    assert conv.customer_email == "test@example.com"
    assert len(conv.tags) == 2
    assert "support" in conv.tags


def test_conversation_url_generation():
    """Test conversation URL generation."""
    conv = Conversation(
        id="conv_123",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        messages=[],
        customer_email="test@example.com",
    )
    url = conv.get_url("app_456")
    assert "app_456" in url
    assert "conv_123" in url
    assert "test%40example.com" in url  # URL encoded email


def test_sync_stats():
    """Test SyncStats model."""
    stats = SyncStats(
        total_conversations=100,
        new_conversations=50,
        updated_conversations=30,
        total_messages=500,
        duration_seconds=10.5,
        api_calls_made=5,
    )
    assert stats.total_conversations == 100
    assert stats.new_conversations == 50
    assert stats.errors_encountered == 0  # default value


def test_server_status():
    """Test ServerStatus model."""
    status = ServerStatus(
        is_running=True,
        database_size_mb=25.5,
        total_conversations=1000,
        total_messages=5000,
        last_sync=datetime.now(),
        background_sync_active=True,
    )
    assert status.is_running is True
    assert status.database_size_mb == 25.5
    assert status.mcp_requests_served == 0  # default value


def test_conversation_filters():
    """Test ConversationFilters model."""
    filters = ConversationFilters(query="urgent", customer_email="customer@example.com", tags=["priority"], limit=50)
    assert filters.query == "urgent"
    assert filters.limit == 50
    assert filters.start_date is None  # Optional field
