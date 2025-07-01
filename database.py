"""SQLite database manager for FastIntercom MCP server."""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta

try:
    from datetime import UTC
except ImportError:
    # Python < 3.11 compatibility
    from datetime import timezone

    UTC = timezone.utc
from pathlib import Path
from typing import Any

from .models import Conversation, Message

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages SQLite database operations for conversation storage and sync tracking."""

    def __init__(self, db_path: str | None = None, pool_size: int = 5):
        """Initialize database manager.

        Args:
            db_path: Path to SQLite database file. If None, uses ~/.fastintercom/data.db
            pool_size: Number of connections to maintain in the pool (max 20)
        """
        if pool_size < 1 or pool_size > 20:
            raise ValueError(f"Database pool size must be between 1 and 20, got {pool_size}")

        self.pool_size = pool_size
        if db_path is None:
            # Default to config directory if set, otherwise user's home directory
            config_dir = os.getenv("FASTINTERCOM_CONFIG_DIR")
            if config_dir:
                self.db_dir = Path(config_dir)
            else:
                home_dir = Path.home()
                self.db_dir = home_dir / ".fastintercom"
            self.db_dir.mkdir(exist_ok=True)
            self.db_path = self.db_dir / "data.db"
        else:
            self.db_path = Path(db_path)
            self.db_dir = self.db_path.parent
            self.db_dir.mkdir(parents=True, exist_ok=True)

        self._init_database()

    def _init_database(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")

            # Check for schema compatibility
            self._check_schema_compatibility(conn)

            # Enhanced conversations table with thread tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    customer_email TEXT,
                    tags TEXT, -- JSON array
                    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    message_count INTEGER DEFAULT 0,
                    -- New thread tracking fields
                    thread_complete BOOLEAN DEFAULT FALSE,
                    last_message_synced TIMESTAMP,
                    message_sequence_number INTEGER DEFAULT 0,
                    thread_last_checked TIMESTAMP
                )
            """)

            # Enhanced messages table with thread position tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    author_type TEXT NOT NULL, -- 'user' | 'admin'
                    body TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    part_type TEXT, -- 'comment' | 'note' | 'message'
                    -- New thread tracking fields
                    sequence_number INTEGER,
                    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sync_version INTEGER DEFAULT 1,
                    thread_position INTEGER,
                    is_complete BOOLEAN DEFAULT TRUE,
                    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
                )
            """)

            # Sync periods tracking table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_periods (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_timestamp TIMESTAMP NOT NULL,
                    end_timestamp TIMESTAMP NOT NULL,
                    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    conversation_count INTEGER DEFAULT 0,
                    new_conversations INTEGER DEFAULT 0,
                    updated_conversations INTEGER DEFAULT 0
                )
            """)

            # Sync metadata table for tracking sync operations
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_started_at TIMESTAMP NOT NULL,
                    sync_completed_at TIMESTAMP,
                    sync_status TEXT NOT NULL, -- 'in_progress', 'completed', 'failed'
                    coverage_start_date DATE,
                    coverage_end_date DATE,
                    total_conversations INTEGER DEFAULT 0,
                    total_messages INTEGER DEFAULT 0,
                    sync_type TEXT, -- 'full', 'incremental'
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Index for quick lookups
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_metadata_completed "
                "ON sync_metadata(sync_completed_at DESC)"
            )

            # Request tracking for intelligent sync triggers
            conn.execute("""
                CREATE TABLE IF NOT EXISTS request_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timeframe_start TIMESTAMP NOT NULL,
                    timeframe_end TIMESTAMP NOT NULL,
                    request_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    data_freshness_seconds INTEGER, -- How old the data was when served
                    sync_triggered BOOLEAN DEFAULT FALSE
                )
            """)

            # Conversation-level sync state tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_sync_state (
                    conversation_id TEXT PRIMARY KEY,
                    last_message_timestamp TIMESTAMP,
                    total_messages_synced INTEGER DEFAULT 0,
                    thread_complete BOOLEAN DEFAULT FALSE,
                    last_sync_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sync_status TEXT DEFAULT 'complete', -- 'incomplete', 'complete', 'error'
                    error_message TEXT,
                    next_sync_needed BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
                )
            """)

            # Message thread tracking for handling message dependencies
            conn.execute("""
                CREATE TABLE IF NOT EXISTS message_threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    parent_message_id TEXT,
                    child_message_id TEXT NOT NULL,
                    thread_depth INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
                    FOREIGN KEY (child_message_id) REFERENCES messages (id) ON DELETE CASCADE,
                    UNIQUE(parent_message_id, child_message_id)
                )
            """)

            # Schema version tracking for future compatibility
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )
            """)

            # Record current schema version
            conn.execute("""
                INSERT OR IGNORE INTO schema_version (version, description)
                VALUES (2, 'Enhanced message tracking with conversation threads')
            """)

            # Create indexes for performance
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_created_at "
                "ON conversations (created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_updated_at "
                "ON conversations (updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_customer_email "
                "ON conversations (customer_email)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conversation_id "
                "ON messages (conversation_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages (created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_periods_timestamps "
                "ON sync_periods (start_timestamp, end_timestamp)"
            )

            # Enhanced indexes for thread tracking
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_thread_complete "
                "ON conversations (thread_complete)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_last_message_synced "
                "ON conversations (last_message_synced)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_message_sequence "
                "ON conversations (message_sequence_number)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_sequence_number "
                "ON messages (conversation_id, sequence_number)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_last_synced ON messages (last_synced)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_sync_version ON messages (sync_version)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_thread_position "
                "ON messages (conversation_id, thread_position)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_sync_state_status "
                "ON conversation_sync_state (sync_status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_sync_state_next_sync "
                "ON conversation_sync_state (next_sync_needed)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_sync_state_last_sync "
                "ON conversation_sync_state (last_sync_attempt)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_threads_conversation "
                "ON message_threads (conversation_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_threads_parent "
                "ON message_threads (parent_message_id)"
            )

            # Create useful views for sync operations
            conn.execute("""
                CREATE VIEW IF NOT EXISTS conversations_needing_sync AS
                SELECT
                    c.id,
                    c.created_at,
                    c.updated_at,
                    c.thread_complete,
                    c.last_message_synced,
                    css.sync_status,
                    css.error_message,
                    css.next_sync_needed
                FROM conversations c
                LEFT JOIN conversation_sync_state css ON c.id = css.conversation_id
                WHERE
                    c.thread_complete = FALSE
                    OR css.sync_status = 'incomplete'
                    OR css.next_sync_needed = TRUE
                    OR css.conversation_id IS NULL
            """)

            conn.execute("""
                CREATE VIEW IF NOT EXISTS conversations_needing_incremental_sync AS
                SELECT
                    c.id,
                    c.updated_at,
                    c.last_message_synced,
                    CASE
                        WHEN c.last_message_synced IS NULL THEN 1
                        WHEN c.updated_at > c.last_message_synced THEN 1
                        ELSE 0
                    END as needs_sync
                FROM conversations c
                WHERE needs_sync = 1
            """)

            conn.commit()

    def _check_schema_compatibility(self, conn: sqlite3.Connection):
        """Check if existing database is compatible with current schema version."""
        try:
            # Check if schema_version table exists
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='schema_version'
            """)
            schema_table_exists = cursor.fetchone() is not None

            if schema_table_exists:
                # Check current schema version
                cursor = conn.execute("SELECT MAX(version) FROM schema_version")
                current_version = cursor.fetchone()[0]

                if current_version and current_version < 2:
                    # Incompatible schema - require fresh database
                    self._backup_and_reset_database(conn)
            else:
                # Check if old database exists by looking for conversations table
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='conversations'
                """)
                conversations_table_exists = cursor.fetchone() is not None

                if conversations_table_exists:
                    # Check if it has new thread tracking columns
                    cursor = conn.execute("PRAGMA table_info(conversations)")
                    columns = [col[1] for col in cursor.fetchall()]

                    if "thread_complete" not in columns:
                        # Old schema - require fresh database
                        self._backup_and_reset_database(conn)

        except Exception as e:
            logger.warning(f"Schema compatibility check failed: {e}")
            # On error, assume incompatible and reset
            self._backup_and_reset_database(conn)

    def _backup_and_reset_database(self, conn: sqlite3.Connection):
        """Backup old database and reset for new schema."""
        logger.info(
            "Incompatible database schema detected. Creating backup and resetting database."
        )

        # Get table names
        cursor = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
        """)
        tables = [row[0] for row in cursor.fetchall()]

        if tables:
            # Create backup timestamp
            backup_suffix = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Rename all existing tables with backup suffix
            for table in tables:
                try:
                    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_backup_{backup_suffix}")
                except sqlite3.OperationalError as e:
                    logger.warning(f"Could not backup table {table}: {e}")

        conn.commit()
        logger.info(
            f"Database reset complete. Old tables backed up with suffix '_backup_{backup_suffix}'"
        )

    def store_conversations(self, conversations: list[Conversation]) -> int:
        """Store or update conversations in database.

        Args:
            conversations: List of conversations to store

        Returns:
            Number of conversations actually stored/updated
        """
        if not conversations:
            return 0

        stored_count = 0
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")

            for conv in conversations:
                # Check if conversation exists
                cursor = conn.execute(
                    "SELECT id, updated_at, message_count FROM conversations WHERE id = ?",
                    (conv.id,),
                )
                existing = cursor.fetchone()

                # Convert tags to JSON
                tags_json = json.dumps(conv.tags) if conv.tags else "[]"

                if existing:
                    # Update if conversation has new messages or updates
                    existing_id, existing_updated_at, existing_msg_count = existing
                    existing_updated = datetime.fromisoformat(
                        existing_updated_at.replace("Z", "+00:00")
                    )

                    if (
                        conv.updated_at > existing_updated
                        or len(conv.messages) != existing_msg_count
                    ):
                        # Update conversation
                        conn.execute(
                            """
                            UPDATE conversations
                            SET updated_at = ?, customer_email = ?, tags = ?,
                                last_synced = CURRENT_TIMESTAMP, message_count = ?
                            WHERE id = ?
                        """,
                            (
                                conv.updated_at.isoformat(),
                                conv.customer_email,
                                tags_json,
                                len(conv.messages),
                                conv.id,
                            ),
                        )

                        # Delete old messages and insert new ones
                        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv.id,))
                        self._store_messages(conn, conv.messages, conv.id)
                        stored_count += 1
                else:
                    # Insert new conversation
                    conn.execute(
                        """
                        INSERT INTO conversations
                        (id, created_at, updated_at, customer_email, tags, message_count)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (
                            conv.id,
                            conv.created_at.isoformat(),
                            conv.updated_at.isoformat(),
                            conv.customer_email,
                            tags_json,
                            len(conv.messages),
                        ),
                    )

                    # Insert messages
                    self._store_messages(conn, conv.messages, conv.id)
                    stored_count += 1

            conn.commit()

        return stored_count

    def upsert_conversation(self, conversation: Conversation) -> int:
        """Store or update a single conversation in database.

        Args:
            conversation: Conversation to store/update

        Returns:
            Number of conversations actually stored/updated (0 or 1)
        """
        return self.store_conversations([conversation])

    def _store_messages(
        self, conn: sqlite3.Connection, messages: list[Message], conversation_id: str
    ):
        """Store messages for a conversation."""
        for msg in messages:
            conn.execute(
                """
                INSERT OR REPLACE INTO messages
                (id, conversation_id, author_type, body, created_at, part_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    msg.id,
                    conversation_id,
                    msg.author_type,
                    msg.body,
                    msg.created_at.isoformat(),
                    getattr(msg, "part_type", None),
                ),
            )

    def search_conversations(
        self,
        query: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        customer_email: str | None = None,
        limit: int = 100,
    ) -> list[Conversation]:
        """Search conversations with filters.

        Args:
            query: Text search in message bodies
            start_date: Filter conversations created after this date
            end_date: Filter conversations created before this date
            customer_email: Filter by customer email
            limit: Maximum number of conversations to return

        Returns:
            List of matching conversations with messages
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Build query conditions
            conditions = []
            params = []

            if start_date:
                conditions.append("c.created_at >= ?")
                params.append(start_date.isoformat())

            if end_date:
                conditions.append("c.created_at <= ?")
                params.append(end_date.isoformat())

            if customer_email:
                conditions.append("c.customer_email = ?")
                params.append(customer_email)

            if query:
                # Search in message bodies
                conditions.append("""
                    c.id IN (
                        SELECT DISTINCT conversation_id
                        FROM messages
                        WHERE body LIKE ?
                    )
                """)
                params.append(f"%{query}%")

            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

            # Get conversations
            conv_query = f"""
                SELECT c.* FROM conversations c
                {where_clause}
                ORDER BY c.created_at DESC
                LIMIT ?
            """
            params.append(limit)

            conversations = []
            for row in conn.execute(conv_query, params):
                # Get messages for this conversation
                messages = []
                msg_cursor = conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at ASC
                """,
                    (row["id"],),
                )

                for msg_row in msg_cursor:
                    messages.append(
                        Message(
                            id=msg_row["id"],
                            author_type=msg_row["author_type"],
                            body=msg_row["body"],
                            created_at=datetime.fromisoformat(msg_row["created_at"]),
                            part_type=msg_row["part_type"],
                        )
                    )

                # Parse tags from JSON
                tags = json.loads(row["tags"]) if row["tags"] else []

                conversations.append(
                    Conversation(
                        id=row["id"],
                        created_at=datetime.fromisoformat(row["created_at"]),
                        updated_at=datetime.fromisoformat(row["updated_at"]),
                        messages=messages,
                        customer_email=row["customer_email"],
                        tags=tags,
                    )
                )

            return conversations

    def get_sync_status(self) -> dict[str, Any]:
        """Get current sync status and statistics."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get conversation counts
            cursor = conn.execute("SELECT COUNT(*) as total FROM conversations")
            total_conversations = cursor.fetchone()["total"]

            cursor = conn.execute("SELECT COUNT(*) as total FROM messages")
            total_messages = cursor.fetchone()["total"]

            # Get last sync time
            cursor = conn.execute("""
                SELECT MAX(last_synced) as last_sync
                FROM conversations
            """)
            last_sync_row = cursor.fetchone()
            last_sync = last_sync_row["last_sync"] if last_sync_row["last_sync"] else None

            # Get recent sync activity
            cursor = conn.execute("""
                SELECT * FROM sync_periods
                ORDER BY last_synced DESC
                LIMIT 5
            """)
            recent_syncs = [dict(row) for row in cursor.fetchall()]

            # Get database file size
            db_size_bytes = os.path.getsize(self.db_path)
            db_size_mb = db_size_bytes / (1024 * 1024)

            return {
                "total_conversations": total_conversations,
                "total_messages": total_messages,
                "last_sync": last_sync,
                "recent_syncs": recent_syncs,
                "database_size_mb": round(db_size_mb, 2),
                "database_path": str(self.db_path),
            }

    def record_sync_period(
        self,
        start_time: datetime,
        end_time: datetime,
        conversation_count: int,
        new_count: int = 0,
        updated_count: int = 0,
    ) -> int:
        """Record a sync period for tracking.

        Args:
            start_time: Start of the sync period
            end_time: End of the sync period
            conversation_count: Total conversations in this period
            new_count: Number of new conversations added
            updated_count: Number of existing conversations updated

        Returns:
            ID of the created sync period record
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO sync_periods
                (start_timestamp, end_timestamp, conversation_count,
                 new_conversations, updated_conversations)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    start_time.isoformat(),
                    end_time.isoformat(),
                    conversation_count,
                    new_count,
                    updated_count,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_periods_needing_sync(self, max_age_minutes: int = 5) -> list[tuple[datetime, datetime]]:
        """Get time periods that need syncing based on last sync time.

        Args:
            max_age_minutes: Maximum age in minutes before considering data stale

        Returns:
            List of (start_time, end_time) tuples that need syncing
        """
        cutoff_time = datetime.now(UTC).replace(tzinfo=None)  # Remove timezone for SQLite
        cutoff_time = cutoff_time.replace(minute=cutoff_time.minute - max_age_minutes)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Find periods that haven't been synced recently
            cursor = conn.execute(
                """
                SELECT start_timestamp, end_timestamp
                FROM sync_periods
                WHERE last_synced < ?
                ORDER BY start_timestamp DESC
                LIMIT 10
            """,
                (cutoff_time.isoformat(),),
            )

            periods = []
            for row in cursor.fetchall():
                start = datetime.fromisoformat(row["start_timestamp"])
                end = datetime.fromisoformat(row["end_timestamp"])
                periods.append((start, end))

            return periods

    def record_request_pattern(
        self,
        start_time: datetime,
        end_time: datetime,
        data_freshness_seconds: int,
        sync_triggered: bool = False,
    ) -> int:
        """Record a request pattern for intelligent sync analysis.

        Args:
            start_time: Start of the requested timeframe
            end_time: End of the requested timeframe
            data_freshness_seconds: How old the data was when served
            sync_triggered: Whether this request triggered a background sync

        Returns:
            ID of the created request pattern record
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO request_patterns
                (timeframe_start, timeframe_end, data_freshness_seconds, sync_triggered)
                VALUES (?, ?, ?, ?)
            """,
                (
                    start_time.isoformat(),
                    end_time.isoformat(),
                    data_freshness_seconds,
                    sync_triggered,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_stale_timeframes(
        self, staleness_threshold_minutes: int = 5
    ) -> list[tuple[datetime, datetime]]:
        """Get timeframes that have been requested recently but may have stale data.

        Args:
            staleness_threshold_minutes: Consider data stale if older than this

        Returns:
            List of (start_time, end_time) tuples that need refreshing
        """
        cutoff_time = datetime.now()
        recent_requests_since = cutoff_time - timedelta(hours=1)  # Look at last hour of requests

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Find recent requests where data was stale or sync wasn't triggered
            cursor = conn.execute(
                """
                SELECT DISTINCT timeframe_start, timeframe_end, data_freshness_seconds
                FROM request_patterns
                WHERE request_timestamp >= ?
                  AND (data_freshness_seconds > ? OR sync_triggered = FALSE)
                ORDER BY request_timestamp DESC
                LIMIT 10
            """,
                (recent_requests_since.isoformat(), staleness_threshold_minutes * 60),
            )

            timeframes = []
            for row in cursor.fetchall():
                start = datetime.fromisoformat(row["timeframe_start"])
                end = datetime.fromisoformat(row["timeframe_end"])
                timeframes.append((start, end))

            return timeframes

    def get_data_freshness_for_timeframe(self, start_time: datetime, end_time: datetime) -> int:
        """Calculate how old the data is for a given timeframe.

        Args:
            start_time: Start of timeframe
            end_time: End of timeframe

        Returns:
            Age of data in seconds (0 if no data exists)
        """
        with sqlite3.connect(self.db_path) as conn:
            # Find the most recent conversation in this timeframe
            cursor = conn.execute(
                """
                SELECT MAX(last_synced) as latest_sync
                FROM conversations
                WHERE created_at >= ? AND created_at <= ?
            """,
                (start_time.isoformat(), end_time.isoformat()),
            )

            result = cursor.fetchone()
            if result and result[0]:
                latest_sync = datetime.fromisoformat(result[0])
                freshness = (datetime.now() - latest_sync).total_seconds()
                return int(freshness)

            return 0  # No data means "fresh" (will trigger initial sync)

    def check_sync_state(
        self,
        start_date: datetime | None,
        end_date: datetime | None,
        freshness_threshold_minutes: int = 5,
    ) -> dict[str, Any]:
        """
        Check sync state relative to requested timeframe.

        Returns sync state ('stale', 'partial', 'fresh') with metadata.

        Args:
            start_date: Start of requested timeframe
            end_date: End of requested timeframe
            freshness_threshold_minutes: Minutes before data considered stale

        Returns:
            Dict with sync_state, last_sync, message, and should_sync fields
        """
        sync_status = self.get_sync_status()
        last_sync_str = sync_status.get("last_sync")

        if not last_sync_str:
            return {
                "sync_state": "stale",
                "last_sync": None,
                "message": "No sync data available - database needs initial sync",
                "should_sync": True,
                "data_complete": False,
            }

        try:
            # Parse last sync time (handle various ISO formats)
            last_sync = datetime.fromisoformat(last_sync_str.replace("Z", "+00:00"))
            if last_sync.tzinfo:
                last_sync = last_sync.replace(tzinfo=None)  # Make naive for comparison
        except (ValueError, AttributeError):
            return {
                "sync_state": "stale",
                "last_sync": None,
                "message": f"Invalid sync timestamp: {last_sync_str}",
                "should_sync": True,
                "data_complete": False,
            }

        # If no timeframe specified, check general freshness
        if not start_date or not end_date:
            recent_threshold = datetime.now() - timedelta(minutes=freshness_threshold_minutes)
            if last_sync >= recent_threshold:
                return {
                    "sync_state": "fresh",
                    "last_sync": last_sync,
                    "data_complete": True,
                }
            return {
                "sync_state": "partial",
                "last_sync": last_sync,
                "message": (
                    f"Data may be stale - last sync: {last_sync.strftime('%Y-%m-%d %H:%M:%S')}"
                ),
                "data_complete": False,
            }

        # State 1: Stale - last sync before requested period
        if last_sync < start_date:
            return {
                "sync_state": "stale",
                "last_sync": last_sync,
                "message": (
                    f"Data is stale - last sync {last_sync.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"is before requested period {start_date.strftime('%Y-%m-%d %H:%M:%S')}"
                ),
                "should_sync": True,
                "data_complete": False,
            }

        # State 2: Partial - last sync within requested period
        if start_date <= last_sync < end_date:
            return {
                "sync_state": "partial",
                "last_sync": last_sync,
                "message": (
                    f"Analysis includes conversations up to "
                    f"{last_sync.strftime('%Y-%m-%d %H:%M:%S')} - "
                    f"may be missing recent conversations"
                ),
                "should_sync": False,
                "data_complete": False,
            }

        # State 3: Fresh - last sync recent relative to end time
        freshness_threshold_dt = end_date - timedelta(minutes=freshness_threshold_minutes)
        if last_sync >= freshness_threshold_dt:
            return {
                "sync_state": "fresh",
                "last_sync": last_sync,
                "should_sync": False,
                "data_complete": True,
            }
        # Slightly stale but within acceptable range
        return {
            "sync_state": "partial",
            "last_sync": last_sync,
            "message": (
                f"Analysis includes conversations up to "
                f"{last_sync.strftime('%Y-%m-%d %H:%M:%S')} - "
                f"may be missing very recent conversations"
            ),
            "should_sync": False,
            "data_complete": False,
        }

    def get_conversation_by_id(self, conversation_id: str) -> Conversation | None:
        """Get a conversation by ID with its messages."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Get conversation data - only select basic columns for test compatibility
            cursor = conn.execute(
                """
                SELECT id, customer_email, created_at, updated_at, tags, message_count
                FROM conversations
                WHERE id = ?
                """,
                (conversation_id,),
            )
            row = cursor.fetchone()

            if not row:
                return None

            # Get messages for this conversation - check which columns exist
            # First, get column info
            cursor = conn.execute("PRAGMA table_info(messages)")
            columns = {row[1] for row in cursor.fetchall()}

            # Build query based on available columns
            select_columns = ["id", "conversation_id", "body", "author_type", "created_at"]
            optional_columns = ["type", "author_id", "author_email", "author_name", "part_type"]

            for col in optional_columns:
                if col in columns:
                    select_columns.append(col)

            messages_cursor = conn.execute(
                f"""
                SELECT {", ".join(select_columns)}
                FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            )

            messages = []
            for msg_row in messages_cursor:
                # Handle both test and production schemas
                msg_dict = dict(msg_row)
                messages.append(
                    Message(
                        id=msg_dict["id"],
                        author_type=msg_dict["author_type"],
                        body=msg_dict["body"],
                        created_at=datetime.fromisoformat(msg_dict["created_at"]),
                        part_type=msg_dict.get("type") or msg_dict.get("part_type", "comment"),
                    )
                )

            # Create Conversation object with only the fields it expects
            return Conversation(
                id=row["id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                customer_email=row["customer_email"],
                tags=json.loads(row["tags"]) if row["tags"] else [],
                messages=messages,
            )

    def get_conversations_needing_thread_sync(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get conversations that need complete thread fetching."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                """
                SELECT * FROM conversations_needing_sync
                ORDER BY created_at DESC
                LIMIT ?
            """,
                (limit,),
            )

            return [dict(row) for row in cursor.fetchall()]

    def get_conversations_needing_incremental_sync(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get conversations that need incremental message updates."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                """
                SELECT * FROM conversations_needing_incremental_sync
                ORDER BY updated_at DESC
                LIMIT ?
            """,
                (limit,),
            )

            return [dict(row) for row in cursor.fetchall()]

    def update_conversation_sync_state(
        self,
        conversation_id: str,
        sync_status: str = "complete",
        thread_complete: bool = True,
        total_messages: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update the sync state for a conversation."""
        with sqlite3.connect(self.db_path) as conn:
            # Update conversation table
            conn.execute(
                """
                UPDATE conversations
                SET thread_complete = ?,
                    last_message_synced = CURRENT_TIMESTAMP,
                    message_sequence_number = COALESCE(?, message_sequence_number)
                WHERE id = ?
            """,
                (thread_complete, total_messages, conversation_id),
            )

            # Update or insert sync state
            conn.execute(
                """
                INSERT OR REPLACE INTO conversation_sync_state
                (conversation_id, sync_status, thread_complete, total_messages_synced,
                 last_sync_attempt, error_message, next_sync_needed)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, FALSE)
            """,
                (
                    conversation_id,
                    sync_status,
                    thread_complete,
                    total_messages or 0,
                    error_message,
                ),
            )

            conn.commit()

    def mark_conversation_for_resync(self, conversation_id: str, reason: str = None) -> None:
        """Mark a conversation as needing re-synchronization."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE conversations
                SET thread_complete = FALSE
                WHERE id = ?
            """,
                (conversation_id,),
            )

            conn.execute(
                """
                INSERT OR REPLACE INTO conversation_sync_state
                (conversation_id, sync_status, thread_complete, next_sync_needed, error_message)
                VALUES (?, 'incomplete', FALSE, TRUE, ?)
            """,
                (conversation_id, reason),
            )

            conn.commit()

    def get_incomplete_conversations_count(self) -> int:
        """Get count of conversations with incomplete thread sync."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM conversations
                WHERE thread_complete = FALSE
            """)
            return cursor.fetchone()[0]

    def get_sync_progress_stats(self) -> dict[str, Any]:
        """Get detailed sync progress statistics."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Total conversations
            cursor = conn.execute("SELECT COUNT(*) as total FROM conversations")
            total_conversations = cursor.fetchone()["total"]

            # Complete vs incomplete threads
            cursor = conn.execute("""
                SELECT
                    SUM(CASE WHEN thread_complete = TRUE THEN 1 ELSE 0 END) as complete,
                    SUM(CASE WHEN thread_complete = FALSE THEN 1 ELSE 0 END) as incomplete
                FROM conversations
            """)
            thread_stats = cursor.fetchone()

            # Sync state breakdown
            cursor = conn.execute("""
                SELECT sync_status, COUNT(*) as count
                FROM conversation_sync_state
                GROUP BY sync_status
            """)
            sync_status_breakdown = {row["sync_status"]: row["count"] for row in cursor.fetchall()}

            # Messages statistics
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total_messages,
                    COUNT(DISTINCT conversation_id) as conversations_with_messages,
                    AVG(CAST(substr(created_at, 1, 10) AS INTEGER)) as avg_message_age_days
                FROM messages
            """)
            message_stats = cursor.fetchone()

            return {
                "total_conversations": total_conversations,
                "complete_threads": thread_stats["complete"] or 0,
                "incomplete_threads": thread_stats["incomplete"] or 0,
                "completion_percentage": round(
                    (thread_stats["complete"] or 0) / max(total_conversations, 1) * 100,
                    1,
                ),
                "sync_status_breakdown": sync_status_breakdown,
                "total_messages": message_stats["total_messages"] or 0,
                "conversations_with_messages": message_stats["conversations_with_messages"] or 0,
                "average_messages_per_conversation": round(
                    (message_stats["total_messages"] or 0) / max(total_conversations, 1),
                    1,
                ),
            }

    def close(self):
        """Close database connections (for cleanup)."""
        # SQLite connections are closed automatically when using context managers
        pass
