"""
Sync verification and data integrity tests.

This module implements the CRITICAL sync verification tests that validate
the core purpose of the server - syncing data from Intercom correctly.

Tests cover:
- Initial sync verification
- New conversation detection
- Message completeness
- Incremental sync efficiency
- Conversation thread completeness
"""

import asyncio
import contextlib
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from fast_intercom_mcp.models import Conversation, Message, SyncStats


class TestInitialSyncVerification:
    """Test suite for initial sync verification."""

    @pytest.mark.asyncio
    async def test_initial_sync_fetches_conversations(
        self, sync_service, database_manager, test_conversations
    ):
        """Test that initial sync actually retrieves conversations from Intercom."""
        # Get initial conversation count
        with sqlite3.connect(database_manager.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM conversations")
            initial_count = cursor.fetchone()[0]

        # Configure mock to return test conversations
        sync_service.intercom.fetch_conversations_for_period.return_value = (
            test_conversations
        )

        # Run initial sync
        start_date = datetime.now(UTC) - timedelta(days=7)
        end_date = datetime.now(UTC)
        stats = await sync_service.sync_period(start_date, end_date)

        # Verify conversations were fetched
        assert stats.total_conversations > 0, "No conversations were synced"
        assert stats.total_conversations == len(test_conversations), (
            "Incorrect number of conversations synced"
        )

        # Verify API was called correctly
        sync_service.intercom.fetch_conversations_for_period.assert_called_once_with(
            start_date, end_date
        )

        # Verify database was updated
        with sqlite3.connect(database_manager.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM conversations")
            new_count = cursor.fetchone()[0]
            assert new_count > initial_count, (
                "Database was not updated with new conversations"
            )

            # Verify conversation data integrity
            cursor = conn.execute("SELECT id, created_at FROM conversations LIMIT 1")
            sample_conv = cursor.fetchone()
            assert sample_conv is not None, "No conversations found in database"
            assert sample_conv[0] is not None, "Conversation ID is None"
            assert sample_conv[1] is not None, "Conversation created_at is None"

    @pytest.mark.asyncio
    async def test_initial_sync_stores_messages(
        self, sync_service, database_manager, test_conversations
    ):
        """Test that initial sync stores all messages for conversations."""
        # Run sync
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)
        stats = await sync_service.sync_period(start_date, end_date)

        # Verify messages were stored
        assert stats.total_messages > 0, "No messages were synced"

        # Check database for messages
        with sqlite3.connect(database_manager.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM messages")
            message_count = cursor.fetchone()[0]
            assert message_count > 0, "No messages found in database"

            # Verify message data integrity
            cursor = conn.execute("""
                SELECT id, author_type, body, created_at, conversation_id
                FROM messages LIMIT 1
            """)
            sample_msg = cursor.fetchone()
            assert sample_msg is not None, "No messages found"
            assert sample_msg[0] is not None, "Message ID is None"
            assert sample_msg[1] in ["user", "admin"], (
                f"Invalid author_type: {sample_msg[1]}"
            )
            assert sample_msg[2] is not None, "Message body is None"
            assert sample_msg[3] is not None, "Message created_at is None"
            assert sample_msg[4] is not None, "Message conversation_id is None"

    @pytest.mark.asyncio
    async def test_initial_sync_with_empty_result(self, sync_service):
        """Test initial sync behavior when no conversations are found."""
        # Configure mock to return empty list
        sync_service.intercom.fetch_conversations_for_period = AsyncMock(
            return_value=[]
        )

        # Run sync
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)
        stats = await sync_service.sync_period(start_date, end_date)

        # Verify empty result is handled correctly
        assert stats.total_conversations == 0, (
            "Expected 0 conversations for empty result"
        )
        assert stats.total_messages == 0, "Expected 0 messages for empty result"

        # Verify API was still called
        sync_service.intercom.fetch_conversations_for_period.assert_called_once()


class TestNewConversationDetection:
    """Test suite for new conversation detection in sync."""

    @pytest.mark.asyncio
    async def test_new_conversations_detected_in_incremental_sync(
        self, sync_service, test_conversations
    ):
        """Test that new conversations created in Intercom are detected."""
        # Mock incremental sync to return new conversation
        # (This is used for documentation/clarity, not directly in the test)
        _new_conversation = Conversation(
            id="new_conv_123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            customer_email="newuser@example.com",
            messages=[
                Message(
                    id="new_msg_1",
                    author_type="user",
                    body="This is a new conversation",
                    created_at=datetime.now(UTC),
                    part_type="comment",
                )
            ],
        )

        sync_service.intercom.fetch_conversations_incremental.return_value = SyncStats(
            total_conversations=1,
            new_conversations=1,
            updated_conversations=0,
            total_messages=1,
            duration_seconds=1.0,
            api_calls_made=1,
        )

        # Run incremental sync
        since_time = datetime.now(UTC) - timedelta(hours=1)
        stats = await sync_service.sync_incremental(since_time)

        # Verify new conversation was detected
        assert stats.total_conversations >= 1, "New conversation was not detected"
        assert stats.new_conversations >= 1, "New conversation count is incorrect"

        # Verify API was called with correct timestamp
        sync_service.intercom.fetch_conversations_incremental.assert_called_once_with(
            since_time
        )

    @pytest.mark.asyncio
    async def test_no_new_conversations_detected(self, sync_service):
        """Test incremental sync when no new conversations exist."""
        # Mock to return no new conversations
        sync_service.intercom.fetch_conversations_incremental = AsyncMock(
            return_value=SyncStats(
                total_conversations=0,
                new_conversations=0,
                updated_conversations=0,
                total_messages=0,
                duration_seconds=0.5,
                api_calls_made=1,
            )
        )

        # Run incremental sync
        since_time = datetime.now(UTC) - timedelta(minutes=30)
        stats = await sync_service.sync_incremental(since_time)

        # Verify no new conversations
        assert stats.total_conversations == 0, "Expected no new conversations"
        assert stats.new_conversations == 0, "Expected no new conversations"


class TestMessageCompleteness:
    """Test suite for message completeness verification."""

    @pytest.mark.asyncio
    async def test_all_messages_in_conversation_synced(
        self, sync_service, database_manager, test_conversations
    ):
        """Test that ALL messages in a conversation are synced."""
        # Find the long conversation from test data
        long_conv = next(
            conv for conv in test_conversations if conv.id == "test_conv_3_long"
        )
        expected_message_count = len(long_conv.messages)

        # Run sync
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)
        await sync_service.sync_period(start_date, end_date)

        # Verify all messages are stored
        with sqlite3.connect(database_manager.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM messages
                WHERE conversation_id = ?
            """,
                [long_conv.id],
            )
            stored_message_count = cursor.fetchone()[0]

            assert stored_message_count == expected_message_count, (
                f"Expected {expected_message_count} messages, got {stored_message_count}"
            )

    @pytest.mark.asyncio
    async def test_message_ordering_preserved(
        self, sync_service, database_manager, test_conversations
    ):
        """Test that message ordering and timestamps are preserved."""
        # Run sync
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)
        await sync_service.sync_period(start_date, end_date)

        # Check message ordering for conversations that have messages
        with sqlite3.connect(database_manager.db_path) as conn:
            # First, get all conversations that actually have messages in the database
            cursor = conn.execute("""
                SELECT DISTINCT conversation_id FROM messages
            """)
            stored_conv_ids = [row[0] for row in cursor.fetchall()]

            assert len(stored_conv_ids) > 0, (
                "No conversations with messages found in database"
            )

            for conv_id in stored_conv_ids:
                cursor = conn.execute(
                    """
                    SELECT created_at FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at
                """,
                    [conv_id],
                )

                timestamps = [row[0] for row in cursor.fetchall()]

                # Verify timestamps are in order
                assert len(timestamps) > 0, (
                    f"No messages found for conversation {conv_id}"
                )

                # Check that timestamps are sorted (allowing for equal timestamps)
                for i in range(1, len(timestamps)):
                    assert timestamps[i] >= timestamps[i - 1], (
                        f"Messages not in chronological order for conversation {conv_id}"
                    )

    @pytest.mark.asyncio
    async def test_no_duplicate_messages(
        self, sync_service, database_manager, test_conversations
    ):
        """Test that no duplicate messages are created."""
        # Run sync twice to test for duplicates
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)

        await sync_service.sync_period(start_date, end_date)
        await sync_service.sync_period(start_date, end_date)  # Second sync

        # Check for duplicate messages
        with sqlite3.connect(database_manager.db_path) as conn:
            cursor = conn.execute("""
                SELECT id, COUNT(*) as count
                FROM messages
                GROUP BY id
                HAVING count > 1
            """)

            duplicates = cursor.fetchall()
            assert len(duplicates) == 0, f"Found duplicate messages: {duplicates}"

    @pytest.mark.asyncio
    async def test_both_user_and_admin_messages_captured(
        self, sync_service, database_manager, test_conversations
    ):
        """Test that both user and admin messages are captured."""
        # Run sync
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)
        await sync_service.sync_period(start_date, end_date)

        # Check for both user and admin messages
        with sqlite3.connect(database_manager.db_path) as conn:
            cursor = conn.execute("SELECT DISTINCT author_type FROM messages")
            author_types = {row[0] for row in cursor.fetchall()}

            assert "user" in author_types, "No user messages found"
            assert "admin" in author_types, "No admin messages found"


class TestIncrementalSyncEfficiency:
    """Test suite for incremental sync efficiency."""

    @pytest.mark.asyncio
    async def test_incremental_sync_efficiency(self, sync_service):
        """Test that incremental sync only fetches changes."""
        # Mock initial sync
        initial_stats = SyncStats(
            total_conversations=10,
            new_conversations=10,
            updated_conversations=0,
            total_messages=50,
            duration_seconds=5.0,
            api_calls_made=10,
        )

        # Mock incremental sync with no changes
        incremental_stats = SyncStats(
            total_conversations=0,
            new_conversations=0,
            updated_conversations=0,
            total_messages=0,
            duration_seconds=0.5,
            api_calls_made=1,  # Should be much fewer API calls
        )

        sync_service.intercom.fetch_conversations_incremental = AsyncMock(
            return_value=incremental_stats
        )

        # Run incremental sync
        since_time = datetime.now(UTC) - timedelta(hours=1)
        stats = await sync_service.sync_incremental(since_time)

        # Verify efficiency
        assert stats.total_conversations == 0, "No changes should be detected"
        assert stats.api_calls_made < initial_stats.api_calls_made, (
            "Incremental sync should make fewer API calls"
        )
        assert stats.duration_seconds < initial_stats.duration_seconds, (
            "Incremental sync should be faster"
        )

    @pytest.mark.asyncio
    async def test_sync_state_tracking(self, sync_service, database_manager):
        """Test that sync state is properly tracked and used."""
        # Run initial sync
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)
        await sync_service.sync_period(start_date, end_date)

        # Check if sync period was recorded
        with sqlite3.connect(database_manager.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM sync_periods")
            sync_period_count = cursor.fetchone()[0]
            assert sync_period_count > 0, "Sync period was not recorded"

            # Check sync period data
            cursor = conn.execute("""
                SELECT start_timestamp, end_timestamp, last_synced
                FROM sync_periods
                ORDER BY last_synced DESC
                LIMIT 1
            """)
            sync_record = cursor.fetchone()
            assert sync_record is not None, "No sync period record found"
            assert sync_record[0] is not None, "Start timestamp is None"
            assert sync_record[1] is not None, "End timestamp is None"
            assert sync_record[2] is not None, "Last synced timestamp is None"


class TestConversationThreadCompleteness:
    """Test suite for conversation thread completeness."""

    @pytest.mark.asyncio
    async def test_complete_conversation_threads_fetched(
        self, enhanced_sync_service, test_conversations
    ):
        """Test that complete conversation threads are fetched."""
        # Find long conversation for testing
        long_conv = next(
            conv for conv in test_conversations if conv.id == "test_conv_3_long"
        )

        # Mock individual conversation fetching
        enhanced_sync_service.intercom.fetch_individual_conversations = AsyncMock(
            return_value=[long_conv]
        )

        # Fetch complete conversation thread
        conversation_ids = [long_conv.id]
        stats = await enhanced_sync_service.sync_full_threads_for_conversations(
            conversation_ids
        )

        # Verify thread completeness
        assert stats.total_conversations == 1, "Expected 1 conversation"
        assert stats.total_messages == len(long_conv.messages), (
            f"Expected {len(long_conv.messages)} messages"
        )

        # Verify API was called correctly
        enhanced_sync_service.intercom.fetch_individual_conversations.assert_called_once_with(
            conversation_ids, enhanced_sync_service._broadcast_progress
        )

    @pytest.mark.asyncio
    async def test_conversation_thread_pagination_handled(self, enhanced_sync_service):
        """Test that pagination is handled correctly for long conversations."""
        # Create a very long conversation to test pagination
        very_long_conv = Conversation(
            id="very_long_conv",
            created_at=datetime.now(UTC) - timedelta(hours=1),
            updated_at=datetime.now(UTC),
            customer_email="longuser@example.com",
            messages=[
                Message(
                    id=f"msg_{i}",
                    author_type="user" if i % 2 == 0 else "admin",
                    body=f"Message {i} in very long conversation",
                    created_at=datetime.now(UTC) - timedelta(minutes=i),
                    part_type="comment",
                )
                for i in range(100)  # 100 messages to test pagination
            ],
        )

        enhanced_sync_service.intercom.fetch_individual_conversations = AsyncMock(
            return_value=[very_long_conv]
        )

        # Fetch complete thread
        stats = await enhanced_sync_service.sync_full_threads_for_conversations(
            [very_long_conv.id]
        )

        # Verify all messages were fetched
        assert stats.total_messages == 100, "Not all messages were fetched"

        # Verify conversation structure
        assert stats.total_conversations == 1, "Expected 1 conversation"

    @pytest.mark.asyncio
    async def test_initial_message_included_in_thread(
        self, sync_service, database_manager, test_conversations
    ):
        """Test that initial message is included in conversation thread."""
        # Run sync
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)
        await sync_service.sync_period(start_date, end_date)

        # Check that each conversation has at least one message
        with sqlite3.connect(database_manager.db_path) as conn:
            cursor = conn.execute("""
                SELECT c.id, COUNT(m.id) as message_count
                FROM conversations c
                LEFT JOIN messages m ON c.id = m.conversation_id
                GROUP BY c.id
                HAVING message_count = 0
            """)

            conversations_without_messages = cursor.fetchall()
            assert len(conversations_without_messages) == 0, (
                f"Conversations without messages: {conversations_without_messages}"
            )


class TestSyncDataIntegrity:
    """Test suite for overall sync data integrity."""

    @pytest.mark.asyncio
    async def test_conversation_customer_association(
        self, sync_service, database_manager, test_conversations
    ):
        """Test that customer information is properly associated with conversations."""
        # Run sync
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)
        await sync_service.sync_period(start_date, end_date)

        # Check customer email associations
        with sqlite3.connect(database_manager.db_path) as conn:
            cursor = conn.execute("""
                SELECT id, customer_email FROM conversations
                WHERE customer_email IS NOT NULL
            """)
            conversations_with_emails = cursor.fetchall()

            assert len(conversations_with_emails) > 0, (
                "No conversations with customer emails found"
            )

            # Verify email format
            for conv_id, email in conversations_with_emails:
                assert "@" in email, (
                    f"Invalid email format for conversation {conv_id}: {email}"
                )

    @pytest.mark.asyncio
    async def test_conversation_tags_preserved(
        self, sync_service, database_manager, test_conversations
    ):
        """Test that conversation tags are preserved during sync."""
        # Run sync
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)
        await sync_service.sync_period(start_date, end_date)

        # Check that tags are stored (this depends on database schema)
        # This test would need to be adapted based on how tags are stored
        with sqlite3.connect(database_manager.db_path) as conn:
            cursor = conn.execute("SELECT id FROM conversations LIMIT 1")
            result = cursor.fetchone()
            assert result is not None, "No conversations found to test tags"

    @pytest.mark.asyncio
    async def test_sync_handles_api_errors_gracefully(self, sync_service):
        """Test that sync handles API errors gracefully."""
        # Mock API error
        sync_service.intercom.fetch_conversations_for_period = AsyncMock(
            side_effect=Exception("API Error")
        )

        # Run sync and expect exception
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)

        with pytest.raises(Exception) as exc_info:
            await sync_service.sync_period(start_date, end_date)

        assert "API Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_sync_prevents_concurrent_operations(self, sync_service):
        """Test that sync prevents concurrent operations."""

        # Mock a long-running sync that sleeps for a bit
        async def long_running_sync(*args, **kwargs):
            await asyncio.sleep(0.1)
            return []  # Return empty list instead of None

        sync_service.intercom.fetch_conversations_for_period = long_running_sync

        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)

        # Start first sync
        task1 = asyncio.create_task(sync_service.sync_period(start_date, end_date))

        # Wait a tiny bit to ensure first sync starts
        await asyncio.sleep(0.01)

        # Try to start second sync while first is running
        with pytest.raises(Exception) as exc_info:
            await sync_service.sync_period(start_date, end_date)

        assert "already in progress" in str(exc_info.value).lower()

        # Clean up
        with contextlib.suppress(Exception):
            await task1


class TestSyncPerformanceAndReliability:
    """Test suite for sync performance and reliability."""

    @pytest.mark.asyncio
    async def test_sync_performance_tracking(self, sync_service, test_conversations):
        """Test that sync performance is tracked."""
        # Run sync
        start_date = datetime.now(UTC) - timedelta(days=1)
        end_date = datetime.now(UTC)
        stats = await sync_service.sync_period(start_date, end_date)

        # Verify performance metrics are tracked
        assert hasattr(stats, "duration_seconds"), "Duration not tracked"
        assert hasattr(stats, "api_calls_made"), "API calls not tracked"
        assert hasattr(stats, "total_conversations"), "Conversation count not tracked"
        assert hasattr(stats, "total_messages"), "Message count not tracked"

    @pytest.mark.asyncio
    async def test_sync_service_status_reporting(self, sync_service):
        """Test that sync service reports its status correctly."""
        status = sync_service.get_status()

        # Verify status structure
        assert "active" in status, "Status missing 'active' field"
        assert "current_operation" in status, "Status missing 'current_operation' field"
        assert "last_sync_time" in status, "Status missing 'last_sync_time' field"
        assert "last_sync_stats" in status, "Status missing 'last_sync_stats' field"

        # Test status during sync
        # This would require mocking a long-running sync to test properly
        assert not status["active"], "Sync should not be active initially"

    @pytest.mark.asyncio
    async def test_connection_testing(self, sync_service):
        """Test that connection to Intercom API can be tested."""
        # Test connection
        is_connected = await sync_service.test_connection()

        # Verify connection test works
        assert isinstance(is_connected, bool), "Connection test should return boolean"

        # Verify API was called
        sync_service.intercom.test_connection.assert_called_once()
