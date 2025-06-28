"""MCP server implementation for FastIntercom."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .database import DatabaseManager
from .sync_service import SyncService

logger = logging.getLogger(__name__)


class FastIntercomMCPServer:
    """MCP server for Intercom conversation access."""

    def __init__(
        self,
        database_manager: DatabaseManager,
        sync_service: SyncService,
        intercom_client=None,
    ):
        self.db = database_manager
        self.sync_service = sync_service
        self.intercom_client = intercom_client
        self.server = Server("fastintercom")

        # Don't initialize background sync service for stdio mode
        # We'll handle sync manually to avoid lifecycle conflicts
        self.background_sync = None

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
                                "description": "Time period like 'last 7 days', 'this month', 'last week'",
                            },
                            "customer_email": {
                                "type": "string",
                                "description": "Filter by specific customer email address",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of conversations to return (default: 50)",
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
                Tool(
                    name="sync_conversations",
                    description="Trigger manual sync of recent conversations",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "force": {
                                "type": "boolean",
                                "description": "Force full sync even if recent data exists",
                                "default": False,
                            }
                        },
                    },
                ),
                Tool(
                    name="get_data_info",
                    description="Get information about cached data freshness and coverage",
                    inputSchema={"type": "object", "properties": {}, "required": []},
                ),
                Tool(
                    name="check_coverage",
                    description="Check if cached data covers a specific date range",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "start_date": {
                                "type": "string",
                                "description": "Start date in ISO format (YYYY-MM-DD or full ISO timestamp)",
                            },
                            "end_date": {
                                "type": "string",
                                "description": "End date in ISO format (YYYY-MM-DD or full ISO timestamp)",
                            },
                        },
                        "required": ["start_date", "end_date"],
                    },
                ),
                Tool(
                    name="get_sync_status",
                    description="Check if a sync is currently in progress",
                    inputSchema={"type": "object", "properties": {}, "required": []},
                ),
                Tool(
                    name="force_sync",
                    description="Force an immediate sync operation",
                    inputSchema={"type": "object", "properties": {}, "required": []},
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
                if name == "sync_conversations":
                    return await self._sync_conversations(arguments)
                if name == "get_data_info":
                    return await self._get_data_info(arguments)
                if name == "check_coverage":
                    return await self._check_coverage(arguments)
                if name == "get_sync_status":
                    return await self._get_sync_status_tool(arguments)
                if name == "force_sync":
                    return await self._force_sync_tool(arguments)
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
            except Exception as e:
                logger.error(f"Tool call error for {name}: {e}")
                return [
                    TextContent(type="text", text=f"Error executing {name}: {str(e)}")
                ]

    async def _get_data_info(self, args: dict[str, Any]) -> list[TextContent]:
        """Get information about cached data freshness and coverage."""
        try:
            import sqlite3
            from pathlib import Path

            with sqlite3.connect(self.db.db_path) as conn:
                conn.row_factory = sqlite3.Row

                # Query the most recent successful sync
                result = conn.execute("""
                    SELECT
                        sync_completed_at,
                        coverage_start_date,
                        coverage_end_date,
                        total_conversations,
                        total_messages,
                        sync_type
                    FROM sync_metadata
                    WHERE sync_status = 'completed'
                    ORDER BY sync_completed_at DESC
                    LIMIT 1
                """).fetchone()

                if not result:
                    response = {
                        "has_data": False,
                        "message": "No successful sync found",
                    }
                else:
                    last_sync = datetime.fromisoformat(result["sync_completed_at"])
                    data_age_minutes = int(
                        (datetime.now() - last_sync).total_seconds() / 60
                    )

                    # Get database size
                    db_path = Path(self.db.db_path)
                    db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)

                    response = {
                        "has_data": True,
                        "last_sync": last_sync.isoformat(),
                        "data_age_minutes": data_age_minutes,
                        "coverage_start": result["coverage_start_date"],
                        "coverage_end": result["coverage_end_date"],
                        "total_conversations": result["total_conversations"],
                        "total_messages": result["total_messages"],
                        "sync_type": result["sync_type"],
                        "database_size_mb": db_size_mb,
                    }

                return [TextContent(type="text", text=json.dumps(response, indent=2))]

        except Exception as e:
            logger.error(f"Error getting data info: {e}")
            response = {"has_data": False, "error": str(e)}
            return [TextContent(type="text", text=json.dumps(response, indent=2))]

    async def _check_coverage(self, args: dict[str, Any]) -> list[TextContent]:
        """Check if cached data covers the requested date range."""
        try:
            start_date_str = args.get("start_date")
            end_date_str = args.get("end_date")

            if not start_date_str or not end_date_str:
                response = {
                    "has_coverage": False,
                    "error": "Both start_date and end_date are required",
                }
                return [TextContent(type="text", text=json.dumps(response, indent=2))]

            query_start = datetime.fromisoformat(start_date_str).date()
            query_end = datetime.fromisoformat(end_date_str).date()

            import sqlite3

            with sqlite3.connect(self.db.db_path) as conn:
                conn.row_factory = sqlite3.Row

                # Get the most recent sync info
                result = conn.execute("""
                    SELECT
                        sync_completed_at,
                        coverage_start_date,
                        coverage_end_date
                    FROM sync_metadata
                    WHERE sync_status = 'completed'
                    ORDER BY sync_completed_at DESC
                    LIMIT 1
                """).fetchone()

                if not result:
                    response = {
                        "has_coverage": False,
                        "reason": "No data available",
                        "coverage_gaps": [(start_date_str, end_date_str)],
                    }
                else:
                    coverage_start = datetime.fromisoformat(
                        result["coverage_start_date"]
                    ).date()
                    coverage_end = datetime.fromisoformat(
                        result["coverage_end_date"]
                    ).date()
                    data_age_minutes = int(
                        (
                            datetime.now()
                            - datetime.fromisoformat(result["sync_completed_at"])
                        ).total_seconds()
                        / 60
                    )

                    # Check if query range is within coverage
                    has_full_coverage = (
                        query_start >= coverage_start and query_end <= coverage_end
                    )

                    # Calculate gaps if any
                    coverage_gaps = []
                    if query_start < coverage_start:
                        coverage_gaps.append(
                            (query_start.isoformat(), coverage_start.isoformat())
                        )
                    if query_end > coverage_end:
                        coverage_gaps.append(
                            (coverage_end.isoformat(), query_end.isoformat())
                        )

                    response = {
                        "has_coverage": has_full_coverage,
                        "partial_coverage": len(coverage_gaps) > 0
                        and query_start <= coverage_end
                        and query_end >= coverage_start,
                        "coverage_gaps": coverage_gaps,
                        "cached_range": {
                            "start": coverage_start.isoformat(),
                            "end": coverage_end.isoformat(),
                        },
                        "data_age_minutes": data_age_minutes,
                        "reason": "Full coverage"
                        if has_full_coverage
                        else f"Missing data for {len(coverage_gaps)} date ranges",
                    }

                return [TextContent(type="text", text=json.dumps(response, indent=2))]

        except Exception as e:
            logger.error(f"Error checking coverage: {e}")
            response = {"has_coverage": False, "error": str(e)}
            return [TextContent(type="text", text=json.dumps(response, indent=2))]

    async def _get_sync_status_tool(self, args: dict[str, Any]) -> list[TextContent]:
        """Check if a sync is currently in progress."""
        try:
            import sqlite3

            with sqlite3.connect(self.db.db_path) as conn:
                conn.row_factory = sqlite3.Row

                # Check for in-progress syncs
                in_progress = conn.execute("""
                    SELECT sync_started_at, coverage_start_date, coverage_end_date
                    FROM sync_metadata
                    WHERE sync_status = 'in_progress'
                    ORDER BY sync_started_at DESC
                    LIMIT 1
                """).fetchone()

                if in_progress:
                    duration_minutes = int(
                        (
                            datetime.now()
                            - datetime.fromisoformat(in_progress["sync_started_at"])
                        ).total_seconds()
                        / 60
                    )
                    response = {
                        "is_syncing": True,
                        "sync_started_at": in_progress["sync_started_at"],
                        "duration_minutes": duration_minutes,
                        "coverage_dates": {
                            "start": in_progress["coverage_start_date"],
                            "end": in_progress["coverage_end_date"],
                        },
                    }
                else:
                    response = {"is_syncing": False}

                return [TextContent(type="text", text=json.dumps(response, indent=2))]

        except Exception as e:
            response = {"is_syncing": False, "error": str(e)}
            return [TextContent(type="text", text=json.dumps(response, indent=2))]

    async def _force_sync_tool(self, args: dict[str, Any]) -> list[TextContent]:
        """Force an immediate sync operation."""
        try:
            if self.background_sync:
                success = await self.background_sync.force_sync()
                response = {
                    "success": success,
                    "message": "Sync completed successfully"
                    if success
                    else "Sync failed",
                }
            else:
                response = {
                    "success": False,
                    "error": "Background sync service not available",
                }
            return [TextContent(type="text", text=json.dumps(response, indent=2))]
        except Exception as e:
            response = {"success": False, "error": str(e)}
            return [TextContent(type="text", text=json.dumps(response, indent=2))]

    async def _search_conversations(self, args: dict[str, Any]) -> list[TextContent]:
        """Search conversations with filters."""
        query = args.get("query")
        timeframe = args.get("timeframe")
        customer_email = args.get("customer_email")
        limit = args.get("limit", 50)

        # Parse timeframe into dates
        start_date, end_date = self._parse_timeframe(timeframe)

        # Use smart sync state logic
        sync_info = None
        try:
            sync_info = await self.sync_service.sync_if_needed(start_date, end_date)
        except Exception as e:
            # If sync fails, still try to return what data we have
            logger.error(f"Sync failed: {e}")
            sync_info = {
                "sync_state": "error",
                "message": f"Sync failed: {str(e)}",
                "data_complete": False,
            }

        # Record this request pattern for future optimization
        data_freshness_seconds = 0
        if start_date and end_date:
            data_freshness_seconds = self.db.get_data_freshness_for_timeframe(
                start_date, end_date
            )

        self.db.record_request_pattern(
            start_date or datetime.now() - timedelta(hours=1),
            end_date or datetime.now(),
            data_freshness_seconds,
            sync_info.get("sync_state") == "fresh" if sync_info else False,
        )

        # Search conversations
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
                    type="text", text="No conversations found matching the criteria."
                )
            ]

        # Format results
        result_text = f"Found {len(conversations)} conversations:\n\n"

        for conv in conversations:
            customer_messages = conv.get_customer_messages()
            admin_messages = conv.get_admin_messages()

            result_text += f"**Conversation {conv.id}**\n"
            result_text += f"- Customer: {conv.customer_email or 'Unknown'}\n"
            result_text += f"- Created: {conv.created_at.strftime('%Y-%m-%d %H:%M')}\n"
            result_text += f"- Messages: {len(customer_messages)} from customer, {len(admin_messages)} from support\n"

            if conv.tags:
                result_text += f"- Tags: {', '.join(conv.tags)}\n"

            # Show first customer message preview
            if customer_messages:
                preview = customer_messages[0].body[:200]
                if len(customer_messages[0].body) > 200:
                    preview += "..."
                result_text += f"- Preview: {preview}\n"

            # Add Intercom URL if available
            app_id = await self._get_app_id()
            if app_id:
                result_text += f"- [View in Intercom]({conv.get_url(app_id)})\n"

            result_text += "\n"

        # Add sync state information
        if sync_info:
            result_text += "\n---\n**Data Freshness Status:**\n"
            sync_state = sync_info.get("sync_state", "unknown")

            if sync_state == "fresh":
                result_text += "✅ Data is current and complete\n"
            elif sync_state == "partial":
                result_text += (
                    f"⚠️ {sync_info.get('message', 'Data may be incomplete')}\n"
                )
            elif sync_state == "stale":
                result_text += f"🔄 {sync_info.get('message', 'Data was refreshed')}\n"
            elif sync_state == "error":
                result_text += f"❌ {sync_info.get('message', 'Sync error occurred')}\n"

            if sync_info.get("last_sync"):
                last_sync = sync_info["last_sync"]
                if isinstance(last_sync, str):
                    result_text += f"Last sync: {last_sync}\n"
                else:
                    result_text += (
                        f"Last sync: {last_sync.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    )

        return [TextContent(type="text", text=result_text)]

    async def _get_conversation(self, args: dict[str, Any]) -> list[TextContent]:
        """Get full conversation details."""
        conversation_id = args.get("conversation_id")

        if not conversation_id:
            return [TextContent(type="text", text="Error: conversation_id is required")]

        # Search for the specific conversation
        conversations = self.db.search_conversations(limit=1)
        conversation = None

        for conv in conversations:
            if conv.id == conversation_id:
                conversation = conv
                break

        if not conversation:
            return [
                TextContent(
                    type="text",
                    text=f"Conversation {conversation_id} not found in local database.",
                )
            ]

        # Format full conversation
        result_text = f"# Conversation {conversation.id}\n\n"
        result_text += f"**Customer:** {conversation.customer_email or 'Unknown'}\n"
        result_text += (
            f"**Created:** {conversation.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
        )
        result_text += f"**Last Updated:** {conversation.updated_at.strftime('%Y-%m-%d %H:%M UTC')}\n"

        if conversation.tags:
            result_text += f"**Tags:** {', '.join(conversation.tags)}\n"

        app_id = await self._get_app_id()
        if app_id:
            result_text += f"**[View in Intercom]({conversation.get_url(app_id)})**\n"

        result_text += "\n## Messages\n\n"

        for i, message in enumerate(conversation.messages, 1):
            author = "👤 Customer" if message.author_type == "user" else "🎧 Support"
            timestamp = message.created_at.strftime("%m/%d %H:%M")

            result_text += f"### {i}. {author} ({timestamp})\n\n"
            result_text += f"{message.body}\n\n"

        return [TextContent(type="text", text=result_text)]

    async def _get_server_status(self, args: dict[str, Any]) -> list[TextContent]:
        """Get server status and statistics."""
        status = self.db.get_sync_status()
        sync_status = self.sync_service.get_status()

        result_text = "# FastIntercom Server Status\n\n"
        result_text += f"📊 **Storage:** {status['database_size_mb']} MB\n"
        result_text += f"💬 **Conversations:** {status['total_conversations']:,}\n"
        result_text += f"✉️ **Messages:** {status['total_messages']:,}\n"

        if status["last_sync"]:
            last_sync = datetime.fromisoformat(status["last_sync"])
            minutes_ago = int((datetime.now() - last_sync).total_seconds() / 60)
            result_text += f"🕒 **Last Sync:** {minutes_ago} minutes ago\n"
        else:
            result_text += "🕒 **Last Sync:** Never\n"

        result_text += f"🔄 **Background Sync:** {'Active' if sync_status['active'] else 'Inactive'}\n"

        if sync_status.get("current_operation"):
            result_text += (
                f"⚡ **Current Operation:** {sync_status['current_operation']}\n"
            )

        result_text += f"\n📁 **Database:** `{status['database_path']}`\n"

        # Recent sync activity
        if status["recent_syncs"]:
            result_text += "\n## Recent Sync Activity\n\n"
            for sync in status["recent_syncs"][:3]:
                sync_time = datetime.fromisoformat(sync["last_synced"])
                result_text += f"- {sync_time.strftime('%m/%d %H:%M')}: "
                result_text += f"{sync['conversation_count']} conversations "
                result_text += f"({sync.get('new_conversations', 0)} new)\n"

        return [TextContent(type="text", text=result_text)]

    async def _sync_conversations(self, args: dict[str, Any]) -> list[TextContent]:
        """Trigger manual sync."""
        force = args.get("force", False)

        result_text = "🔄 Starting manual sync...\n\n"

        try:
            if force:
                # Force sync last 7 days
                end_date = datetime.now()
                start_date = end_date - timedelta(days=7)
                stats = await self.sync_service.sync_period(start_date, end_date)
            else:
                # Incremental sync
                stats = await self.sync_service.sync_recent()

            result_text += "✅ **Sync completed successfully!**\n\n"
            result_text += "📊 **Results:**\n"
            result_text += f"- Total conversations: {stats.total_conversations}\n"
            result_text += f"- New conversations: {stats.new_conversations}\n"
            result_text += f"- Updated conversations: {stats.updated_conversations}\n"
            result_text += f"- Total messages: {stats.total_messages}\n"
            result_text += f"- Duration: {stats.duration_seconds:.1f} seconds\n"
            result_text += f"- API calls: {stats.api_calls_made}\n"

            if stats.errors_encountered > 0:
                result_text += f"- ⚠️ Errors: {stats.errors_encountered}\n"

        except Exception as e:
            result_text += f"❌ **Sync failed:** {str(e)}\n"

        return [TextContent(type="text", text=result_text)]

    def _parse_timeframe(
        self, timeframe: str | None
    ) -> tuple[datetime | None, datetime | None]:
        """Parse natural language timeframe into start/end dates."""
        if not timeframe:
            return None, None

        timeframe = timeframe.lower().strip()
        now = datetime.now()

        if "last 24 hours" in timeframe or "today" in timeframe:
            return now - timedelta(days=1), now
        if "last 7 days" in timeframe or "this week" in timeframe:
            return now - timedelta(days=7), now
        if "last 30 days" in timeframe or "this month" in timeframe:
            return now - timedelta(days=30), now
        if "last week" in timeframe:
            # Previous week (Monday to Sunday)
            days_since_monday = now.weekday()
            last_monday = now - timedelta(days=days_since_monday + 7)
            last_sunday = last_monday + timedelta(days=6)
            return last_monday, last_sunday
        if "yesterday" in timeframe:
            yesterday = now - timedelta(days=1)
            start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
            end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
            return start, end

        # Default to last 7 days if we can't parse
        return now - timedelta(days=7), now

    async def _smart_background_sync(self, start_date: datetime, end_date: datetime):
        """Perform intelligent background sync for a specific timeframe.

        This runs in the background and doesn't block the current request.
        The synced data will be available for the next request.
        """
        try:
            logger.info(
                f"Starting smart background sync for {start_date} to {end_date}"
            )

            # Use incremental sync if the timeframe is recent (last 24 hours)
            now = datetime.now()
            if (now - end_date).total_seconds() < 86400:  # 24 hours
                # Incremental sync for recent data
                await self.sync_service.sync_incremental(start_date)
            else:
                # Full period sync for older data
                await self.sync_service.sync_period(
                    start_date, end_date, is_background=True
                )

            logger.info(
                f"Smart background sync completed for {start_date} to {end_date}"
            )

        except Exception as e:
            logger.warning(
                f"Smart background sync failed for {start_date} to {end_date}: {e}"
            )

    async def _get_app_id(self) -> str | None:
        """Get Intercom app ID for URL generation."""
        # This would normally come from the sync service or be cached
        return getattr(self.sync_service, "app_id", None)

    async def start_background_sync(self):
        """Start the background sync service."""
        if self.background_sync:
            await self.background_sync.start()
            logger.info("Background sync service started")

    async def stop_background_sync(self):
        """Stop the background sync service."""
        if self.background_sync:
            await self.background_sync.stop()
            logger.info("Background sync service stopped")

    async def _list_tools(self):
        """Internal method to get tools list for testing."""
        # Return the tools directly since they're defined in _setup_tools
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
                            "description": "Time period like 'last 7 days', 'this month', 'last week'",
                        },
                        "customer_email": {
                            "type": "string",
                            "description": "Filter by specific customer email address",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of conversations to return (default: 50)",
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
            Tool(
                name="sync_conversations",
                description="Trigger manual sync of recent conversations",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "force": {
                            "type": "boolean",
                            "description": "Force full sync even if recent data exists",
                            "default": False,
                        }
                    },
                },
            ),
            Tool(
                name="get_data_info",
                description="Get information about cached data freshness and coverage",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="check_coverage",
                description="Check if cached data covers a specific date range",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "start_date": {
                            "type": "string",
                            "description": "Start date in ISO format (YYYY-MM-DD or full ISO timestamp)",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date in ISO format (YYYY-MM-DD or full ISO timestamp)",
                        },
                    },
                    "required": ["start_date", "end_date"],
                },
            ),
            Tool(
                name="get_sync_status",
                description="Check if a sync is currently in progress",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="force_sync",
                description="Force an immediate sync operation",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
        ]

    async def _call_tool(self, name: str, arguments: dict[str, Any]):
        """Internal method to call a tool for testing."""
        try:
            if name == "search_conversations":
                return await self._search_conversations(arguments)
            if name == "get_conversation":
                return await self._get_conversation(arguments)
            if name == "get_server_status":
                return await self._get_server_status(arguments)
            if name == "sync_conversations":
                return await self._sync_conversations(arguments)
            if name == "get_data_info":
                return await self._get_data_info(arguments)
            if name == "check_coverage":
                return await self._check_coverage(arguments)
            if name == "get_sync_status":
                return await self._get_sync_status_tool(arguments)
            if name == "force_sync":
                return await self._force_sync_tool(arguments)
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        except Exception as e:
            logger.error(f"Tool call error for {name}: {e}")
            return [TextContent(type="text", text=f"Error executing {name}: {str(e)}")]

    async def run(self):
        """Run the MCP server with simplified architecture."""
        logger.info("Starting FastIntercom MCP server...")

        # Start a simple periodic sync task that won't interfere
        sync_task = asyncio.create_task(self._periodic_sync())
        logger.info("Background sync service started")

        try:
            # Run MCP server - this should block indefinitely
            async with stdio_server() as (read_stream, write_stream):
                logger.info("MCP server listening for requests...")
                await self.server.run(
                    read_stream,
                    write_stream,
                    self.server.create_initialization_options(),
                )
        except KeyboardInterrupt:
            logger.info("MCP server shutdown requested")
        except Exception as e:
            logger.error(f"MCP server error: {e}")
        finally:
            # Clean up sync task
            if sync_task and not sync_task.done():
                sync_task.cancel()

    async def _periodic_sync(self):
        """Simple periodic sync that doesn't interfere with MCP server."""
        # Wait a bit before starting first sync
        await asyncio.sleep(10)

        while True:
            try:
                logger.info("Starting periodic sync...")

                # Perform a simple recent sync
                if self.intercom_client:
                    from datetime import datetime, timedelta

                    end_date = datetime.now()
                    start_date = end_date - timedelta(hours=1)  # Just sync last hour

                    # Simple sync without complex state management
                    await self.sync_service.sync_period(start_date, end_date)
                    logger.info("Periodic sync completed")

                # Wait 15 minutes for next sync
                await asyncio.sleep(15 * 60)

            except asyncio.CancelledError:
                logger.info("Periodic sync cancelled")
                break
            except Exception as e:
                logger.error(f"Periodic sync error (non-fatal): {e}")
                # Wait a bit before retrying
                await asyncio.sleep(60)
