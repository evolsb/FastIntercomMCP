"""Sync strategies for different types of conversation synchronization."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..database import DatabaseManager
from ..intercom_client import IntercomClient
from ..models import Conversation, SyncStats
from .detection import ConversationChangeDetector
from .incremental import IncrementalSync

logger = logging.getLogger(__name__)


@dataclass
class SyncProgress:
    """Progress information for sync operations."""

    phase: str
    current: int
    total: int
    message: str

    def get_percentage(self) -> float:
        """Get completion percentage."""
        if self.total == 0:
            return 100.0
        return (self.current / self.total) * 100.0


class FullThreadSyncStrategy:
    """Strategy for complete conversation thread synchronization.

    Uses a two-phase approach:
    1. Search phase: Find conversations in the time period
    2. Fetch phase: Get complete threads for each conversation
    """

    def __init__(self, intercom_client: IntercomClient, database: DatabaseManager):
        self.intercom = intercom_client
        self.db = database
        self.progress_callback: Callable | None = None

    def set_progress_callback(self, callback: Callable | None):
        """Set progress callback for real-time updates."""
        self.progress_callback = callback

    async def sync_period(self, start_date: datetime, end_date: datetime) -> SyncStats:
        """Sync all conversations in a period with complete threads.

        Args:
            start_date: Start of time period
            end_date: End of time period

        Returns:
            Sync statistics
        """
        start_time = datetime.now()

        # Phase 1: Search for conversations
        await self._update_progress("search", 0, 1, "Searching for conversations...")

        search_conversations = await self.intercom.fetch_conversations_for_period(
            start_date, end_date, self._search_progress_callback
        )

        if not search_conversations:
            await self._update_progress("complete", 1, 1, "No conversations found")
            return SyncStats(
                total_conversations=0,
                new_conversations=0,
                updated_conversations=0,
                total_messages=0,
                duration_seconds=(datetime.now() - start_time).total_seconds(),
                api_calls_made=1,
            )

        # Phase 2: Fetch complete threads
        await self._update_progress(
            "fetch",
            0,
            len(search_conversations),
            f"Fetching {len(search_conversations)} complete threads...",
        )

        conversation_ids = [conv.id for conv in search_conversations]
        complete_conversations = await self.intercom.fetch_individual_conversations(
            conversation_ids, self._fetch_progress_callback
        )

        # Phase 3: Merge and deduplicate
        await self._update_progress(
            "merge", 0, len(complete_conversations), "Merging conversation data..."
        )

        merged_conversations = self._merge_conversation_data(
            search_conversations, complete_conversations
        )

        # Phase 4: Store in database
        await self._update_progress(
            "store", 0, len(merged_conversations), "Storing conversations..."
        )

        stored_count = self.db.store_conversations(merged_conversations)

        duration = (datetime.now() - start_time).total_seconds()

        stats = SyncStats(
            total_conversations=len(merged_conversations),
            new_conversations=stored_count,  # Simplified - DB handles this
            updated_conversations=0,  # Simplified - DB handles this
            total_messages=sum(len(conv.messages) for conv in merged_conversations),
            duration_seconds=duration,
            api_calls_made=1 + len(conversation_ids),  # Search + individual fetches
        )

        await self._update_progress(
            "complete",
            1,
            1,
            f"Completed: {stats.total_conversations} conversations, "
            f"{stats.total_messages} messages in {duration:.1f}s",
        )

        return stats

    def _merge_conversation_data(
        self, search_results: list[Conversation], complete_threads: list[Conversation]
    ) -> list[Conversation]:
        """Merge data from search results with complete thread data.

        The search API may have different/incomplete message data compared to
        the individual conversation API, so we prioritize the complete thread data.
        """
        # Create a mapping of complete threads by ID
        {conv.id: conv for conv in complete_threads}

        merged = []
        seen_ids: set[str] = set()

        # Start with complete threads (they have the most accurate data)
        for conv in complete_threads:
            if conv.id not in seen_ids:
                merged.append(conv)
                seen_ids.add(conv.id)

        # Add any conversations from search that weren't in complete results
        for conv in search_results:
            if conv.id not in seen_ids:
                # Use search result as fallback
                merged.append(conv)
                seen_ids.add(conv.id)
                logger.warning(
                    f"Using search data for conversation {conv.id} - complete thread fetch may have failed"
                )

        return merged

    async def _search_progress_callback(self, message: str):
        """Progress callback for search phase."""
        if self.progress_callback:
            await self.progress_callback(f"Search: {message}")

    async def _fetch_progress_callback(self, message: str):
        """Progress callback for fetch phase."""
        if self.progress_callback:
            await self.progress_callback(f"Fetch: {message}")

    async def _update_progress(
        self, phase: str, current: int, total: int, message: str
    ):
        """Update progress with standardized format."""
        if self.progress_callback:
            progress = SyncProgress(phase, current, total, message)
            await self.progress_callback(
                f"{phase.capitalize()}: {message} ({current}/{total} - {progress.get_percentage():.1f}%)"
            )


class IncrementalSyncStrategy:
    """Enhanced strategy for incremental conversation synchronization.

    Uses change detection to identify only conversations that need updates,
    minimizing API calls and focusing on efficiency.
    """

    def __init__(self, intercom_client: IntercomClient, database: DatabaseManager):
        self.intercom = intercom_client
        self.db = database
        self.progress_callback: Callable | None = None

        # Initialize incremental sync and change detection components
        self.incremental_sync = IncrementalSync(intercom_client, database)
        self.change_detector = ConversationChangeDetector(intercom_client, database)

    def set_progress_callback(self, callback: Callable | None):
        """Set progress callback for real-time updates."""
        self.progress_callback = callback

    async def sync_since(
        self, since_timestamp: datetime, until_timestamp: datetime | None = None
    ) -> SyncStats:
        """Enhanced incremental sync with change detection.

        Args:
            since_timestamp: Only sync conversations updated after this time
            until_timestamp: Only sync conversations updated before this time (optional)

        Returns:
            Sync statistics
        """
        datetime.now()

        await self._update_progress("detect", 0, 1, "Detecting conversation changes...")

        # Use enhanced incremental sync
        incremental_stats = await self.incremental_sync.sync_incremental(
            since_timestamp, until_timestamp
        )

        # Convert to standard SyncStats format
        stats = SyncStats(
            total_conversations=incremental_stats.total_conversations_checked,
            new_conversations=0,  # Incremental sync doesn't add new conversations
            updated_conversations=incremental_stats.updated_conversations,
            total_messages=incremental_stats.new_messages_found,
            duration_seconds=incremental_stats.duration_seconds,
            api_calls_made=incremental_stats.api_calls_made,
        )

        await self._update_progress(
            "complete",
            1,
            1,
            f"Enhanced incremental sync completed: {stats.updated_conversations} conversations updated",
        )

        return stats

    async def detect_changes_only(
        self, since_timestamp: datetime, until_timestamp: datetime | None = None
    ) -> dict[str, Any]:
        """Detect changes without syncing (for analysis purposes).

        Args:
            since_timestamp: Look for changes since this time
            until_timestamp: Look for changes until this time

        Returns:
            Change detection results
        """
        if until_timestamp is None:
            until_timestamp = datetime.now()

        await self._update_progress(
            "analyze", 0, 1, "Analyzing conversation changes..."
        )

        # Detect changes in the timeframe
        detection_result = await self.change_detector.detect_changes_in_timeframe(
            since_timestamp, until_timestamp
        )

        # Analyze change patterns
        pattern_analysis = self.change_detector.analyze_change_patterns(
            detection_result.changes_detected
        )

        await self._update_progress(
            "complete",
            1,
            1,
            f"Change analysis completed: {len(detection_result.changes_detected)} changes detected",
        )

        return {
            "detection_result": detection_result,
            "pattern_analysis": pattern_analysis,
            "recommendations": self._generate_sync_recommendations(pattern_analysis),
        }

    def _generate_sync_recommendations(
        self, pattern_analysis: dict[str, Any]
    ) -> dict[str, Any]:
        """Generate sync recommendations based on change patterns."""
        changes_per_hour = pattern_analysis.get("average_changes_per_hour", 0)
        total_changes = pattern_analysis.get("total_changes", 0)

        if total_changes == 0:
            return {
                "recommended_frequency_minutes": 120,
                "reasoning": "No changes detected - infrequent sync is sufficient",
                "urgency": "low",
            }
        if changes_per_hour < 2:
            return {
                "recommended_frequency_minutes": 60,
                "reasoning": "Low change rate - hourly sync recommended",
                "urgency": "low",
            }
        if changes_per_hour < 10:
            return {
                "recommended_frequency_minutes": 30,
                "reasoning": "Moderate change rate - sync every 30 minutes",
                "urgency": "medium",
            }
        return {
            "recommended_frequency_minutes": 15,
            "reasoning": "High change rate - frequent sync needed",
            "urgency": "high",
        }

    async def _progress_callback(self, message: str):
        """Progress callback for incremental sync."""
        if self.progress_callback:
            await self.progress_callback(f"Incremental: {message}")

    async def _update_progress(
        self, phase: str, current: int, total: int, message: str
    ):
        """Update progress with standardized format."""
        if self.progress_callback:
            progress = SyncProgress(phase, current, total, message)
            await self.progress_callback(
                f"{phase.capitalize()}: {message} ({current}/{total} - {progress.get_percentage():.1f}%)"
            )


class SmartSyncStrategy:
    """Smart strategy that chooses the best sync approach based on context.

    Decides between full thread sync, incremental sync, or hybrid approaches
    based on factors like:
    - Time since last sync
    - Number of conversations to sync
    - API rate limit budget
    - Data completeness requirements
    """

    def __init__(self, intercom_client: IntercomClient, database: DatabaseManager):
        self.intercom = intercom_client
        self.db = database
        self.full_strategy = FullThreadSyncStrategy(intercom_client, database)
        self.incremental_strategy = IncrementalSyncStrategy(intercom_client, database)
        self.progress_callback: Callable | None = None

    def set_progress_callback(self, callback: Callable | None):
        """Set progress callback for real-time updates."""
        self.progress_callback = callback
        self.full_strategy.set_progress_callback(callback)
        self.incremental_strategy.set_progress_callback(callback)

    async def sync_period(
        self, start_date: datetime, end_date: datetime, force_full: bool = False
    ) -> SyncStats:
        """Smart sync for a time period.

        Args:
            start_date: Start of time period
            end_date: End of time period
            force_full: Force full thread sync even if incremental would be better

        Returns:
            Sync statistics
        """
        if force_full:
            await self._update_progress(
                "strategy", 0, 1, "Using full thread sync (forced)"
            )
            return await self.full_strategy.sync_period(start_date, end_date)

        # Analyze sync requirements
        sync_decision = await self._analyze_sync_requirements(start_date, end_date)

        await self._update_progress(
            "strategy", 0, 1, f"Using {sync_decision['strategy']} sync"
        )

        if sync_decision["strategy"] == "full":
            return await self.full_strategy.sync_period(start_date, end_date)
        if sync_decision["strategy"] == "incremental":
            return await self.incremental_strategy.sync_since(start_date, end_date)
        # Hybrid approach - not implemented yet
        await self._update_progress("strategy", 0, 1, "Falling back to full sync")
        return await self.full_strategy.sync_period(start_date, end_date)

    async def _analyze_sync_requirements(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Analyze what sync strategy to use.

        Args:
            start_date: Start of sync period
            end_date: End of sync period

        Returns:
            Dictionary with strategy decision and reasoning
        """
        # Check if we have any data for this period
        existing_conversations = self.db.search_conversations(
            start_date=start_date, end_date=end_date, limit=1
        )

        # Check last sync time for this period
        sync_state = self.db.check_sync_state(
            start_date, end_date, freshness_threshold_minutes=30
        )

        # Decision logic
        if not existing_conversations:
            # No existing data - use full sync
            return {"strategy": "full", "reason": "No existing data for period"}
        if sync_state["sync_state"] == "stale":
            # Data is very stale - use full sync for accuracy
            return {
                "strategy": "full",
                "reason": "Data is stale, need complete refresh",
            }
        # Have some data - incremental might be sufficient
        time_span = end_date - start_date
        if time_span <= timedelta(hours=24):
            # Short time span - incremental is efficient
            return {
                "strategy": "incremental",
                "reason": "Short time span, incremental is efficient",
            }
        # Longer time span - full sync for completeness
        return {
            "strategy": "full",
            "reason": "Long time span, full sync for completeness",
        }

    async def _update_progress(
        self, phase: str, current: int, total: int, message: str
    ):
        """Update progress with standardized format."""
        if self.progress_callback:
            SyncProgress(phase, current, total, message)
            await self.progress_callback(f"{phase.capitalize()}: {message}")
