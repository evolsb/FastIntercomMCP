"""Conversation-level sync state tracking for efficient incremental updates."""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from ..database import DatabaseManager

logger = logging.getLogger(__name__)


class SyncStatus(Enum):
    """Sync status enumeration."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class SyncType(Enum):
    """Sync type enumeration."""

    FULL = "full"
    INCREMENTAL = "incremental"
    THREAD_ONLY = "thread_only"
    METADATA_ONLY = "metadata_only"


@dataclass
class ConversationSyncState:
    """Represents the sync state for a single conversation."""

    conversation_id: str
    last_full_sync: datetime | None = None
    last_incremental_sync: datetime | None = None
    last_sync_attempt: datetime | None = None
    sync_status: SyncStatus = SyncStatus.PENDING
    sync_type: SyncType | None = None
    message_count_synced: int = 0
    last_message_timestamp: datetime | None = None
    error_count: int = 0
    last_error: str | None = None
    last_error_timestamp: datetime | None = None
    sync_completion_percentage: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def needs_full_sync(self, staleness_threshold_hours: int = 24) -> bool:
        """Check if conversation needs a full sync."""
        if self.last_full_sync is None:
            return True

        threshold = datetime.now() - timedelta(hours=staleness_threshold_hours)
        return self.last_full_sync < threshold

    def needs_incremental_sync(self, staleness_threshold_minutes: int = 30) -> bool:
        """Check if conversation needs an incremental sync."""
        if self.last_incremental_sync is None:
            return True

        threshold = datetime.now() - timedelta(minutes=staleness_threshold_minutes)
        return self.last_incremental_sync < threshold

    def is_sync_in_progress(self) -> bool:
        """Check if sync is currently in progress."""
        return self.sync_status == SyncStatus.IN_PROGRESS

    def has_recent_errors(self, error_threshold_hours: int = 1) -> bool:
        """Check if conversation has had recent sync errors."""
        if self.last_error_timestamp is None:
            return False

        threshold = datetime.now() - timedelta(hours=error_threshold_hours)
        return self.last_error_timestamp > threshold


class ConversationSyncTracker:
    """Tracks and manages sync state for individual conversations."""

    def __init__(self, database_manager: DatabaseManager):
        self.db = database_manager
        self._init_sync_state_schema()

    def _init_sync_state_schema(self):
        """Initialize database schema for conversation sync state tracking."""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")

            # Conversation sync state table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_sync_state (
                    conversation_id TEXT PRIMARY KEY,
                    last_full_sync TIMESTAMP,
                    last_incremental_sync TIMESTAMP,
                    last_sync_attempt TIMESTAMP,
                    sync_status TEXT NOT NULL DEFAULT 'pending',
                    sync_type TEXT,
                    message_count_synced INTEGER DEFAULT 0,
                    last_message_timestamp TIMESTAMP,
                    error_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    last_error_timestamp TIMESTAMP,
                    sync_completion_percentage REAL DEFAULT 0.0,
                    metadata TEXT, -- JSON
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
                )
            """)

            # Sync attempts log for detailed tracking and debugging
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_sync_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    sync_type TEXT NOT NULL,
                    sync_status TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    duration_seconds REAL,
                    messages_before INTEGER DEFAULT 0,
                    messages_after INTEGER DEFAULT 0,
                    api_calls_made INTEGER DEFAULT 0,
                    error_message TEXT,
                    metadata TEXT, -- JSON
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
                )
            """)

            # Create indexes for efficient queries
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_state_last_full ON conversation_sync_state (last_full_sync)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_state_last_incremental ON conversation_sync_state (last_incremental_sync)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_state_status ON conversation_sync_state (sync_status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_attempts_conversation ON conversation_sync_attempts (conversation_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_attempts_started ON conversation_sync_attempts (started_at)"
            )

            conn.commit()

    def get_conversation_sync_state(
        self, conversation_id: str
    ) -> ConversationSyncState:
        """Get sync state for a specific conversation."""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                """
                SELECT * FROM conversation_sync_state
                WHERE conversation_id = ?
            """,
                (conversation_id,),
            )

            row = cursor.fetchone()

            if row is None:
                # Create initial state
                return ConversationSyncState(conversation_id=conversation_id)

            return self._row_to_sync_state(row)

    def update_conversation_sync_state(self, state: ConversationSyncState):
        """Update sync state for a conversation."""
        import json

        with sqlite3.connect(self.db.db_path) as conn:
            # Convert metadata to JSON
            metadata_json = json.dumps(state.metadata) if state.metadata else "{}"

            conn.execute(
                """
                INSERT OR REPLACE INTO conversation_sync_state (
                    conversation_id, last_full_sync, last_incremental_sync,
                    last_sync_attempt, sync_status, sync_type, message_count_synced,
                    last_message_timestamp, error_count, last_error, last_error_timestamp,
                    sync_completion_percentage, metadata, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (
                    state.conversation_id,
                    state.last_full_sync.isoformat() if state.last_full_sync else None,
                    state.last_incremental_sync.isoformat()
                    if state.last_incremental_sync
                    else None,
                    state.last_sync_attempt.isoformat()
                    if state.last_sync_attempt
                    else None,
                    state.sync_status.value,
                    state.sync_type.value if state.sync_type else None,
                    state.message_count_synced,
                    state.last_message_timestamp.isoformat()
                    if state.last_message_timestamp
                    else None,
                    state.error_count,
                    state.last_error,
                    state.last_error_timestamp.isoformat()
                    if state.last_error_timestamp
                    else None,
                    state.sync_completion_percentage,
                    metadata_json,
                ),
            )

            conn.commit()

    def log_sync_attempt(
        self,
        conversation_id: str,
        sync_type: SyncType,
        sync_status: SyncStatus,
        started_at: datetime,
        completed_at: datetime | None = None,
        messages_before: int = 0,
        messages_after: int = 0,
        api_calls_made: int = 0,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Log a sync attempt for detailed tracking."""
        import json

        duration_seconds = None
        if completed_at and started_at:
            duration_seconds = (completed_at - started_at).total_seconds()

        metadata_json = json.dumps(metadata or {})

        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO conversation_sync_attempts (
                    conversation_id, sync_type, sync_status, started_at, completed_at,
                    duration_seconds, messages_before, messages_after, api_calls_made,
                    error_message, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    conversation_id,
                    sync_type.value,
                    sync_status.value,
                    started_at.isoformat(),
                    completed_at.isoformat() if completed_at else None,
                    duration_seconds,
                    messages_before,
                    messages_after,
                    api_calls_made,
                    error_message,
                    metadata_json,
                ),
            )

            conn.commit()
            return cursor.lastrowid

    def get_conversations_needing_full_sync(
        self, staleness_threshold_hours: int = 24, limit: int = 100
    ) -> list[str]:
        """Get conversations that need a full sync."""
        threshold = datetime.now() - timedelta(hours=staleness_threshold_hours)
        threshold_iso = threshold.isoformat()

        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT c.id
                FROM conversations c
                LEFT JOIN conversation_sync_state css ON c.id = css.conversation_id
                WHERE css.last_full_sync IS NULL
                   OR css.last_full_sync < ?
                   OR css.sync_status = 'failed'
                ORDER BY
                    CASE WHEN css.last_full_sync IS NULL THEN 0 ELSE 1 END,
                    css.last_full_sync ASC
                LIMIT ?
            """,
                (threshold_iso, limit),
            )

            return [row[0] for row in cursor.fetchall()]

    def get_conversations_needing_incremental_sync(
        self, staleness_threshold_minutes: int = 30, limit: int = 200
    ) -> list[str]:
        """Get conversations that need an incremental sync."""
        threshold = datetime.now() - timedelta(minutes=staleness_threshold_minutes)
        threshold_iso = threshold.isoformat()

        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT c.id
                FROM conversations c
                LEFT JOIN conversation_sync_state css ON c.id = css.conversation_id
                WHERE (css.last_incremental_sync IS NULL OR css.last_incremental_sync < ?)
                  AND css.sync_status != 'in_progress'
                  AND (css.error_count = 0 OR css.error_count IS NULL OR css.last_error_timestamp < ?)
                ORDER BY
                    c.updated_at DESC,
                    css.last_incremental_sync ASC
                LIMIT ?
            """,
                (threshold_iso, threshold_iso, limit),
            )

            return [row[0] for row in cursor.fetchall()]

    def get_sync_statistics(self) -> dict[str, Any]:
        """Get overall sync statistics."""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Basic counts
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total_conversations,
                    COUNT(css.conversation_id) as conversations_with_sync_state,
                    COUNT(CASE WHEN css.last_full_sync IS NOT NULL THEN 1 END) as full_synced,
                    COUNT(CASE WHEN css.last_incremental_sync IS NOT NULL THEN 1 END) as incremental_synced,
                    COUNT(CASE WHEN css.sync_status = 'in_progress' THEN 1 END) as in_progress,
                    COUNT(CASE WHEN css.sync_status = 'failed' THEN 1 END) as failed,
                    AVG(css.sync_completion_percentage) as avg_completion_percentage
                FROM conversations c
                LEFT JOIN conversation_sync_state css ON c.id = css.conversation_id
            """)
            stats = dict(cursor.fetchone())

            # Recent sync activity
            recent_threshold = datetime.now() - timedelta(hours=24)
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as recent_full_syncs,
                    COUNT(CASE WHEN sync_type = 'incremental' THEN 1 END) as recent_incremental_syncs,
                    COUNT(CASE WHEN sync_status = 'failed' THEN 1 END) as recent_failures,
                    AVG(duration_seconds) as avg_duration_seconds
                FROM conversation_sync_attempts
                WHERE started_at > ?
            """,
                (recent_threshold.isoformat(),),
            )

            recent_stats = dict(cursor.fetchone())
            stats.update(recent_stats)

            # Error analysis
            cursor = conn.execute("""
                SELECT
                    COUNT(DISTINCT conversation_id) as conversations_with_errors,
                    COUNT(*) as total_errors,
                    MAX(last_error_timestamp) as latest_error_time
                FROM conversation_sync_state
                WHERE error_count > 0
            """)

            error_stats = dict(cursor.fetchone())
            stats.update(error_stats)

            return stats

    def get_conversations_with_sync_errors(
        self, hours_back: int = 24
    ) -> list[dict[str, Any]]:
        """Get conversations that have had sync errors recently."""
        threshold = datetime.now() - timedelta(hours=hours_back)

        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                """
                SELECT
                    css.conversation_id,
                    css.error_count,
                    css.last_error,
                    css.last_error_timestamp,
                    css.sync_status,
                    css.last_sync_attempt,
                    c.customer_email
                FROM conversation_sync_state css
                JOIN conversations c ON css.conversation_id = c.id
                WHERE css.last_error_timestamp > ?
                ORDER BY css.last_error_timestamp DESC
            """,
                (threshold.isoformat(),),
            )

            return [dict(row) for row in cursor.fetchall()]

    def reset_conversation_sync_state(self, conversation_id: str):
        """Reset sync state for a conversation (useful for troubleshooting)."""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute(
                """
                DELETE FROM conversation_sync_state
                WHERE conversation_id = ?
            """,
                (conversation_id,),
            )

            conn.execute(
                """
                DELETE FROM conversation_sync_attempts
                WHERE conversation_id = ?
            """,
                (conversation_id,),
            )

            conn.commit()

    def mark_sync_started(self, conversation_id: str, sync_type: SyncType) -> datetime:
        """Mark a sync as started for a conversation."""
        started_at = datetime.now()

        # Update sync state
        state = self.get_conversation_sync_state(conversation_id)
        state.sync_status = SyncStatus.IN_PROGRESS
        state.sync_type = sync_type
        state.last_sync_attempt = started_at
        self.update_conversation_sync_state(state)

        # Log attempt
        self.log_sync_attempt(
            conversation_id, sync_type, SyncStatus.IN_PROGRESS, started_at
        )

        return started_at

    def mark_sync_completed(
        self,
        conversation_id: str,
        sync_type: SyncType,
        started_at: datetime,
        message_count: int = 0,
        api_calls_made: int = 0,
        metadata: dict[str, Any] | None = None,
    ):
        """Mark a sync as completed for a conversation."""
        completed_at = datetime.now()

        # Update sync state
        state = self.get_conversation_sync_state(conversation_id)
        state.sync_status = SyncStatus.COMPLETED
        state.sync_type = sync_type
        state.message_count_synced = message_count
        state.sync_completion_percentage = 100.0

        if sync_type == SyncType.FULL:
            state.last_full_sync = completed_at
        elif sync_type == SyncType.INCREMENTAL:
            state.last_incremental_sync = completed_at

        self.update_conversation_sync_state(state)

        # Log completed attempt
        self.log_sync_attempt(
            conversation_id,
            sync_type,
            SyncStatus.COMPLETED,
            started_at,
            completed_at,
            messages_after=message_count,
            api_calls_made=api_calls_made,
            metadata=metadata,
        )

    def mark_sync_failed(
        self,
        conversation_id: str,
        sync_type: SyncType,
        started_at: datetime,
        error_message: str,
        api_calls_made: int = 0,
        metadata: dict[str, Any] | None = None,
    ):
        """Mark a sync as failed for a conversation."""
        failed_at = datetime.now()

        # Update sync state
        state = self.get_conversation_sync_state(conversation_id)
        state.sync_status = SyncStatus.FAILED
        state.sync_type = sync_type
        state.error_count += 1
        state.last_error = error_message
        state.last_error_timestamp = failed_at

        self.update_conversation_sync_state(state)

        # Log failed attempt
        self.log_sync_attempt(
            conversation_id,
            sync_type,
            SyncStatus.FAILED,
            started_at,
            failed_at,
            api_calls_made=api_calls_made,
            error_message=error_message,
            metadata=metadata,
        )

    def _row_to_sync_state(self, row: sqlite3.Row) -> ConversationSyncState:
        """Convert database row to ConversationSyncState object."""
        import json

        return ConversationSyncState(
            conversation_id=row["conversation_id"],
            last_full_sync=datetime.fromisoformat(row["last_full_sync"])
            if row["last_full_sync"]
            else None,
            last_incremental_sync=datetime.fromisoformat(row["last_incremental_sync"])
            if row["last_incremental_sync"]
            else None,
            last_sync_attempt=datetime.fromisoformat(row["last_sync_attempt"])
            if row["last_sync_attempt"]
            else None,
            sync_status=SyncStatus(row["sync_status"]),
            sync_type=SyncType(row["sync_type"]) if row["sync_type"] else None,
            message_count_synced=row["message_count_synced"] or 0,
            last_message_timestamp=datetime.fromisoformat(row["last_message_timestamp"])
            if row["last_message_timestamp"]
            else None,
            error_count=row["error_count"] or 0,
            last_error=row["last_error"],
            last_error_timestamp=datetime.fromisoformat(row["last_error_timestamp"])
            if row["last_error_timestamp"]
            else None,
            sync_completion_percentage=row["sync_completion_percentage"] or 0.0,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )
