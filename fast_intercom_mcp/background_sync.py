"""Background sync service that runs inside the MCP server process."""

import asyncio
import contextlib
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class BackgroundSyncService:
    """Background sync service that runs inside the MCP server process."""

    def __init__(
        self, db_manager: Any, intercom_client: Any, sync_interval_minutes: int = 15
    ) -> None:
        self.db = db_manager
        self.intercom_client = intercom_client
        self.sync_interval = timedelta(minutes=sync_interval_minutes)
        self.running = False
        self.sync_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background sync service."""
        if self.running:
            return

        self.running = True
        self.sync_task = asyncio.create_task(self._sync_loop())
        logger.info(
            f"Background sync started with "
            f"{self.sync_interval.total_seconds() / 60} minute interval"
        )

    async def stop(self):
        """Stop the background sync service."""
        self.running = False
        if self.sync_task:
            self.sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.sync_task
        logger.info("Background sync stopped")

    async def _sync_loop(self):
        """Main sync loop - runs continuously."""
        # Initial sync on startup
        await self._perform_sync()

        while self.running:
            try:
                await asyncio.sleep(self.sync_interval.total_seconds())
                if self.running:  # Check again after sleep
                    await self._perform_sync()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sync loop error: {e}")
                # Continue running, retry in next interval

    async def _perform_sync(self):
        """Perform a single sync operation with metadata tracking."""
        start_time = datetime.now()

        # Progressive sync: check for gaps in history and prioritize recent data
        sync_periods = self._get_progressive_sync_periods()

        for start_date, end_date in sync_periods:
            # Start sync - write metadata
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO sync_metadata
                    (sync_started_at, sync_status, sync_type,
                     coverage_start_date, coverage_end_date)
                    VALUES (?, 'in_progress', 'background', ?, ?)
                """,
                    [
                        start_time.isoformat(),
                        start_date.date().isoformat(),
                        end_date.date().isoformat(),
                    ],
                )
                sync_id = cursor.lastrowid
                conn.commit()

            try:
                logger.info(
                    f"Starting background sync for {start_date.date()} to {end_date.date()}"
                )

                # Use IntercomClient directly to avoid sync service conflicts
                conversations = await self.intercom_client.fetch_conversations_for_period(
                    start_date, end_date
                )
                stored_count = self.db.store_conversations(conversations)

                # Record sync period
                self.db.record_sync_period(
                    start_date, end_date, len(conversations), stored_count, 0
                )

                total_convos = len(conversations)
                total_msgs = sum(len(conv.messages) for conv in conversations)

                # Update metadata on success
                with sqlite3.connect(self.db.db_path) as conn:
                    conn.execute(
                        """
                        UPDATE sync_metadata
                        SET sync_completed_at = ?,
                            sync_status = 'completed',
                            total_conversations = ?,
                            total_messages = ?
                        WHERE id = ?
                    """,
                        [datetime.now().isoformat(), total_convos, total_msgs, sync_id],
                    )
                    conn.commit()

                logger.info(
                    f"Background sync completed: {total_convos} conversations, "
                    f"{total_msgs} messages"
                )

            except Exception as e:
                logger.error(
                    f"Background sync failed for {start_date.date()} to {end_date.date()}: {e}"
                )

                # Update metadata on failure
                with sqlite3.connect(self.db.db_path) as conn:
                    conn.execute(
                        """
                        UPDATE sync_metadata
                        SET sync_completed_at = ?,
                            sync_status = 'failed',
                            error_message = ?
                        WHERE id = ?
                    """,
                        [datetime.now().isoformat(), str(e), sync_id],
                    )
                    conn.commit()

                # Don't break the entire sync for one failed period
                continue

    def _get_progressive_sync_periods(self):
        """Get sync periods in priority order: recent first, then historical gaps."""
        now = datetime.now()
        periods = []

        # Simplified approach - just sync recent periods to avoid database issues
        # Priority 1: Last hour (most recent data)
        recent_start = now - timedelta(hours=1)
        periods.append((recent_start, now))

        # Priority 2: Yesterday
        yesterday_start = now - timedelta(days=1)
        yesterday_end = now - timedelta(hours=1)
        periods.append((yesterday_start, yesterday_end))

        # Priority 3: Last 7 days (if configured for more history)
        config_days = self._get_configured_history_days()
        if config_days > 1:
            week_start = now - timedelta(days=7)
            week_end = now - timedelta(days=1)
            periods.append((week_start, week_end))

        return periods[:2]  # Limit to 2 periods to avoid overwhelming

    def _get_configured_history_days(self) -> int:
        """Get the configured number of history days to sync."""
        try:
            from .config import Config

            config = Config.load()
            return (
                config.initial_sync_days if config.initial_sync_days > 0 else 365
            )  # Default to 1 year if ALL
        except Exception:
            return 30  # Fallback default

    async def force_sync(self) -> bool:
        """Force an immediate sync (callable from MCP tools)."""
        try:
            await self._perform_sync()
            return True
        except Exception as e:
            logger.error(f"Force sync failed: {e}")
            return False
