"""Background sync service for keeping conversation data up to date."""

import asyncio
import contextlib
import logging
import threading
from datetime import datetime, timedelta
from typing import Any

from .database import DatabaseManager
from .intercom_client import IntercomClient
from .models import SyncStateException, SyncStats
from .sync.coordinator import TwoPhaseConfig, TwoPhaseSyncCoordinator

logger = logging.getLogger(__name__)


class SyncService:
    """Manages background synchronization of Intercom conversations."""

    def __init__(
        self, database_manager: DatabaseManager, intercom_client: IntercomClient
    ):
        self.db = database_manager
        self.intercom = intercom_client
        self.app_id = None

        # Sync state
        self._sync_active = False
        self._current_operation = None
        self._last_sync_time = None
        self._sync_stats = {}

        # Background task management
        self._background_task = None
        self._shutdown_event = asyncio.Event()

        # Sync settings
        self.max_sync_age_minutes = 5  # Trigger sync if data is older than this
        self.background_sync_interval_minutes = (
            10  # Check for sync needs every 10 minutes
        )

        # Two-phase coordinator for advanced operations
        self.two_phase_coordinator = TwoPhaseSyncCoordinator(
            intercom_client, database_manager, TwoPhaseConfig()
        )

    async def start_background_sync(self):
        """Start the background sync service."""
        if self._background_task and not self._background_task.done():
            logger.warning("Background sync already running")
            return

        logger.info("Starting background sync service")
        self._background_task = asyncio.create_task(self._background_sync_loop())

        # Initialize app_id
        self.app_id = await self.intercom.get_app_id()

    async def stop_background_sync(self):
        """Stop the background sync service."""
        logger.info("Stopping background sync service")
        self._shutdown_event.set()

        if self._background_task:
            try:
                await asyncio.wait_for(self._background_task, timeout=10.0)
            except TimeoutError:
                logger.warning("Background sync task did not stop gracefully")
                self._background_task.cancel()

    async def _background_sync_loop(self):
        """Main background sync loop."""
        while not self._shutdown_event.is_set():
            try:
                # Check if we need to sync recent data
                await self._check_and_sync_recent()

                # Wait for next check
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.background_sync_interval_minutes * 60,
                    )
                    break  # Shutdown requested
                except TimeoutError:
                    pass  # Continue loop

            except Exception as e:
                logger.error(f"Background sync error: {e}")
                # Wait a bit before retrying
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=60)
                    break
                except TimeoutError:
                    pass

    async def _check_and_sync_recent(self):
        """Check if recent data needs syncing and do it if needed."""
        if self._sync_active:
            logger.debug("Sync already active, skipping background check")
            return

        # Priority 1: Check for request-triggered timeframes that need syncing
        stale_request_timeframes = self.db.get_stale_timeframes(
            self.max_sync_age_minutes
        )

        if stale_request_timeframes:
            logger.info(
                f"Found {len(stale_request_timeframes)} request-triggered timeframes needing sync"
            )
            for start, end in stale_request_timeframes[
                :2
            ]:  # Limit to 2 to avoid overwhelming API
                await self.sync_period(start, end, is_background=True)

        # Priority 2: Check legacy period-based syncing
        stale_periods = self.db.get_periods_needing_sync(self.max_sync_age_minutes)

        if (
            stale_periods and not stale_request_timeframes
        ):  # Only if no request-triggered syncs needed
            logger.info(
                f"Found {len(stale_periods)} stale periods, triggering background sync"
            )
            for start, end in stale_periods[:2]:  # Limit to 2 periods
                await self.sync_period(start, end, is_background=True)

        # Priority 3: Enhanced background sync for better coverage
        if not stale_request_timeframes and not stale_periods:
            now = datetime.now()

            # Check if we have data from today by checking database directly
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            import sqlite3

            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE created_at >= ?",
                    [today_start.isoformat()],
                )
                today_count = cursor.fetchone()[0]

            if today_count < 5:  # Less than 5 conversations today
                # Sync the full day to get better coverage
                logger.info(
                    f"Only {today_count} conversations found for today, syncing full day"
                )
                await self.sync_period(today_start, now, is_background=True)
            else:
                # We have some data, just sync recent hour
                recent_start = now - timedelta(hours=1)
                await self.sync_period(recent_start, now, is_background=True)

    async def sync_if_needed(
        self, start_date: datetime | None, end_date: datetime | None
    ):
        """
        Smart sync based on 3-state sync logic.

        States:
        - 'stale': Data is too old, trigger sync and wait
        - 'partial': Data is incomplete but usable, proceed with warning
        - 'fresh': Data is current, proceed normally
        """
        # Check sync state using intelligent logic
        sync_info = self.db.check_sync_state(
            start_date, end_date, self.max_sync_age_minutes
        )
        sync_state = sync_info["sync_state"]

        logger.info(f"Sync state check: {sync_state}")
        if sync_info.get("message"):
            logger.info(f"Sync message: {sync_info['message']}")

        # Handle different sync states
        if sync_state == "stale" and sync_info.get("should_sync"):
            # State 1: Data is too stale - trigger sync and wait
            logger.warning(f"Data is stale, triggering sync: {sync_info['message']}")

            if not start_date or not end_date:
                # No specific range, sync recent data
                await self.sync_recent()
            else:
                # Sync specific period
                try:
                    await self.sync_period(start_date, end_date)
                    logger.info("Sync completed, data is now fresh")
                except Exception as e:
                    # If sync fails, raise exception to inform user
                    raise SyncStateException(
                        f"Data is stale and sync failed: {str(e)}",
                        sync_state="stale",
                        last_sync=sync_info.get("last_sync"),
                    ) from e

        elif sync_state == "partial":
            # State 2: Partial data - proceed but log warning
            logger.warning(f"Proceeding with partial data: {sync_info['message']}")

        # State 3: Fresh data - proceed normally (no action needed)
        elif sync_state == "fresh":
            logger.debug("Using fresh cached data")

        return sync_info

    async def sync_recent(self) -> SyncStats:
        """Sync conversations from the last few hours."""
        now = datetime.now()
        since = now - timedelta(hours=6)  # Last 6 hours
        return await self.sync_incremental(since)

    async def sync_period(
        self, start_date: datetime, end_date: datetime, is_background: bool = False
    ) -> SyncStats:
        """Sync all conversations in a specific time period."""
        if self._sync_active and not is_background:
            raise Exception("Sync already in progress")

        self._sync_active = True
        self._current_operation = (
            f"Syncing {start_date.strftime('%m/%d')} to {end_date.strftime('%m/%d')}"
        )

        try:
            logger.info(f"Starting period sync: {start_date} to {end_date}")

            # Fetch conversations from Intercom
            conversations = await self.intercom.fetch_conversations_for_period(
                start_date, end_date
            )

            # Store in database
            stored_count = self.db.store_conversations(conversations)

            # Record sync period
            self.db.record_sync_period(
                start_date, end_date, len(conversations), stored_count, 0
            )

            stats = SyncStats(
                total_conversations=len(conversations),
                new_conversations=stored_count,
                updated_conversations=0,  # Simplified for now
                total_messages=sum(len(conv.messages) for conv in conversations),
                duration_seconds=0,  # Would track this in real implementation
                api_calls_made=0,  # Would track this in real implementation
            )

            self._last_sync_time = datetime.now()
            self._sync_stats = stats.__dict__

            logger.info(
                f"Period sync completed: {stats.total_conversations} conversations"
            )
            return stats

        finally:
            self._sync_active = False
            self._current_operation = None

    async def sync_incremental(self, since: datetime) -> SyncStats:
        """Sync conversations updated since the given timestamp."""
        if self._sync_active:
            raise Exception("Sync already in progress")

        self._sync_active = True
        self._current_operation = (
            f"Incremental sync since {since.strftime('%m/%d %H:%M')}"
        )

        try:
            logger.info(f"Starting incremental sync since {since}")

            # Use the incremental sync method
            stats = await self.intercom.fetch_conversations_incremental(since)

            self._last_sync_time = datetime.now()
            self._sync_stats = stats.__dict__

            logger.info(
                f"Incremental sync completed: {stats.total_conversations} conversations"
            )
            return stats

        finally:
            self._sync_active = False
            self._current_operation = None

    async def sync_period_two_phase(
        self, start_date: datetime, end_date: datetime, is_background: bool = False
    ) -> SyncStats:
        """Two-phase sync: search for conversations, then fetch complete threads."""
        if self._sync_active and not is_background:
            raise Exception("Sync already in progress")

        self._sync_active = True
        self._current_operation = f"Two-phase sync {start_date.strftime('%m/%d')} to {end_date.strftime('%m/%d')}"

        try:
            logger.info(f"Starting two-phase sync: {start_date} to {end_date}")

            # Use two-phase coordinator
            stats = await self.two_phase_coordinator.sync_period_two_phase(
                start_date, end_date, force_refetch=False
            )

            self._last_sync_time = datetime.now()
            self._sync_stats = stats.__dict__

            logger.info(
                f"Two-phase sync completed: {stats.total_conversations} conversations"
            )
            return stats

        finally:
            self._sync_active = False
            self._current_operation = None

    async def sync_initial(self, days_back: int = 30) -> SyncStats:
        """Perform initial sync of conversation history.

        Args:
            days_back: Number of days of history to sync (default: 30, max: 30)
        """
        # Limit to 30 days max for initial sync
        days_back = min(days_back, 30)

        now = datetime.now()
        start_date = now - timedelta(days=days_back)

        logger.info(f"Starting initial sync: {days_back} days of history")
        return await self.sync_period(start_date, now)

    def get_status(self) -> dict[str, Any]:
        """Get current sync service status."""
        return {
            "active": self._sync_active,
            "current_operation": self._current_operation,
            "last_sync_time": self._last_sync_time.isoformat()
            if self._last_sync_time
            else None,
            "last_sync_stats": self._sync_stats,
            "app_id": self.app_id,
        }

    async def test_connection(self) -> bool:
        """Test connection to Intercom API."""
        return await self.intercom.test_connection()


class SyncManager:
    """Manages the sync service lifecycle in a separate thread."""

    def __init__(
        self, database_manager: DatabaseManager, intercom_client: IntercomClient
    ):
        self.sync_service = SyncService(database_manager, intercom_client)
        self._loop = None
        self._thread = None
        self._started = False

    def start(self):
        """Start the sync service in a background thread."""
        if self._started:
            return

        def run_sync_service():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self.sync_service.start_background_sync())

            try:
                self._loop.run_forever()
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=run_sync_service, daemon=True)
        self._thread.start()
        self._started = True
        logger.info("Sync manager started in background thread")

    def stop(self):
        """Stop the sync service."""
        if not self._started or not self._loop:
            return

        try:
            # Schedule stop on the event loop and wait for completion
            future = asyncio.run_coroutine_threadsafe(
                self.sync_service.stop_background_sync(), self._loop
            )
            future.result(timeout=5)  # Wait up to 5 seconds
        except Exception as e:
            logger.warning(f"Error stopping sync service: {e}")

        # Stop the event loop
        with contextlib.suppress(Exception):
            self._loop.call_soon_threadsafe(self._loop.stop)

        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

        self._started = False
        self._loop = None
        logger.info("Sync manager stopped")

    def get_sync_service(self) -> SyncService:
        """Get the sync service instance."""
        return self.sync_service
