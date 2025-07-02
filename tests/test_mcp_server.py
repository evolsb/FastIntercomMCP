"""Basic MCP server startup tests."""

from unittest.mock import Mock

import pytest

from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.mcp_server import FastIntercomMCPServer


class TestMCPServer:
    """Test basic MCP server functionality."""

    @pytest.fixture
    def mock_database_manager(self):
        """Create a mock database manager."""
        mock_db = Mock(spec=DatabaseManager)
        mock_db.db_path = ":memory:"
        mock_db.get_sync_status.return_value = {
            "database_size_mb": 1.5,
            "total_conversations": 0,
            "total_messages": 0,
            "last_sync": None,
            "database_path": ":memory:",
            "recent_syncs": [],
        }
        mock_db.search_conversations.return_value = []
        mock_db.get_data_freshness_for_timeframe.return_value = 0
        mock_db.record_request_pattern = Mock()
        return mock_db

    @pytest.fixture
    def server(self, mock_database_manager):
        """Create a FastIntercomMCPServer instance for testing."""
        # Simplified architecture - no sync service or intercom client needed for basic tests
        return FastIntercomMCPServer(
            database_manager=mock_database_manager,
            sync_service=None,
            intercom_client=None,
        )

    def test_server_can_be_created(self, server):
        """Test that the MCP server can be created without errors."""
        assert server is not None
        assert hasattr(server, "server")
        assert hasattr(server, "db")
        # In simplified architecture, sync_service and intercom_client are optional
        assert hasattr(server, "sync_service")
        assert hasattr(server, "intercom_client")

    @pytest.mark.asyncio
    async def test_server_can_list_tools(self, server):
        """Test that server can list available tools."""
        tools = await server._list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    @pytest.mark.asyncio
    async def test_server_status_tool_works(self, server):
        """Test that the basic status tool works."""
        result = await server._call_tool("get_server_status", {})
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0].type == "text"
        assert "FastIntercom Server Status" in result[0].text