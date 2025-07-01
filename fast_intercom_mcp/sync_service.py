"""Background sync service for keeping conversation data up to date."""

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from .database import DatabaseManager
from .intercom_client import IntercomClient
from .models import SyncStateException, SyncStats
from .sync.coordinator import TwoPhaseConfig, TwoPhaseSyncCoordinator

logger = logging.getLogger(__name__)

# Import progress broadcasting (optional, graceful fallback)
try:
    from .core.progress import (
        get_progress_broadcaster,
        start_operation,
        update_progress,
        complete_operation,
        ProgressType
    )
    PROGRESS_AVAILABLE = True
except ImportError:
    PROGRESS_AVAILABLE = False


class SyncService:
    """Manages background synchronization of Intercom conversations."""

    def __init__(self, database_manager: DatabaseManager, intercom_client: IntercomClient):
        self.db = database_manager
        self.intercom = intercom_client
        self.app_id = None

        # Sync state
        self._sync_active = False
        self._current_operation = None
        self._last_sync_time = None
        self._sync_stats = {}
        self._sync_errors = []

        # Progress tracking
        self._progress_callbacks: list[Callable] = []

        # Background task management
        self._background_task = None
        self._shutdown_event = asyncio.Event()

        # Sync settings
        self.max_sync_age_minutes = 5  # Trigger sync if data is older than this
        self.background_sync_interval_minutes = 10  # Check for sync needs every 10 minutes

        # Two-phase coordinator for advanced operations
        self.two_phase_coordinator = TwoPhaseSyncCoordinator(
            intercom_client, database_manager, TwoPhaseConfig()
        )

        # Progress tracking
        self._last_progress_time = 0
        self._progress_update_interval = 3  # seconds - more responsive updates

    def add_progress_callback(self, callback: Callable):
        """Add a progress callback for sync operations."""
        self._progress_callbacks.append(callback)
        self.two_phase_coordinator.set_progress_callback(self._broadcast_progress_simple)

    async def _broadcast_progress_simple(self, message: str):
        """Broadcast simple string progress messages."""
        # Convert simple messages to detailed progress when possible
        for callback in self._progress_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(message)
                else:
                    callback(message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    async def _broadcast_progress(
        self, current_count: int, estimated_total: int, elapsed_seconds: float
    ):
        """Broadcast progress to all registered callbacks with detailed statistics."""
        for callback in self._progress_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(current_count, estimated_total, elapsed_seconds)
                else:
                    callback(current_count, estimated_total, elapsed_seconds)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    def _format_time(self, seconds: float) -> str:
        """Format seconds into human-readable time."""
        if seconds < 60:
            return f"{int(seconds)}s"
        if seconds < 3600:
            minutes = int(seconds / 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        return f"{hours}h {minutes}m"

    async def _update_progress_if_needed(
        self, current_count: int, estimated_total: int, start_time: float
    ):
        """Update progress if enough time has passed since last update."""
        current_time = time.time()
        elapsed_seconds = current_time - start_time

        # Update every 10-30 seconds or on first/last item
        if (
            current_time - self._last_progress_time >= self._progress_update_interval
            or current_count == 1
            or current_count == estimated_total
        ):
            self._last_progress_time = current_time

            # Calculate rate and ETA
            if elapsed_seconds > 0:
                rate = current_count / elapsed_seconds
                remaining = estimated_total - current_count
                eta_seconds = remaining / rate if rate > 0 else 0

                # Log detailed progress for debugging
                batch_info = ""
                if hasattr(self, "_sync_batch_number"):
                    batch_info = f"Batch {self._sync_batch_number} | "

                # Display progress without misleading percentages when we exceed estimate
                if current_count <= estimated_total:
                    percentage = f"({(current_count / estimated_total) * 100:.1f}%)"
                    eta_display = f"ETA: {self._format_time(eta_seconds)}"
                else:
                    # We've exceeded our estimate - show actual count
                    percentage = f"({current_count:,} total)"
                    eta_display = "Processing additional conversations..."

                logger.info(
                    f"⏳ Syncing: {current_count:,} conversations {percentage} | "
                    f"{batch_info}"
                    f"{rate:.1f} conv/sec | "
                    f"{eta_display}"
                )

            # Broadcast to callbacks
            await self._broadcast_progress(current_count, estimated_total, elapsed_seconds)

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
                self._sync_errors.append(
                    {
                        "timestamp": datetime.now(),
                        "error": str(e),
                        "operation": "background_sync",
                    }
                )
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
        stale_request_timeframes = self.db.get_stale_timeframes(self.max_sync_age_minutes)

        if stale_request_timeframes:
            logger.info(
                f"Found {len(stale_request_timeframes)} request-triggered timeframes needing sync"
            )
            for start, end in stale_request_timeframes[:2]:  # Limit to 2 to avoid overwhelming API
                await self.sync_period(start, end, is_background=True)

        # Priority 2: Check legacy period-based syncing
        stale_periods = self.db.get_periods_needing_sync(self.max_sync_age_minutes)

        if (
            stale_periods and not stale_request_timeframes
        ):  # Only if no request-triggered syncs needed
            logger.info(f"Found {len(stale_periods)} stale periods, triggering background sync")
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
                logger.info(f"Only {today_count} conversations found for today, syncing full day")
                await self.sync_period(today_start, now, is_background=True)
            else:
                # We have some data, just sync recent hour
                recent_start = now - timedelta(hours=1)
                await self.sync_period(recent_start, now, is_background=True)

    async def sync_if_needed(self, start_date: datetime | None, end_date: datetime | None):
        """
        Smart sync based on 3-state sync logic.

        States:
        - 'stale': Data is too old, trigger sync and wait
        - 'partial': Data is incomplete but usable, proceed with warning
        - 'fresh': Data is current, proceed normally
        """
        # Check sync state using intelligent logic
        sync_info = self.db.check_sync_state(start_date, end_date, self.max_sync_age_minutes)
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

    async def sync_recent(
        self, progress_callback: Callable[[int, int, float], None] = None
    ) -> SyncStats:
        """Sync conversations from the last few hours."""
        now = datetime.now()
        since = now - timedelta(hours=6)  # Last 6 hours
        return await self.sync_incremental(since, progress_callback=progress_callback)

    async def sync_period(
        self,
        start_date: datetime,
        end_date: datetime,
        is_background: bool = False,
        progress_callback: Callable[[int, int, float], None] = None,
    ) -> SyncStats:
        """Sync all conversations in a specific time period with enhanced progress broadcasting."""
        if self._sync_active and not is_background:
            raise Exception("Sync already in progress")

        self._sync_active = True
        operation_name = f"Syncing {start_date.strftime('%m/%d')} to {end_date.strftime('%m/%d')}"
        self._current_operation = operation_name

        # Start operation with progress broadcasting
        if PROGRESS_AVAILABLE:
            start_operation(
                "sync_period",
                f"Conversation sync from {start_date.date()} to {end_date.date()}",
                estimated_items=None  # Will be updated as we discover
            )

        try:
            start_time = time.time()
            logger.info(f"Starting period sync: {start_date} to {end_date}")

            # Broadcast initial progress 
            await self._broadcast_progress_simple(
                f"🔄 Starting sync from {start_date.date()} to {end_date.date()}..."
            )
            
            if PROGRESS_AVAILABLE:
                update_progress(
                    f"Initializing sync for {start_date.date()} to {end_date.date()}",
                    phase="initialization"
                )

            # We'll track progress dynamically as we go
            total_estimated = 100  # Start with a conservative estimate

            # Display sync information
            days_syncing = (end_date - start_date).days
            if days_syncing <= 1:
                expected_total = "50-150"
            elif days_syncing <= 7:
                expected_total = f"{50 * days_syncing}-{150 * days_syncing}"
            else:
                expected_total = f"{50 * days_syncing}-{150 * days_syncing}"

            logger.info("📊 Sync Information:")
            logger.info(
                f"  Date range: {start_date.date()} to {end_date.date()} ({days_syncing} day{'s' if days_syncing != 1 else ''})"
            )
            logger.info(f"  Timezone: Using {start_date.tzinfo or 'local time'}")
            logger.info("  Sync mode: Activity-based (using 'updated_at' field)")
            logger.info(f"  Expected conversations with activity: ~{expected_total}")
            if days_syncing <= 7:  # Only show warning for short syncs where discrepancy is obvious
                logger.warning("⚠️  NOTE: If seeing thousands instead of ~150/day, there may be:")
                logger.warning("  - Automated system updates (tags, assignments, etc.)")
                logger.warning("  - Timezone mismatches")
                logger.warning("  - API behavior differences")

            # Add progress callback to local callbacks if provided
            if progress_callback:
                self.add_progress_callback(progress_callback)

            # Track batch progress
            self._sync_batch_number = 0
            self._sync_total_batches = 1  # Will be updated dynamically

            # Fetch conversations from Intercom with progress monitoring
            async def period_progress_callback(message: str):
                # Extract conversation count from message for progress tracking
                if "Fetched" in message and "conversations" in message:
                    try:
                        # Parse message like "Fetched 50 conversations from 2024-01-01 to
                        # 2024-01-02"
                        parts = message.split()
                        current_count = int(parts[1])
                        self._sync_batch_number = (current_count + 149) // 150

                        # Update batch number dynamically
                        # If we exceed the estimate, adjust it
                        actual_total = max(current_count, total_estimated)
                        self._sync_total_batches = max(
                            self._sync_batch_number, self._sync_total_batches
                        )

                        await self._update_progress_if_needed(
                            current_count, actual_total, start_time
                        )
                    except (ValueError, IndexError):
                        pass

            conversations = await self.intercom.fetch_conversations_for_period(
                start_date, end_date, period_progress_callback
            )

            # Final progress update for storage phase
            total_conversations = len(conversations)
            if total_conversations > 0:
                await self._update_progress_if_needed(
                    total_conversations, total_conversations, start_time
                )

            # Store in database
            await self._broadcast_progress_simple(
                f"Storing {len(conversations)} conversations in database..."
            )
            stored_count = self.db.store_conversations(conversations)

            # Record sync period
            updated_count = max(
                0, total_conversations - stored_count
            )  # Approximate updated conversations
            self.db.record_sync_period(
                start_date, end_date, total_conversations, stored_count, updated_count
            )

            duration_seconds = time.time() - start_time

            # Calculate per-date breakdown
            conversations_by_date = {}
            messages_by_date = {}
            for conv in conversations:
                # Use updated_at date for grouping (matches our query logic)
                date_key = conv.updated_at.date()
                conversations_by_date[date_key] = conversations_by_date.get(date_key, 0) + 1
                messages_by_date[date_key] = messages_by_date.get(date_key, 0) + len(conv.messages)

            stats = SyncStats(
                total_conversations=total_conversations,
                new_conversations=stored_count,
                updated_conversations=updated_count,
                total_messages=sum(len(conv.messages) for conv in conversations),
                duration_seconds=duration_seconds,
                api_calls_made=1,  # At least one search API call
                conversations_by_date=conversations_by_date,
                messages_by_date=messages_by_date,
            )

            self._last_sync_time = datetime.now()
            self._sync_stats = stats.__dict__

            logger.info(
                f"Period sync completed: {stats.total_conversations} conversations, "
                f"{stats.total_messages} messages in {stats.duration_seconds:.1f}s"
            )
            await self._broadcast_progress_simple(
                f"Sync completed: {stats.total_conversations} conversations, "
                f"{stats.total_messages} messages"
            )
            return stats

        except Exception as e:
            logger.error(f"Period sync failed: {e}")
            self._sync_errors.append(
                {
                    "timestamp": datetime.now(),
                    "error": str(e),
                    "operation": f"period_sync_{start_date}_{end_date}",
                }
            )
            raise
        finally:
            self._sync_active = False
            self._current_operation = None

    async def sync_incremental(
        self, since: datetime, progress_callback: Callable[[int, int, float], None] = None
    ) -> SyncStats:
        """Sync conversations updated since the given timestamp."""
        if self._sync_active:
            raise Exception("Sync already in progress")

        self._sync_active = True
        self._current_operation = f"Incremental sync since {since.strftime('%m/%d %H:%M')}"

        try:
            start_time = time.time()
            logger.info(f"Starting incremental sync since {since}")

            # Add progress callback to local callbacks if provided
            if progress_callback:
                self.add_progress_callback(progress_callback)

            # Use the incremental sync method (this already returns SyncStats)
            stats = await self.intercom.fetch_conversations_incremental(since)

            # Update duration and progress
            duration_seconds = time.time() - start_time
            stats.duration_seconds = duration_seconds

            # Final progress update
            if stats.total_conversations > 0:
                await self._update_progress_if_needed(
                    stats.total_conversations, stats.total_conversations, start_time
                )

            self._last_sync_time = datetime.now()
            self._sync_stats = stats.__dict__

            logger.info(
                f"Incremental sync completed: {stats.total_conversations} conversations "
                f"in {stats.duration_seconds:.1f}s"
            )
            return stats

        finally:
            self._sync_active = False
            self._current_operation = None

    async def sync_period_two_phase(
        self,
        start_date: datetime,
        end_date: datetime,
        is_background: bool = False,
        progress_callback: Callable[[int, int, float], None] = None,
    ) -> SyncStats:
        """Two-phase sync: search for conversations, then fetch complete threads."""
        if self._sync_active and not is_background:
            raise Exception("Sync already in progress")

        self._sync_active = True
        self._current_operation = (
            f"Two-phase sync {start_date.strftime('%m/%d')} to {end_date.strftime('%m/%d')}"
        )

        try:
            start_time = time.time()
            logger.info(f"Starting two-phase sync: {start_date} to {end_date}")

            # Add progress callback to local callbacks if provided
            if progress_callback:
                self.add_progress_callback(progress_callback)

            # Set up progress callback for two-phase coordinator
            async def coordinator_progress_callback(message: str):
                # Extract progress information from coordinator messages
                # Messages like "Phase 1: Found 50 conversations to sync"
                # or "Phase 2: Fetching complete threads: 20/50"
                try:
                    if "Phase 2:" in message and "/" in message:
                        # Parse "Phase 2: Fetching complete threads: 20/50"
                        parts = message.split(":")[-1].strip().split("/")
                        if len(parts) == 2:
                            current = int(parts[0].split()[-1])
                            total = int(parts[1])
                            await self._update_progress_if_needed(current, total, start_time)
                    elif "Found" in message and "conversations" in message:
                        # Parse "Phase 1: Found 50 conversations to sync"
                        words = message.split()
                        if "Found" in words:
                            idx = words.index("Found")
                            if idx + 1 < len(words):
                                total = int(words[idx + 1])
                                await self._update_progress_if_needed(1, total, start_time)
                except (ValueError, IndexError):
                    pass

            self.two_phase_coordinator.set_progress_callback(coordinator_progress_callback)

            # Use two-phase coordinator
            stats = await self.two_phase_coordinator.sync_period_two_phase(
                start_date, end_date, force_refetch=False
            )

            # Update duration
            stats.duration_seconds = time.time() - start_time

            # Final progress update
            if stats.total_conversations > 0:
                await self._update_progress_if_needed(
                    stats.total_conversations, stats.total_conversations, start_time
                )

            self._last_sync_time = datetime.now()
            self._sync_stats = stats.__dict__

            logger.info(
                f"Two-phase sync completed: {stats.total_conversations} conversations, "
                f"{stats.total_messages} messages in {stats.duration_seconds:.1f}s"
            )
            return stats

        finally:
            self._sync_active = False
            self._current_operation = None

    async def sync_initial(
        self, days_back: int = 30, progress_callback: Callable[[int, int, float], None] = None
    ) -> SyncStats:
        """Perform initial sync of conversation history.

        Args:
            days_back: Number of days of history to sync (default: 30, max: 30)
            progress_callback: Optional progress callback
        """
        # Limit to 30 days max for initial sync
        days_back = min(days_back, 30)

        now = datetime.now()
        start_date = now - timedelta(days=days_back)

        logger.info(f"Starting initial sync: {days_back} days of history")
        return await self.sync_period(start_date, now, progress_callback=progress_callback)

    def get_status(self) -> dict[str, Any]:
        """Get current enhanced sync service status."""
        return {
            "active": self._sync_active,
            "current_operation": self._current_operation,
            "last_sync_time": self._last_sync_time.isoformat() if self._last_sync_time else None,
            "last_sync_stats": self._sync_stats,
            "app_id": self.app_id,
            "recent_errors": self._sync_errors[-5:],  # Last 5 errors
            "progress_callbacks_count": len(self._progress_callbacks),
            "two_phase_status": self.two_phase_coordinator.get_operation_status(),
            "enhanced_features": [
                "progress_tracking",
                "error_collection",
                "two_phase_sync_coordination",
                "background_sync_management",
                "enhanced_status_reporting",
            ],
        }

    async def test_connection(self) -> bool:
        """Test connection to Intercom API."""
        return await self.intercom.test_connection()


class SyncManager:
    """Manages the sync service lifecycle in a separate thread."""

    def __init__(self, database_manager: DatabaseManager, intercom_client: IntercomClient):
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
