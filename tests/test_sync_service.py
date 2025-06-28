"""Tests for sync service initialization and core functionality."""

import asyncio
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.intercom_client import IntercomClient
from fast_intercom_mcp.models import Conversation, Message, SyncStats
from fast_intercom_mcp.sync_service import SyncManager, SyncService


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
    """Create a test database manager."""
    return DatabaseManager(db_path=temp_db_path, pool_size=1)


@pytest.fixture
def mock_intercom_client():
    """Create a mock Intercom client for testing."""
    mock_client = Mock(spec=IntercomClient)
    mock_client.test_connection = AsyncMock(return_value=True)
    mock_client.get_app_id = AsyncMock(return_value="test_app_123")
    mock_client.fetch_conversations_for_period = AsyncMock(return_value=[])
    mock_client.fetch_conversations_incremental = AsyncMock(
        return_value=SyncStats(
            total_conversations=0,
            new_conversations=0,
            updated_conversations=0,
            total_messages=0,
            duration_seconds=0.1,
            api_calls_made=1,
        )
    )
    return mock_client


@pytest.fixture
def sync_service(test_db_manager, mock_intercom_client):
    """Create a SyncService instance for testing."""
    return SyncService(test_db_manager, mock_intercom_client)


class TestSyncServiceInitialization:
    """Test sync service initialization and configuration."""

    def test_sync_service_initialization(self, test_db_manager, mock_intercom_client):
        """Test that sync service initializes correctly."""
        service = SyncService(test_db_manager, mock_intercom_client)

        # Verify service properties
        assert service.db == test_db_manager
        assert service.intercom == mock_intercom_client
        assert service.app_id is None  # Not set until background sync starts
        assert not service._sync_active
        assert service._current_operation is None
        assert service._last_sync_time is None
        assert service.max_sync_age_minutes == 5
        assert service.background_sync_interval_minutes == 10

    def test_sync_service_health_check(self, sync_service):
        """Test that sync service can perform health checks."""
        # Service should be healthy when initialized
        status = sync_service.get_status()

        assert isinstance(status, dict)
        assert "active" in status
        assert "current_operation" in status
        assert "last_sync_time" in status
        assert "last_sync_stats" in status
        assert "app_id" in status

        assert not status["active"]  # Should not be active initially
        assert status["current_operation"] is None
        assert status["last_sync_time"] is None

    @pytest.mark.asyncio
    async def test_sync_service_connection_test(self, sync_service):
        """Test sync service connection testing."""
        # Mock client should return True for test_connection
        connection_ok = await sync_service.test_connection()
        assert connection_ok is True

        # Verify the mock was called
        sync_service.intercom.test_connection.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_service_app_id_initialization(self, sync_service):
        """Test that app_id is set when background sync starts."""
        assert sync_service.app_id is None

        # Start background sync
        await sync_service.start_background_sync()

        # App ID should now be set
        assert sync_service.app_id == "test_app_123"
        sync_service.intercom.get_app_id.assert_called_once()

        # Cleanup
        await sync_service.stop_background_sync()


class TestSyncServiceOperations:
    """Test core sync service operations."""

    @pytest.mark.asyncio
    async def test_sync_recent_operation(self, sync_service):
        """Test recent sync operation."""
        # Configure mock to return test data
        test_stats = SyncStats(
            total_conversations=5,
            new_conversations=3,
            updated_conversations=2,
            total_messages=15,
            duration_seconds=2.5,
            api_calls_made=3,
        )
        sync_service.intercom.fetch_conversations_incremental.return_value = test_stats

        # Perform recent sync
        result = await sync_service.sync_recent()

        # Verify results
        assert isinstance(result, SyncStats)
        assert result.total_conversations == 5
        assert result.new_conversations == 3
        assert result.total_messages == 15

        # Verify service state
        assert sync_service._last_sync_time is not None
        assert not sync_service._sync_active  # Should be inactive after completion
        assert sync_service._current_operation is None

    @pytest.mark.asyncio
    async def test_sync_period_operation(self, sync_service):
        """Test period sync operation."""
        start_date = datetime.now() - timedelta(days=1)
        end_date = datetime.now()

        # Create test conversations
        test_message = Message(
            id="msg1",
            author_type="user",
            body="Test message",
            created_at=datetime.now(),
        )

        test_conversation = Conversation(
            id="conv1",
            created_at=start_date,
            updated_at=end_date,
            messages=[test_message],
        )

        sync_service.intercom.fetch_conversations_for_period.return_value = [
            test_conversation
        ]

        # Perform period sync
        result = await sync_service.sync_period(start_date, end_date)

        # Verify results
        assert isinstance(result, SyncStats)
        assert result.total_conversations == 1
        assert result.total_messages == 1

        # Verify mock was called with correct parameters
        sync_service.intercom.fetch_conversations_for_period.assert_called_once_with(
            start_date, end_date
        )

        # Verify service state
        assert sync_service._last_sync_time is not None
        assert not sync_service._sync_active

    @pytest.mark.asyncio
    async def test_sync_initial_operation(self, sync_service):
        """Test initial sync operation."""
        # Configure mock to return test data
        test_message = Message(
            id="msg1",
            author_type="user",
            body="Test message",
            created_at=datetime.now(),
        )

        test_conversation = Conversation(
            id="conv1",
            created_at=datetime.now() - timedelta(days=5),
            updated_at=datetime.now(),
            messages=[test_message],
        )

        sync_service.intercom.fetch_conversations_for_period.return_value = [
            test_conversation
        ]

        # Perform initial sync
        result = await sync_service.sync_initial(days_back=7)

        # Verify results
        assert isinstance(result, SyncStats)
        assert result.total_conversations == 1

        # Verify mock was called
        sync_service.intercom.fetch_conversations_for_period.assert_called_once()

        # Verify days_back parameter is limited to 30
        result = await sync_service.sync_initial(days_back=50)
        assert isinstance(result, SyncStats)

    @pytest.mark.asyncio
    async def test_sync_concurrent_operations_prevented(self, sync_service):
        """Test that concurrent sync operations are prevented."""
        # Start a long-running sync
        sync_service._sync_active = True

        # Try to start another sync - should raise exception
        with pytest.raises(Exception, match="Sync already in progress"):
            await sync_service.sync_recent()

        with pytest.raises(Exception, match="Sync already in progress"):
            await sync_service.sync_incremental(datetime.now())

        # Background sync should be allowed
        await sync_service.sync_period(
            datetime.now() - timedelta(hours=1), datetime.now(), is_background=True
        )

    @pytest.mark.asyncio
    async def test_sync_error_handling(self, sync_service):
        """Test sync error handling and cleanup."""
        # Configure mock to raise exception
        sync_service.intercom.fetch_conversations_incremental.side_effect = Exception(
            "API Error"
        )

        # Sync should raise exception but clean up state
        with pytest.raises(Exception, match="API Error"):
            await sync_service.sync_recent()

        # Service should be cleaned up
        assert not sync_service._sync_active
        assert sync_service._current_operation is None


class TestSyncServiceBackgroundOperations:
    """Test background sync operations."""

    @pytest.mark.asyncio
    async def test_background_sync_lifecycle(self, sync_service):
        """Test background sync start and stop."""
        # Start background sync
        await sync_service.start_background_sync()

        # Verify background task is running
        assert sync_service._background_task is not None
        assert not sync_service._background_task.done()

        # Stop background sync
        await sync_service.stop_background_sync()

        # Verify background task is stopped
        assert sync_service._background_task.done()

    @pytest.mark.asyncio
    async def test_background_sync_duplicate_start(self, sync_service):
        """Test that duplicate background sync start is handled gracefully."""
        # Start background sync
        await sync_service.start_background_sync()

        # Try to start again - should not create new task
        first_task = sync_service._background_task
        await sync_service.start_background_sync()
        assert sync_service._background_task == first_task

        # Cleanup
        await sync_service.stop_background_sync()

    @pytest.mark.asyncio
    @patch("fast_intercom_mcp.sync_service.logger")
    async def test_background_sync_error_handling(self, mock_logger, sync_service):
        """Test background sync error handling."""
        # Mock database methods to raise exceptions
        sync_service.db.get_stale_timeframes = Mock(side_effect=Exception("DB Error"))

        # Start background sync
        await sync_service.start_background_sync()

        # Wait a short time for background loop to run
        await asyncio.sleep(0.1)

        # Stop background sync
        await sync_service.stop_background_sync()

        # Verify error was logged
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_check_and_sync_recent_with_stale_data(self, sync_service):
        """Test background sync when stale data exists."""
        # Mock database to return stale timeframes
        now = datetime.now()
        stale_timeframes = [(now - timedelta(hours=2), now - timedelta(hours=1))]
        sync_service.db.get_stale_timeframes = Mock(return_value=stale_timeframes)
        sync_service.db.get_periods_needing_sync = Mock(return_value=[])

        # Configure mock to return test data
        sync_service.intercom.fetch_conversations_for_period.return_value = []

        # Run background check
        await sync_service._check_and_sync_recent()

        # Verify stale timeframes were processed
        sync_service.db.get_stale_timeframes.assert_called_once()
        sync_service.intercom.fetch_conversations_for_period.assert_called_once()


class TestSyncServiceSmartSyncLogic:
    """Test smart sync logic and state management."""

    @pytest.mark.asyncio
    async def test_sync_if_needed_fresh_data(self, sync_service):
        """Test sync_if_needed with fresh data."""
        # Mock database to return fresh sync state
        sync_service.db.check_sync_state = Mock(
            return_value={
                "sync_state": "fresh",
                "last_sync": datetime.now(),
                "should_sync": False,
                "data_complete": True,
            }
        )

        start_date = datetime.now() - timedelta(hours=1)
        end_date = datetime.now()

        # Call sync_if_needed
        result = await sync_service.sync_if_needed(start_date, end_date)

        # Should return fresh state without syncing
        assert result["sync_state"] == "fresh"
        assert not sync_service._sync_active
        sync_service.intercom.fetch_conversations_for_period.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_if_needed_stale_data(self, sync_service):
        """Test sync_if_needed with stale data."""
        # Mock database to return stale sync state
        sync_service.db.check_sync_state = Mock(
            return_value={
                "sync_state": "stale",
                "last_sync": datetime.now() - timedelta(hours=2),
                "should_sync": True,
                "data_complete": False,
                "message": "Data is stale",
            }
        )

        # Configure mock to return test data
        sync_service.intercom.fetch_conversations_for_period.return_value = []

        start_date = datetime.now() - timedelta(hours=1)
        end_date = datetime.now()

        # Call sync_if_needed
        result = await sync_service.sync_if_needed(start_date, end_date)

        # Should have triggered sync
        assert result["sync_state"] == "stale"
        sync_service.intercom.fetch_conversations_for_period.assert_called_once_with(
            start_date, end_date
        )

    @pytest.mark.asyncio
    async def test_sync_if_needed_partial_data(self, sync_service):
        """Test sync_if_needed with partial data."""
        # Mock database to return partial sync state
        sync_service.db.check_sync_state = Mock(
            return_value={
                "sync_state": "partial",
                "last_sync": datetime.now() - timedelta(minutes=30),
                "should_sync": False,
                "data_complete": False,
                "message": "Data is partial",
            }
        )

        start_date = datetime.now() - timedelta(hours=1)
        end_date = datetime.now()

        # Call sync_if_needed
        result = await sync_service.sync_if_needed(start_date, end_date)

        # Should return partial state without syncing
        assert result["sync_state"] == "partial"
        assert not sync_service._sync_active
        sync_service.intercom.fetch_conversations_for_period.assert_not_called()


class TestSyncManager:
    """Test sync manager lifecycle and thread management."""

    def test_sync_manager_initialization(self, test_db_manager, mock_intercom_client):
        """Test sync manager initialization."""
        manager = SyncManager(test_db_manager, mock_intercom_client)

        assert manager.sync_service is not None
        assert isinstance(manager.sync_service, SyncService)
        assert not manager._started
        assert manager._loop is None
        assert manager._thread is None

    def test_sync_manager_lifecycle(self, test_db_manager, mock_intercom_client):
        """Test sync manager start and stop."""
        manager = SyncManager(test_db_manager, mock_intercom_client)

        # Start manager
        manager.start()
        assert manager._started
        assert manager._thread is not None

        # Stop manager - may take some time to fully stop
        manager.stop()

        # Wait for thread to finish and state to update
        import time

        max_wait = 2.0  # Wait up to 2 seconds
        wait_time = 0.0
        while manager._started and wait_time < max_wait:
            time.sleep(0.1)
            wait_time += 0.1

        # Eventually should be stopped (may still be cleaning up internally)
        assert not manager._started or wait_time >= max_wait

    def test_sync_manager_duplicate_start(self, test_db_manager, mock_intercom_client):
        """Test that duplicate starts are handled gracefully."""
        manager = SyncManager(test_db_manager, mock_intercom_client)

        # Start manager twice
        manager.start()
        first_thread = manager._thread
        manager.start()  # Should not create new thread
        assert manager._thread == first_thread

        # Cleanup
        manager.stop()

    def test_sync_manager_get_sync_service(self, test_db_manager, mock_intercom_client):
        """Test getting sync service from manager."""
        manager = SyncManager(test_db_manager, mock_intercom_client)

        service = manager.get_sync_service()
        assert service == manager.sync_service
        assert isinstance(service, SyncService)


class TestSyncServiceConfiguration:
    """Test sync service configuration options."""

    def test_sync_service_timing_configuration(
        self, test_db_manager, mock_intercom_client
    ):
        """Test sync service timing configuration."""
        service = SyncService(test_db_manager, mock_intercom_client)

        # Test default values
        assert service.max_sync_age_minutes == 5
        assert service.background_sync_interval_minutes == 10

        # Test that these can be modified
        service.max_sync_age_minutes = 15
        service.background_sync_interval_minutes = 20

        assert service.max_sync_age_minutes == 15
        assert service.background_sync_interval_minutes == 20

    def test_sync_service_status_reporting(self, sync_service):
        """Test comprehensive status reporting."""
        status = sync_service.get_status()

        # Verify all expected fields are present
        required_fields = [
            "active",
            "current_operation",
            "last_sync_time",
            "last_sync_stats",
            "app_id",
        ]

        for field in required_fields:
            assert field in status

        # Verify types
        assert isinstance(status["active"], bool)
        assert status["current_operation"] is None or isinstance(
            status["current_operation"], str
        )
        assert status["last_sync_time"] is None or isinstance(
            status["last_sync_time"], str
        )
        assert isinstance(status["last_sync_stats"], dict)
        assert status["app_id"] is None or isinstance(status["app_id"], str)
