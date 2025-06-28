"""MCP protocol compliance and functionality tests."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.mcp_server import FastIntercomMCPServer
from fast_intercom_mcp.sync_service import SyncService


class TestMCPProtocol:
    """Test MCP protocol compliance and basic functionality."""

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
    async def test_tool_discovery(self, server):
        """Test that tools can be discovered through MCP protocol."""
        tools = await server._list_tools()

        assert isinstance(tools, list)
        assert len(tools) > 0

        # Verify each tool has required MCP Tool properties
        for tool in tools:
            assert hasattr(tool, "name")
            assert hasattr(tool, "description")
            assert hasattr(tool, "inputSchema")
            assert isinstance(tool.name, str)
            assert isinstance(tool.description, str)
            assert isinstance(tool.inputSchema, dict)

    @pytest.mark.asyncio
    async def test_tool_input_schemas(self, server):
        """Test that all tools have valid JSON Schema input definitions."""
        tools = await server._list_tools()

        for tool in tools:
            schema = tool.inputSchema

            # Must be a valid JSON Schema object
            assert "type" in schema
            assert schema["type"] == "object"

            # Should have properties defined
            assert "properties" in schema
            assert isinstance(schema["properties"], dict)

            # Validate specific tools
            if tool.name == "search_conversations":
                props = schema["properties"]
                assert "query" in props
                assert "timeframe" in props
                assert "customer_email" in props
                assert "limit" in props

            elif tool.name == "get_conversation":
                props = schema["properties"]
                assert "conversation_id" in props
                assert "required" in schema
                assert "conversation_id" in schema["required"]

    @pytest.mark.asyncio
    async def test_tool_execution_basic(self, server):
        """Test basic tool execution functionality."""
        # Test each registered tool
        tools = await server._list_tools()

        for tool in tools:
            tool_name = tool.name

            # Create minimal valid arguments based on schema
            args = {}
            if "required" in tool.inputSchema:
                for required_field in tool.inputSchema["required"]:
                    if required_field == "conversation_id":
                        args[required_field] = "test_conv_id"
                    elif required_field == "start_date":
                        args[required_field] = "2023-01-01"
                    elif required_field == "end_date":
                        args[required_field] = "2023-01-02"
                    else:
                        args[required_field] = "test_value"

            # Execute tool
            result = await server._call_tool(tool_name, args)

            # Verify result format
            assert isinstance(result, list)
            assert len(result) > 0

            for content in result:
                assert hasattr(content, "type")
                assert hasattr(content, "text")
                assert content.type == "text"
                assert isinstance(content.text, str)

    @pytest.mark.asyncio
    async def test_json_response_format(self, server):
        """Test that tools returning JSON data have valid format."""
        json_tools = ["get_data_info", "get_sync_status", "check_coverage"]

        for tool_name in json_tools:
            args = {}
            if tool_name == "check_coverage":
                args = {"start_date": "2023-01-01", "end_date": "2023-01-02"}

            with patch("sqlite3.connect") as mock_connect:
                # Mock database connection for tools that need it
                mock_conn = Mock()
                mock_cursor = Mock()
                mock_cursor.fetchone.return_value = None
                mock_conn.execute.return_value = mock_cursor
                mock_connect.return_value.__enter__.return_value = mock_conn

                result = await server._call_tool(tool_name, args)

                assert isinstance(result, list)
                assert len(result) > 0

                # Try to parse as JSON
                try:
                    json_data = json.loads(result[0].text)
                    assert isinstance(json_data, dict)
                except json.JSONDecodeError:
                    pytest.fail(f"Tool {tool_name} did not return valid JSON")

    @pytest.mark.asyncio
    async def test_tool_parameter_validation(self, server):
        """Test that tools properly validate input parameters."""
        # Test get_conversation with missing required parameter
        result = await server._call_tool("get_conversation", {})
        assert isinstance(result, list)
        assert len(result) > 0
        assert "conversation_id is required" in result[0].text

    @pytest.mark.asyncio
    async def test_error_handling_format(self, server):
        """Test that errors are returned in proper MCP format."""
        # Test with invalid tool name
        result = await server._call_tool("nonexistent_tool", {})

        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0].type == "text"
        assert "Unknown tool" in result[0].text

    @pytest.mark.asyncio
    async def test_tool_execution_with_exceptions(self, server):
        """Test that tool exceptions are handled properly."""
        # Mock database to raise an exception
        with patch("sqlite3.connect", side_effect=Exception("Database error")):
            result = await server._call_tool("get_data_info", {})

            assert isinstance(result, list)
            assert len(result) > 0
            assert result[0].type == "text"
            # Should contain error information but not crash
            assert "error" in result[0].text.lower()

    @pytest.mark.asyncio
    async def test_search_conversations_parameter_handling(self, server):
        """Test search_conversations handles various parameter combinations."""
        test_cases = [
            {},  # No parameters
            {"query": "test"},  # Query only
            {"timeframe": "last 7 days"},  # Timeframe only
            {"customer_email": "test@example.com"},  # Email only
            {"limit": 10},  # Limit only
            {
                "query": "test",
                "timeframe": "last 7 days",
                "limit": 5,
            },  # Multiple params
        ]

        for args in test_cases:
            result = await server._call_tool("search_conversations", args)
            assert isinstance(result, list)
            assert len(result) > 0
            assert result[0].type == "text"

    @pytest.mark.asyncio
    async def test_tool_response_consistency(self, server):
        """Test that tools return consistent response formats."""
        # Test the same tool multiple times
        for _ in range(3):
            result = await server._call_tool("get_server_status", {})

            assert isinstance(result, list)
            assert len(result) == 1  # Should always return exactly one TextContent
            assert result[0].type == "text"
            assert "FastIntercom Server Status" in result[0].text

    @pytest.mark.asyncio
    async def test_concurrent_tool_calls(self, server):
        """Test that server can handle concurrent tool calls."""
        import asyncio

        # Create multiple concurrent tool calls
        tasks = []
        for i in range(5):
            task = asyncio.create_task(server._call_tool("get_server_status", {}))
            tasks.append(task)

        # Wait for all to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Verify all succeeded
        for result in results:
            assert not isinstance(result, Exception)
            assert isinstance(result, list)
            assert len(result) > 0

    @pytest.mark.asyncio
    async def test_tool_descriptions_are_helpful(self, server):
        """Test that tool descriptions provide useful information."""
        tools = await server._list_tools()

        for tool in tools:
            # Description should be descriptive
            assert len(tool.description) > 20
            assert not tool.description.startswith("TODO")
            assert not tool.description.lower().startswith("test")

            # Should contain relevant keywords for the tool functionality
            desc_lower = tool.description.lower()
            if "search" in tool.name:
                assert any(word in desc_lower for word in ["search", "find", "query"])
            elif "status" in tool.name:
                assert any(
                    word in desc_lower
                    for word in ["status", "statistics", "info", "check", "get"]
                )
            elif "sync" in tool.name:
                assert any(
                    word in desc_lower
                    for word in ["sync", "synchronize", "update", "trigger"]
                )

    def test_server_initialization_requirements(self, server):
        """Test that server initialization has required components."""
        # Server should have all required dependencies
        assert server.db is not None
        assert server.sync_service is not None
        assert server.server is not None

        # Server should have a name
        assert hasattr(server.server, "name")
        assert server.server.name == "fastintercom"

    @pytest.mark.asyncio
    async def test_tool_schema_completeness(self, server):
        """Test that tool schemas are complete and valid."""
        tools = await server._list_tools()

        for tool in tools:
            schema = tool.inputSchema

            # Check for standard JSON Schema fields
            assert "type" in schema
            assert "properties" in schema

            # Check property definitions are complete
            for prop_name, prop_def in schema["properties"].items():
                assert "type" in prop_def
                assert "description" in prop_def
                assert len(prop_def["description"]) > 5  # Meaningful description
