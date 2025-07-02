"""Data models for FastIntercom MCP server."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote


@dataclass
class Message:
    """A message within an Intercom conversation."""

    id: str
    author_type: str  # 'user' | 'admin'
    body: str
    created_at: datetime
    part_type: str | None = None  # 'comment' | 'note' | 'message'


@dataclass
class Conversation:
    """An Intercom conversation with messages."""

    id: str
    created_at: datetime
    updated_at: datetime
    messages: list[Message]
    customer_email: str | None = None
    tags: list[str] = field(default_factory=list)

    def get_url(self, app_id: str) -> str:
        """Generate clickable Intercom URL for this conversation."""
        base_url = f"https://app.intercom.com/a/inbox/{app_id}/inbox/search/conversation/{self.id}"
        if self.customer_email:
            encoded_email = quote(self.customer_email)
            return f"{base_url}?query={encoded_email}"
        return base_url

    def get_customer_messages(self) -> list[Message]:
        """Get only messages from customers (not admins)."""
        return [msg for msg in self.messages if msg.author_type == "user"]

    def get_admin_messages(self) -> list[Message]:
        """Get only messages from admins."""
        return [msg for msg in self.messages if msg.author_type == "admin"]


@dataclass
class SyncPeriod:
    """Represents a time period that has been synced from Intercom."""

    start_timestamp: datetime
    end_timestamp: datetime
    last_synced: datetime
    conversation_count: int
    new_conversations: int = 0
    updated_conversations: int = 0


@dataclass
class SyncStats:
    """Statistics from a sync operation."""
    
    total_conversations: int = 0
    new_conversations: int = 0
    updated_conversations: int = 0
    total_messages: int = 0
    duration_seconds: float = 0.0
    api_calls_made: int = 0


@dataclass
class ConversationFilters:
    """Filters for searching conversations."""

    query: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    customer_email: str | None = None
    tags: list[str] | None = None
    limit: int = 100




@dataclass
class MCPTool:
    """Definition of an MCP tool."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class MCPRequest:
    """An incoming MCP request."""

    tool_name: str
    parameters: dict[str, Any]
    request_id: str | None = None


@dataclass
class MCPResponse:
    """Response to an MCP request."""

    success: bool
    data: Any = None
    error: str | None = None
    request_id: str | None = None
