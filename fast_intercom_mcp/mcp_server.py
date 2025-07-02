"""MCP server implementation for FastIntercom."""

import logging
from datetime import datetime, timedelta
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .database import DatabaseManager

logger = logging.getLogger(__name__)


class FastIntercomMCPServer:
    """MCP server for Intercom conversation access."""

    def __init__(
        self,
        database_manager: DatabaseManager,
        sync_service=None,
        intercom_client=None,
    ):
        self.db = database_manager
        self.sync_service = sync_service
        self.intercom_client = intercom_client
        self.server = Server("fastintercom")

        self._setup_tools()

    def _setup_tools(self):
        """Register MCP tools."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available MCP tools."""
            return [
                Tool(
                    name="search_conversations",
                    description="Search Intercom conversations with flexible filters",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Text to search for in conversation messages",
                            },
                            "timeframe": {
                                "type": "string",
                                "description": (
                                    "Time period like 'last 7 days', 'this month', 'last week'"
                                ),
                            },
                            "customer_email": {
                                "type": "string",
                                "description": "Filter by specific customer email address",
                            },
                            "limit": {
                                "type": "integer",
                                "description": (
                                    "Maximum number of conversations to return (default: 50)"
                                ),
                                "default": 50,
                            },
                        },
                    },
                ),
                Tool(
                    name="get_conversation",
                    description="Get full details of a specific conversation by ID",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "conversation_id": {
                                "type": "string",
                                "description": "The Intercom conversation ID",
                            }
                        },
                        "required": ["conversation_id"],
                    },
                ),
                Tool(
                    name="get_server_status",
                    description="Get FastIntercom server status and statistics",
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Handle MCP tool calls."""
            try:
                if name == "search_conversations":
                    return await self._search_conversations(arguments)
                if name == "get_conversation":
                    return await self._get_conversation(arguments)
                if name == "get_server_status":
                    return await self._get_server_status(arguments)
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
            except Exception as e:
                logger.error(f"Tool call error for {name}: {e}")
                return [TextContent(type="text", text=f"Error executing {name}: {str(e)}")]

    async def _search_conversations(self, args: dict[str, Any]) -> list[TextContent]:
        """Search conversations with filters."""
        query = args.get("query")
        timeframe = args.get("timeframe")
        customer_email = args.get("customer_email")
        limit = args.get("limit", 50)

        # Parse timeframe into dates
        start_date, end_date = self._parse_timeframe(timeframe)

        # Search conversations directly in database (no complex sync)
        conversations = self.db.search_conversations(
            query=query,
            start_date=start_date,
            end_date=end_date,
            customer_email=customer_email,
            limit=limit,
        )

        if not conversations:
            return [
                TextContent(
                    type="text",
                    text="No conversations found matching your criteria. Try broadening your search or checking if data has been synced.",
                )
            ]

        # Format results
        results = []
        for conv in conversations:
            messages_preview = conv.messages[:3]  # Show first 3 messages
            preview_text = "\n".join([f"  {msg.author}: {msg.body[:100]}..." for msg in messages_preview])
            
            customer_info = "Unknown"
            if conv.customer_email:
                customer_info = conv.customer_email
            elif hasattr(conv, 'customer_name') and conv.customer_name:
                customer_info = conv.customer_name

            result_text = f"""
**Conversation {conv.id}**
- Customer: {customer_info}
- Created: {conv.created_at.strftime('%Y-%m-%d %H:%M')}
- Updated: {conv.updated_at.strftime('%Y-%m-%d %H:%M')}
- State: {conv.state}
- Messages: {len(conv.messages)}

**Preview:**
{preview_text}
---
"""
            results.append(result_text.strip())

        response_text = f"Found {len(conversations)} conversations:\n\n" + "\n\n".join(results)
        
        return [TextContent(type="text", text=response_text)]

    async def _get_conversation(self, args: dict[str, Any]) -> list[TextContent]:
        """Get full details of a specific conversation."""
        conversation_id = args.get("conversation_id")
        
        if not conversation_id:
            return [TextContent(type="text", text="Error: conversation_id is required")]

        # Get conversation from database
        conversation = self.db.get_conversation(conversation_id)
        
        if not conversation:
            return [TextContent(type="text", text=f"Conversation {conversation_id} not found")]

        # Format full conversation
        customer_info = "Unknown"
        if conversation.customer_email:
            customer_info = conversation.customer_email
        elif hasattr(conversation, 'customer_name') and conversation.customer_name:
            customer_info = conversation.customer_name

        messages_text = []
        for msg in conversation.messages:
            timestamp = msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
            messages_text.append(f"[{timestamp}] {msg.author}: {msg.body}")

        full_text = f"""
**Conversation {conversation.id}**

**Details:**
- Customer: {customer_info}
- Created: {conversation.created_at.strftime('%Y-%m-%d %H:%M:%S')}
- Updated: {conversation.updated_at.strftime('%Y-%m-%d %H:%M:%S')}
- State: {conversation.state}
- Total Messages: {len(conversation.messages)}

**Messages:**
{chr(10).join(messages_text)}
"""
        return [TextContent(type="text", text=full_text.strip())]

    async def _get_server_status(self, args: dict[str, Any]) -> list[TextContent]:
        """Get server status and statistics."""
        try:
            status = self.db.get_sync_status()
            
            status_text = f"""
**FastIntercom Server Status**

📊 **Database Statistics:**
- Conversations: {status.get('total_conversations', 0):,}
- Messages: {status.get('total_messages', 0):,}
- Database Size: {status.get('database_size_mb', 0)} MB

📁 **Storage:**
- Database Path: {status.get('database_path', 'Unknown')}

⚡ **Server Info:**
- Mode: Simplified MCP Server
- Transport: stdio
- Status: Running
"""
            
            return [TextContent(type="text", text=status_text.strip())]
        except Exception as e:
            return [TextContent(type="text", text=f"Error getting status: {str(e)}")]

    def _parse_timeframe(self, timeframe: str | None) -> tuple[datetime | None, datetime | None]:
        """Parse timeframe string into start and end dates."""
        if not timeframe:
            return None, None
            
        now = datetime.now()
        timeframe = timeframe.lower().strip()
        
        try:
            if "last" in timeframe and "day" in timeframe:
                if "7" in timeframe or "week" in timeframe:
                    return now - timedelta(days=7), now
                elif "30" in timeframe:
                    return now - timedelta(days=30), now
                else:
                    return now - timedelta(days=1), now
            elif "this month" in timeframe:
                start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                return start_of_month, now
            elif "last week" in timeframe:
                return now - timedelta(days=7), now
            else:
                # Default to last 7 days for unrecognized timeframes
                return now - timedelta(days=7), now
        except Exception:
            # Fallback
            return now - timedelta(days=7), now

    async def run(self):
        """Run the MCP server."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream, write_stream, self.server.create_initialization_options()
            )

    async def _list_tools(self) -> list[Tool]:
        """List tools for external access."""
        return await self.server._list_tools()

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Call tool for external access."""
        return await self.server._call_tool(name, arguments)