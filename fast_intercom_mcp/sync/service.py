"""Enhanced sync service with full conversation thread support."""

import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ..database import DatabaseManager
from ..intercom_client import IntercomClient
from ..models import SyncStateException, SyncStats
from .strategies import FullThreadSyncStrategy, IncrementalSyncStrategy, SmartSyncStrategy

logger = logging.getLogger(__name__)


class EnhancedSyncService:
    """Enhanced sync service with full conversation thread support."""

    def __init__(self, database_manager: DatabaseManager, intercom_client: IntercomClient):
        self.db = database_manager
        self.intercom = intercom_client
        self.app_id = None

        # Sync strategies
        self.full_strategy = FullThreadSyncStrategy(intercom_client, database_manager)
        self.incremental_strategy = IncrementalSyncStrategy(intercom_client, database_manager)
        self.smart_strategy = SmartSyncStrategy(intercom_client, database_manager)

        # Sync state
        self._sync_active = False
        self._current_operation = None
        self._last_sync_time = None
        self._sync_stats = {}
        self._sync_errors = []

        # Background task management
        self._background_task = None
        self._shutdown_event = asyncio.Event()

        # Sync settings
        self.max_sync_age_minutes = 5  # Trigger sync if data is older than this
        self.background_sync_interval_minutes = 10  # Check for sync needs every 10 minutes

        # Progress tracking
        self._progress_callbacks: list[Callable] = []

    def add_progress_callback(self, callback: Callable):
        """Add a progress callback for sync operations."""
        self._progress_callbacks.append(callback)
        self.full_strategy.set_progress_callback(self._broadcast_progress)
        self.incremental_strategy.set_progress_callback(self._broadcast_progress)
        self.smart_strategy.set_progress_callback(self._broadcast_progress)

    async def _broadcast_progress(self, message: str):
        """Broadcast progress to all registered callbacks."""
        for callback in self._progress_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(message)
                else:
                    callback(message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    async def start_background_sync(self):
        """Start the background sync service."""
        if self._background_task and not self._background_task.done():
            logger.warning("Background sync already running")
            return

        logger.info("Starting enhanced background sync service")
        self._background_task = asyncio.create_task(self._background_sync_loop())

        # Initialize app_id
        self.app_id = await self.intercom.get_app_id()

    async def stop_background_sync(self):
        """Stop the background sync service."""
        logger.info("Stopping enhanced background sync service")
        self._shutdown_event.set()

        if self._background_task:
            try:
                await asyncio.wait_for(self._background_task, timeout=10.0)
            except TimeoutError:
                logger.warning("Background sync task did not stop gracefully")
                self._background_task.cancel()

    async def _background_sync_loop(self):
        """Main background sync loop with enhanced capabilities."""
        while not self._shutdown_event.is_set():
            try:
                # Check if we need to sync recent data
                await self._check_and_sync_recent()

                # Wait for next check
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.background_sync_interval_minutes * 60
                    )
                    break  # Shutdown requested
                except TimeoutError:
                    pass  # Continue loop

            except Exception as e:
                logger.error(f"Background sync error: {e}")
                self._sync_errors.append({
                    "timestamp": datetime.now(),
                    "error": str(e),
                    "operation": "background_sync"
                })
                # Wait a bit before retrying
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=60)
                    break
                except TimeoutError:
                    pass

    async def _check_and_sync_recent(self):
        """Check if recent data needs syncing using enhanced strategies."""
        if self._sync_active:
            logger.debug("Sync already active, skipping background check")
            return

        # Priority 1: Check for request-triggered timeframes that need syncing
        stale_request_timeframes = self.db.get_stale_timeframes(self.max_sync_age_minutes)

        if stale_request_timeframes:
            logger.info(f"Found {len(stale_request_timeframes)} request-triggered timeframes needing sync")
            for start, end in stale_request_timeframes[:2]:  # Limit to 2 to avoid overwhelming API
                await self.sync_period_enhanced(start, end, is_background=True)

        # Priority 2: Check legacy period-based syncing
        stale_periods = self.db.get_periods_needing_sync(self.max_sync_age_minutes)

        if stale_periods and not stale_request_timeframes:  # Only if no request-triggered syncs needed
            logger.info(f"Found {len(stale_periods)} stale periods, triggering background sync")
            for start, end in stale_periods[:2]:  # Limit to 2 periods
                await self.sync_period_enhanced(start, end, is_background=True)

        # Priority 3: Enhanced background sync for better coverage
        if not stale_request_timeframes and not stale_periods:
            now = datetime.now()

            # Check if we have recent data
            recent_start = now - timedelta(hours=2)
            sync_state = self.db.check_sync_state(recent_start, now, self.max_sync_age_minutes)

            if sync_state["sync_state"] in ["stale", "partial"]:
                logger.info("Recent data needs refresh, triggering background sync")
                await self.sync_period_enhanced(recent_start, now, is_background=True)

    async def sync_if_needed(self, start_date: datetime | None, end_date: datetime | None):
        """Smart sync based on enhanced 3-state sync logic."""
        # Check sync state using intelligent logic
        sync_info = self.db.check_sync_state(start_date, end_date, self.max_sync_age_minutes)
        sync_state = sync_info["sync_state"]

        logger.info(f"Sync state check: {sync_state}")
        if sync_info.get("message"):
            logger.info(f"Sync message: {sync_info['message']}")

        # Handle different sync states
        if sync_state == "stale" and sync_info.get("should_sync"):
            # State 1: Data is too stale - trigger sync and wait
            logger.warning(f"Data is stale, triggering enhanced sync: {sync_info['message']}")

            if not start_date or not end_date:
                # No specific range, sync recent data
                await self.sync_recent_enhanced()
            else:
                # Sync specific period with enhanced strategy
                try:
                    await self.sync_period_enhanced(start_date, end_date)
                    logger.info("Enhanced sync completed, data is now fresh")
                except Exception as e:
                    # If sync fails, raise exception to inform user
                    raise SyncStateException(
                        f"Data is stale and enhanced sync failed: {str(e)}",
                        sync_state="stale",
                        last_sync=sync_info.get("last_sync")
                    )

        elif sync_state == "partial":
            # State 2: Partial data - proceed but log warning
            logger.warning(f"Proceeding with partial data: {sync_info['message']}")

        # State 3: Fresh data - proceed normally (no action needed)
        elif sync_state == "fresh":
            logger.debug("Using fresh cached data")

        return sync_info

    async def sync_recent_enhanced(self) -> SyncStats:
        """Sync recent conversations using enhanced strategies."""
        now = datetime.now()
        since = now - timedelta(hours=6)  # Last 6 hours
        return await self.sync_incremental_enhanced(since)

    async def sync_period_enhanced(self, start_date: datetime, end_date: datetime,
                                 is_background: bool = False,
                                 force_full_threads: bool = False) -> SyncStats:
        """Enhanced period sync with full conversation thread support.
        
        Args:
            start_date: Start of time period
            end_date: End of time period
            is_background: Whether this is a background sync
            force_full_threads: Force full thread fetching even for incremental syncs
            
        Returns:
            Sync statistics
        """
        if self._sync_active and not is_background:
            raise Exception("Sync already in progress")

        self._sync_active = True
        self._current_operation = f"Enhanced sync {start_date.strftime('%m/%d')} to {end_date.strftime('%m/%d')}"

        try:
            logger.info(f"Starting enhanced period sync: {start_date} to {end_date}")

            # Choose strategy based on requirements
            if force_full_threads:
                strategy = self.full_strategy
                logger.info("Using full thread sync strategy (forced)")
            else:
                strategy = self.smart_strategy
                logger.info("Using smart sync strategy")

            # Execute sync with progress tracking
            stats = await strategy.sync_period(start_date, end_date)

            # Record sync period in database
            self.db.record_sync_period(
                start_date, end_date, stats.total_conversations,
                stats.new_conversations, stats.updated_conversations
            )

            # Update internal state
            self._last_sync_time = datetime.now()
            self._sync_stats = stats.__dict__

            logger.info(f"Enhanced period sync completed: {stats.total_conversations} conversations, "
                       f"{stats.total_messages} messages in {stats.duration_seconds:.1f}s")
            return stats

        except Exception as e:
            logger.error(f"Enhanced sync failed: {e}")
            self._sync_errors.append({
                "timestamp": datetime.now(),
                "error": str(e),
                "operation": f"period_sync_{start_date}_{end_date}"
            })
            raise
        finally:
            self._sync_active = False
            self._current_operation = None

    async def sync_incremental_enhanced(self, since: datetime,
                                      until: datetime | None = None) -> SyncStats:
        """Enhanced incremental sync with full thread support."""
        if self._sync_active:
            raise Exception("Sync already in progress")

        self._sync_active = True
        self._current_operation = f"Enhanced incremental sync since {since.strftime('%m/%d %H:%M')}"

        try:
            logger.info(f"Starting enhanced incremental sync since {since}")

            # Use incremental strategy
            stats = await self.incremental_strategy.sync_since(since, until)

            self._last_sync_time = datetime.now()
            self._sync_stats = stats.__dict__

            logger.info(f"Enhanced incremental sync completed: {stats.total_conversations} conversations")
            return stats

        except Exception as e:
            logger.error(f"Enhanced incremental sync failed: {e}")
            self._sync_errors.append({
                "timestamp": datetime.now(),
                "error": str(e),
                "operation": f"incremental_sync_{since}"
            })
            raise
        finally:
            self._sync_active = False
            self._current_operation = None

    async def sync_full_threads_for_conversations(self, conversation_ids: list[str]) -> SyncStats:
        """Sync full threads for specific conversations.
        
        Args:
            conversation_ids: List of conversation IDs to fetch complete threads for
            
        Returns:
            Sync statistics
        """
        if self._sync_active:
            raise Exception("Sync already in progress")

        self._sync_active = True
        self._current_operation = f"Full thread sync for {len(conversation_ids)} conversations"

        try:
            logger.info(f"Starting full thread sync for {len(conversation_ids)} conversations")
            start_time = datetime.now()

            # Fetch complete conversations
            complete_conversations = await self.intercom.fetch_individual_conversations(
                conversation_ids, self._broadcast_progress
            )

            # Store in database
            stored_count = self.db.store_conversations(complete_conversations)

            duration = (datetime.now() - start_time).total_seconds()

            stats = SyncStats(
                total_conversations=len(complete_conversations),
                new_conversations=stored_count,
                updated_conversations=0,  # Simplified
                total_messages=sum(len(conv.messages) for conv in complete_conversations),
                duration_seconds=duration,
                api_calls_made=len(conversation_ids)
            )

            self._last_sync_time = datetime.now()
            self._sync_stats = stats.__dict__

            logger.info(f"Full thread sync completed: {stats.total_conversations} conversations")
            return stats

        except Exception as e:
            logger.error(f"Full thread sync failed: {e}")
            self._sync_errors.append({
                "timestamp": datetime.now(),
                "error": str(e),
                "operation": f"full_threads_{len(conversation_ids)}"
            })
            raise
        finally:
            self._sync_active = False
            self._current_operation = None

    async def sync_initial(self, days_back: int = 30, force_full_threads: bool = True) -> SyncStats:
        """Perform initial sync of conversation history with enhanced capabilities.
        
        Args:
            days_back: Number of days of history to sync (default: 30, max: 30)
            force_full_threads: Whether to force full thread fetching
        """
        # Limit to 30 days max for initial sync
        days_back = min(days_back, 30)

        now = datetime.now()
        start_date = now - timedelta(days=days_back)

        logger.info(f"Starting enhanced initial sync: {days_back} days of history")
        return await self.sync_period_enhanced(start_date, now,
                                             force_full_threads=force_full_threads)

    def get_status(self) -> dict[str, Any]:
        """Get current enhanced sync service status."""
        return {
            'active': self._sync_active,
            'current_operation': self._current_operation,
            'last_sync_time': self._last_sync_time.isoformat() if self._last_sync_time else None,
            'last_sync_stats': self._sync_stats,
            'app_id': self.app_id,
            'recent_errors': self._sync_errors[-5:],  # Last 5 errors
            'strategies_available': ['full_thread', 'incremental', 'smart'],
            'enhanced_features': [
                'full_conversation_threads',
                'message_deduplication',
                'progress_tracking',
                'error_recovery',
                'smart_strategy_selection'
            ]
        }

    async def test_connection(self) -> bool:
        """Test connection to Intercom API."""
        return await self.intercom.test_connection()


class EnhancedSyncManager:
    """Enhanced sync manager with full thread capabilities."""

    def __init__(self, database_manager: DatabaseManager, intercom_client: IntercomClient):
        self.sync_service = EnhancedSyncService(database_manager, intercom_client)
        self._loop = None
        self._thread = None
        self._started = False

    def start(self):
        """Start the enhanced sync service in a background thread."""
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
        logger.info("Enhanced sync manager started in background thread")

    def stop(self):
        """Stop the enhanced sync service."""
        if not self._started or not self._loop:
            return

        try:
            # Schedule stop on the event loop and wait for completion
            future = asyncio.run_coroutine_threadsafe(
                self.sync_service.stop_background_sync(),
                self._loop
            )
            future.result(timeout=5)  # Wait up to 5 seconds
        except Exception as e:
            logger.warning(f"Error stopping sync service: {e}")

        # Stop the event loop
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

        self._started = False
        self._loop = None
        logger.info("Enhanced sync manager stopped")

    def get_sync_service(self) -> EnhancedSyncService:
        """Get the enhanced sync service instance."""
        return self.sync_service
