"""Basic MCP protocol functionality tests."""

from unittest.mock import Mock

import pytest

from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.mcp_server import FastIntercomMCPServer


class TestBasicFunctionality:
    """Test basic MCP protocol functionality."""

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

    def test_tools_can_be_discovered(self, server):
        """Test that tools can be discovered through MCP protocol."""
        tools = await server._list_tools()

        assert isinstance(tools, list)
        assert len(tools) > 0

        # Verify each tool has required properties
        for tool in tools:
            assert hasattr(tool, "name")
            assert hasattr(tool, "description")
            assert hasattr(tool, "inputSchema")
            assert isinstance(tool.name, str)
            assert isinstance(tool.description, str)
            assert isinstance(tool.inputSchema, dict)

    @pytest.mark.asyncio
    async def test_basic_tool_execution(self, server):
        """Test basic tool execution works."""
        # Test server status tool
        result = await server._call_tool("get_server_status", {})
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0].type == "text"

        # Test search tool
        result = await server._call_tool("search_conversations", {"query": "test"})
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0].type == "text"

    @pytest.mark.asyncio
    async def test_error_handling(self, server):
        """Test that errors are handled properly."""
        # Test with invalid tool name
        result = await server._call_tool("nonexistent_tool", {})
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0].type == "text"
        assert "Unknown tool" in result[0].text