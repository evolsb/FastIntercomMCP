"""Two-phase sync coordinator for managing search and fetch operations."""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..database import DatabaseManager
from ..intercom_client import IntercomClient
from ..models import Conversation, SyncStats

logger = logging.getLogger(__name__)


class TwoPhaseConfig:
    """Configuration for two-phase sync operations."""

    def __init__(
        self,
        search_batch_size: int = 150,
        fetch_batch_size: int = 10,
        search_timeout: int = 300,
        fetch_timeout: int = 600,
        max_concurrent_fetches: int = 5,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
    ):
        self.search_batch_size = search_batch_size
        self.fetch_batch_size = fetch_batch_size
        self.search_timeout = search_timeout
        self.fetch_timeout = fetch_timeout
        self.max_concurrent_fetches = max_concurrent_fetches
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay


class SyncPhaseResult:
    """Result from a sync phase operation."""

    def __init__(
        self,
        phase_name: str,
        success: bool,
        items_processed: int,
        duration_seconds: float,
        api_calls: int,
        errors: list[str] = None,
    ):
        self.phase_name = phase_name
        self.success = success
        self.items_processed = items_processed
        self.duration_seconds = duration_seconds
        self.api_calls = api_calls
        self.errors = errors or []


class TwoPhaseSyncCoordinator:
    """Coordinates two-phase sync operations: search + fetch."""

    def __init__(
        self,
        intercom_client: IntercomClient,
        database_manager: DatabaseManager,
        config: TwoPhaseConfig | None = None,
    ):
        self.intercom = intercom_client
        self.db = database_manager
        self.config = config or TwoPhaseConfig()

        # State tracking
        self._active_operation = None
        self._phase_results: list[SyncPhaseResult] = []
        self._progress_callback: Callable | None = None

        # Phase-specific state
        self._discovered_conversations: set[str] = set()
        self._fetched_conversations: set[str] = set()
        self._failed_fetches: set[str] = set()

    def set_progress_callback(self, callback: Callable):
        """Set progress callback for operations."""
        self._progress_callback = callback

    async def _notify_progress(self, message: str):
        """Notify progress if callback is set."""
        if self._progress_callback:
            try:
                if asyncio.iscoroutinefunction(self._progress_callback):
                    await self._progress_callback(message)
                else:
                    self._progress_callback(message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    async def sync_period_two_phase(
        self, start_date: datetime, end_date: datetime, force_refetch: bool = False
    ) -> SyncStats:
        """Execute two-phase sync for a time period.

        Phase 1: Search for conversations in the time period
        Phase 2: Fetch individual complete conversation threads

        Args:
            start_date: Start of time period
            end_date: End of time period
            force_refetch: Force refetch of conversations already in database

        Returns:
            Combined sync statistics
        """
        operation_start = time.time()
        self._active_operation = (
            f"Two-phase sync {start_date.date()} to {end_date.date()}"
        )
        self._phase_results.clear()
        self._discovered_conversations.clear()
        self._fetched_conversations.clear()
        self._failed_fetches.clear()

        try:
            logger.info(f"Starting two-phase sync: {start_date} to {end_date}")
            await self._notify_progress(
                f"Starting two-phase sync for {start_date.date()} to {end_date.date()}"
            )

            # Phase 1: Discovery (Search)
            discovery_result = await self._execute_discovery_phase(start_date, end_date)

            if not discovery_result.success:
                raise Exception(
                    f"Discovery phase failed: {'; '.join(discovery_result.errors)}"
                )

            # Filter conversations based on database state and force_refetch flag
            conversations_to_fetch = await self._filter_conversations_for_fetch(
                list(self._discovered_conversations), force_refetch
            )

            if not conversations_to_fetch:
                logger.info(
                    "No conversations need fetching - all are current in database"
                )
                await self._notify_progress(
                    "All conversations are current - no fetching needed"
                )

                # Create summary stats from discovery only
                total_duration = time.time() - operation_start
                return self._create_summary_stats(
                    total_conversations=len(self._discovered_conversations),
                    fetched_conversations=0,
                    total_duration=total_duration,
                )

            # Phase 2: Detail Fetching
            await self._execute_fetch_phase(conversations_to_fetch)

            # Create comprehensive statistics
            total_duration = time.time() - operation_start
            stats = self._create_summary_stats(
                total_conversations=len(self._discovered_conversations),
                fetched_conversations=len(self._fetched_conversations),
                total_duration=total_duration,
            )

            logger.info(
                f"Two-phase sync completed: {stats.total_conversations} discovered, "
                f"{stats.new_conversations} fetched in {stats.duration_seconds:.1f}s"
            )

            return stats

        except Exception as e:
            logger.error(f"Two-phase sync failed: {e}")
            raise
        finally:
            self._active_operation = None

    async def _execute_discovery_phase(
        self, start_date: datetime, end_date: datetime
    ) -> SyncPhaseResult:
        """Execute Phase 1: Discover conversations via search API."""
        phase_start = time.time()
        api_calls = 0
        errors = []

        try:
            logger.info("Phase 1: Discovering conversations via search API")
            await self._notify_progress("Phase 1: Searching for conversations...")

            # Use the intercom client's search functionality
            conversations = await self.intercom.fetch_conversations_for_period(
                start_date, end_date, self._notify_progress
            )

            # Track discovered conversation IDs
            for conv in conversations:
                self._discovered_conversations.add(conv.id)

            # Store basic conversation data from search
            self.db.store_conversations(conversations)
            api_calls = len(conversations) // self.config.search_batch_size + 1

            duration = time.time() - phase_start
            result = SyncPhaseResult(
                phase_name="discovery",
                success=True,
                items_processed=len(conversations),
                duration_seconds=duration,
                api_calls=api_calls,
                errors=errors,
            )

            self._phase_results.append(result)
            logger.info(
                f"Discovery phase completed: {len(conversations)} conversations found in {duration:.1f}s"
            )
            await self._notify_progress(
                f"Phase 1 complete: Found {len(conversations)} conversations"
            )

            return result

        except Exception as e:
            duration = time.time() - phase_start
            error_msg = f"Discovery phase failed: {str(e)}"
            errors.append(error_msg)
            logger.error(error_msg)

            result = SyncPhaseResult(
                phase_name="discovery",
                success=False,
                items_processed=0,
                duration_seconds=duration,
                api_calls=api_calls,
                errors=errors,
            )
            self._phase_results.append(result)
            return result

    async def _filter_conversations_for_fetch(
        self, conversation_ids: list[str], force_refetch: bool
    ) -> list[str]:
        """Filter conversations to determine which need detailed fetching."""
        if force_refetch:
            logger.info(
                f"Force refetch enabled - will fetch all {len(conversation_ids)} conversations"
            )
            return conversation_ids

        # Check database for conversations that already have complete threads
        conversations_needing_fetch = []

        for conv_id in conversation_ids:
            # Check if conversation exists and has complete thread data
            existing_conv = self.db.get_conversation_by_id(conv_id)

            if not existing_conv:
                # Conversation not in database
                conversations_needing_fetch.append(conv_id)
            elif len(existing_conv.messages) <= 1:
                # Conversation has incomplete thread data (only initial message)
                conversations_needing_fetch.append(conv_id)
            # else: conversation has complete thread data, skip

        logger.info(
            f"Filtered conversations: {len(conversations_needing_fetch)} of {len(conversation_ids)} need fetching"
        )
        await self._notify_progress(
            f"Identified {len(conversations_needing_fetch)} conversations needing complete threads"
        )

        return conversations_needing_fetch

    async def _execute_fetch_phase(
        self, conversation_ids: list[str]
    ) -> SyncPhaseResult:
        """Execute Phase 2: Fetch complete conversation threads."""
        phase_start = time.time()
        api_calls = 0
        errors = []

        try:
            logger.info(
                f"Phase 2: Fetching complete threads for {len(conversation_ids)} conversations"
            )
            await self._notify_progress(
                f"Phase 2: Fetching {len(conversation_ids)} complete conversation threads..."
            )

            # Process in batches with concurrency control
            semaphore = asyncio.Semaphore(self.config.max_concurrent_fetches)

            async def fetch_batch(conv_ids_batch: list[str]) -> list[Conversation]:
                async with semaphore:
                    return await self.intercom.fetch_individual_conversations(
                        conv_ids_batch, self._notify_progress
                    )

            # Split into batches
            all_conversations = []
            for i in range(0, len(conversation_ids), self.config.fetch_batch_size):
                batch = conversation_ids[i : i + self.config.fetch_batch_size]

                try:
                    batch_conversations = await fetch_batch(batch)
                    all_conversations.extend(batch_conversations)

                    # Track successfully fetched conversations
                    for conv in batch_conversations:
                        self._fetched_conversations.add(conv.id)

                    api_calls += len(batch)

                    await self._notify_progress(
                        f"Fetched {len(all_conversations)}/{len(conversation_ids)} conversation threads"
                    )

                except Exception as e:
                    error_msg = (
                        f"Batch fetch failed for {len(batch)} conversations: {str(e)}"
                    )
                    errors.append(error_msg)
                    logger.warning(error_msg)

                    # Track failed conversations
                    for conv_id in batch:
                        self._failed_fetches.add(conv_id)

            # Store complete conversations in database
            if all_conversations:
                stored_count = self.db.store_conversations(all_conversations)
                logger.info(f"Stored {stored_count} complete conversation threads")

            duration = time.time() - phase_start
            success = len(self._fetched_conversations) > 0 or len(conversation_ids) == 0

            result = SyncPhaseResult(
                phase_name="fetch",
                success=success,
                items_processed=len(all_conversations),
                duration_seconds=duration,
                api_calls=api_calls,
                errors=errors,
            )

            self._phase_results.append(result)
            logger.info(
                f"Fetch phase completed: {len(all_conversations)} threads fetched in {duration:.1f}s"
            )
            await self._notify_progress(
                f"Phase 2 complete: Fetched {len(all_conversations)} complete threads"
            )

            return result

        except Exception as e:
            duration = time.time() - phase_start
            error_msg = f"Fetch phase failed: {str(e)}"
            errors.append(error_msg)
            logger.error(error_msg)

            result = SyncPhaseResult(
                phase_name="fetch",
                success=False,
                items_processed=0,
                duration_seconds=duration,
                api_calls=api_calls,
                errors=errors,
            )
            self._phase_results.append(result)
            return result

    def _create_summary_stats(
        self,
        total_conversations: int,
        fetched_conversations: int,
        total_duration: float,
    ) -> SyncStats:
        """Create comprehensive sync statistics from phase results."""
        total_api_calls = sum(result.api_calls for result in self._phase_results)

        # Calculate message count from database (approximate)
        total_messages = fetched_conversations * 3  # Rough estimate

        return SyncStats(
            total_conversations=total_conversations,
            new_conversations=fetched_conversations,
            updated_conversations=0,  # Two-phase doesn't track updates separately
            total_messages=total_messages,
            duration_seconds=total_duration,
            api_calls_made=total_api_calls,
            sync_type="two_phase",
            period_start=None,  # Set by caller if needed
            period_end=None,  # Set by caller if needed
        )

    def get_operation_status(self) -> dict[str, Any]:
        """Get current operation status and phase results."""
        return {
            "active_operation": self._active_operation,
            "phase_results": [
                {
                    "phase": result.phase_name,
                    "success": result.success,
                    "items_processed": result.items_processed,
                    "duration_seconds": result.duration_seconds,
                    "api_calls": result.api_calls,
                    "errors": result.errors,
                }
                for result in self._phase_results
            ],
            "discovered_conversations": len(self._discovered_conversations),
            "fetched_conversations": len(self._fetched_conversations),
            "failed_fetches": len(self._failed_fetches),
            "config": {
                "search_batch_size": self.config.search_batch_size,
                "fetch_batch_size": self.config.fetch_batch_size,
                "max_concurrent_fetches": self.config.max_concurrent_fetches,
            },
        }
