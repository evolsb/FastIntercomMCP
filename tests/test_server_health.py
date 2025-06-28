"""Server startup and health check tests."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.mcp_server import FastIntercomMCPServer
from fast_intercom_mcp.sync_service import SyncService


class TestServerHealth:
    """Test server startup and health check functionality."""

    @pytest.fixture
    def mock_database_manager(self):
        """Create a mock database manager."""
        mock_db = Mock(spec=DatabaseManager)
        mock_db.db_path = ":memory:"
        mock_db.get_sync_status.return_value = {
            "database_size_mb": 1.5,
            "total_conversations": 100,
            "total_messages": 500,
            "last_sync": "2023-01-01T12:00:00",
            "database_path": ":memory:",
            "recent_syncs": [],
        }
        mock_db.search_conversations.return_value = []
        mock_db.get_data_freshness_for_timeframe.return_value = 0
        mock_db.record_request_pattern = Mock()
        return mock_db

    @pytest.fixture
    def mock_sync_service(self):
        """Create a mock sync service."""
        mock_sync = Mock(spec=SyncService)
        mock_sync.get_status.return_value = {"active": True, "current_operation": None}
        mock_sync.sync_if_needed = AsyncMock(
            return_value={
                "sync_state": "fresh",
                "message": "Data is up to date",
                "data_complete": True,
            }
        )
        return mock_sync

    @pytest.fixture
    def mock_intercom_client(self):
        """Create a mock Intercom client."""
        return Mock()

    @pytest.fixture
    def server(self, mock_database_manager, mock_sync_service, mock_intercom_client):
        """Create a FastIntercomMCPServer instance for testing."""
        return FastIntercomMCPServer(
            database_manager=mock_database_manager,
            sync_service=mock_sync_service,
            intercom_client=mock_intercom_client,
        )

    @pytest.mark.asyncio
    async def test_server_creation(self, server):
        """Test that the MCP server can be created without errors."""
        assert server is not None
        assert hasattr(server, "server")
        assert hasattr(server, "db")
        assert hasattr(server, "sync_service")
        assert hasattr(server, "intercom_client")

    @pytest.mark.asyncio
    async def test_server_has_required_attributes(self, server):
        """Test that server has all required attributes."""
        assert hasattr(server, "_list_tools")
        assert hasattr(server, "_call_tool")
        assert hasattr(server, "run")
        assert hasattr(server, "start_background_sync")
        assert hasattr(server, "stop_background_sync")

    @pytest.mark.asyncio
    async def test_server_tools_registration(self, server):
        """Test that tools are properly registered."""
        tools = await server._list_tools()
        assert isinstance(tools, list)

        # Check that expected tools are present
        tool_names = [tool.name for tool in tools]
        expected_tools = [
            "search_conversations",
            "get_conversation",
            "get_server_status",
            "sync_conversations",
            "get_data_info",
            "check_coverage",
            "get_sync_status",
            "force_sync",
        ]

        for expected_tool in expected_tools:
            assert expected_tool in tool_names, f"Missing tool: {expected_tool}"

    @pytest.mark.asyncio
    async def test_get_server_status_tool(self, server):
        """Test the get_server_status tool."""
        result = await server._call_tool("get_server_status", {})
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0].type == "text"
        assert "FastIntercom Server Status" in result[0].text

    @pytest.mark.asyncio
    async def test_get_sync_status_tool(self, server):
        """Test the get_sync_status tool."""
        with patch("sqlite3.connect") as mock_connect:
            # Mock database connection
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = None
            mock_conn.execute.return_value = mock_cursor
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = await server._call_tool("get_sync_status", {})
            assert isinstance(result, list)
            assert len(result) > 0
            assert result[0].type == "text"
            assert "is_syncing" in result[0].text

    @pytest.mark.asyncio
    async def test_get_data_info_tool(self, server):
        """Test the get_data_info tool."""
        with patch("sqlite3.connect") as mock_connect:
            # Mock database connection
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = None
            mock_conn.execute.return_value = mock_cursor
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = await server._call_tool("get_data_info", {})
            assert isinstance(result, list)
            assert len(result) > 0
            assert result[0].type == "text"
            assert "has_data" in result[0].text

    @pytest.mark.asyncio
    async def test_invalid_tool_call(self, server):
        """Test that invalid tool calls are handled gracefully."""
        result = await server._call_tool("invalid_tool", {})
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0].type == "text"
        assert "Unknown tool" in result[0].text

    @pytest.mark.asyncio
    async def test_search_conversations_tool(self, server):
        """Test the search_conversations tool."""
        result = await server._call_tool(
            "search_conversations", {"query": "test", "limit": 10}
        )
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0].type == "text"

    @pytest.mark.asyncio
    async def test_server_handles_exceptions_gracefully(self, server):
        """Test that server handles exceptions in tool calls gracefully."""
        # Mock the sync_service to raise an exception
        server.sync_service.sync_if_needed.side_effect = Exception("Test error")

        result = await server._call_tool("search_conversations", {"query": "test"})
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0].type == "text"
        # Should still return results, not crash

    @pytest.mark.asyncio
    async def test_timeframe_parsing(self, server):
        """Test that timeframe parsing works correctly."""
        # Test various timeframe formats
        test_cases = [
            "last 24 hours",
            "today",
            "last 7 days",
            "this week",
            "last 30 days",
            "this month",
            "last week",
            "yesterday",
        ]

        for timeframe in test_cases:
            start_date, end_date = server._parse_timeframe(timeframe)
            if timeframe not in [None, ""]:
                assert start_date is not None
                assert end_date is not None
                assert start_date <= end_date

    @pytest.mark.asyncio
    async def test_background_sync_lifecycle(self, server):
        """Test background sync start/stop lifecycle."""
        # These should not raise exceptions
        await server.start_background_sync()
        await server.stop_background_sync()

    @pytest.mark.asyncio
    async def test_periodic_sync_exception_handling(self, server):
        """Test that periodic sync handles exceptions gracefully."""
        # Mock sync_service to raise an exception
        server.sync_service.sync_period = AsyncMock(side_effect=Exception("Sync error"))

        # Start periodic sync task
        task = asyncio.create_task(server._periodic_sync())

        # Let it run briefly
        await asyncio.sleep(0.1)

        # Cancel the task
        task.cancel()

        # Should not raise an exception
        import contextlib
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def test_server_name_and_version(self, server):
        """Test that server has correct name and version info."""
        assert server.server.name == "fastintercom"
