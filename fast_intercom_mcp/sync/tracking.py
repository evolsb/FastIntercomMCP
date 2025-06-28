"""High-level sync state tracking and coordination."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..database import DatabaseManager
from ..intercom_client import IntercomClient
from .state import ConversationSyncTracker, SyncType

logger = logging.getLogger(__name__)


@dataclass
class SyncPlan:
    """Represents a plan for syncing conversations."""

    full_sync_needed: list[str]
    incremental_sync_needed: list[str]
    in_progress: list[str]
    failed_recently: list[str]
    total_api_calls_estimated: int
    estimated_duration_minutes: int
    priority_conversations: list[str]


@dataclass
class SyncExecutionResult:
    """Results from executing a sync plan."""

    conversations_synced: int
    conversations_failed: int
    api_calls_made: int
    duration_seconds: float
    errors: list[str]
    detailed_results: dict[str, Any]


class SyncStateManager:
    """Manages conversation-level sync state and coordinates sync operations."""

    def __init__(
        self, database_manager: DatabaseManager, intercom_client: IntercomClient
    ):
        self.db = database_manager
        self.intercom = intercom_client
        self.sync_tracker = ConversationSyncTracker(database_manager)

        # Configuration
        self.max_concurrent_syncs = 5
        self.full_sync_staleness_hours = 24
        self.incremental_sync_staleness_minutes = 30
        self.error_retry_delay_hours = 2
        self.api_calls_per_full_sync = 1  # Estimate
        self.api_calls_per_incremental_sync = 1  # Estimate

    async def create_sync_plan(
        self,
        target_conversations: list[str] | None = None,
        force_full_sync: bool = False,
        max_conversations: int = 100,
    ) -> SyncPlan:
        """Create an optimal sync plan based on current state.

        Args:
            target_conversations: Specific conversations to sync (default: auto-detect)
            force_full_sync: Force full sync for all conversations
            max_conversations: Maximum conversations to include in plan

        Returns:
            Sync plan with prioritized conversation lists
        """
        logger.info("Creating sync plan...")

        if target_conversations:
            # Plan for specific conversations
            full_sync_needed = []
            incremental_sync_needed = []

            for conv_id in target_conversations:
                state = self.sync_tracker.get_conversation_sync_state(conv_id)

                if force_full_sync or state.needs_full_sync(
                    self.full_sync_staleness_hours
                ):
                    full_sync_needed.append(conv_id)
                elif state.needs_incremental_sync(
                    self.incremental_sync_staleness_minutes
                ):
                    incremental_sync_needed.append(conv_id)
        else:
            # Auto-detect conversations needing sync
            full_sync_needed = self.sync_tracker.get_conversations_needing_full_sync(
                self.full_sync_staleness_hours, max_conversations // 2
            )

            incremental_sync_needed = (
                self.sync_tracker.get_conversations_needing_incremental_sync(
                    self.incremental_sync_staleness_minutes, max_conversations // 2
                )
            )

        # Get conversations currently in progress
        in_progress = self._get_conversations_in_progress()

        # Get conversations that failed recently
        failed_recently = self._get_conversations_with_recent_failures()

        # Remove in-progress and recently failed from sync lists
        full_sync_needed = [
            conv_id
            for conv_id in full_sync_needed
            if conv_id not in in_progress and conv_id not in failed_recently
        ]
        incremental_sync_needed = [
            conv_id
            for conv_id in incremental_sync_needed
            if conv_id not in in_progress and conv_id not in failed_recently
        ]

        # Prioritize conversations (e.g., recently updated, high message count)
        priority_conversations = await self._prioritize_conversations(
            full_sync_needed + incremental_sync_needed
        )

        # Estimate API calls and duration
        total_api_calls = (
            len(full_sync_needed) * self.api_calls_per_full_sync
            + len(incremental_sync_needed) * self.api_calls_per_incremental_sync
        )

        # Estimate duration (assume 2 seconds per API call + overhead)
        estimated_duration_minutes = max(1, (total_api_calls * 2 + 30) // 60)

        plan = SyncPlan(
            full_sync_needed=full_sync_needed[: max_conversations // 2],
            incremental_sync_needed=incremental_sync_needed[: max_conversations // 2],
            in_progress=in_progress,
            failed_recently=failed_recently,
            total_api_calls_estimated=total_api_calls,
            estimated_duration_minutes=estimated_duration_minutes,
            priority_conversations=priority_conversations[:20],  # Top 20 priority
        )

        logger.info(
            f"Sync plan created: {len(plan.full_sync_needed)} full, "
            f"{len(plan.incremental_sync_needed)} incremental, "
            f"~{plan.estimated_duration_minutes} min"
        )

        return plan

    async def execute_sync_plan(
        self, plan: SyncPlan, progress_callback: callable | None = None
    ) -> SyncExecutionResult:
        """Execute a sync plan with proper state tracking.

        Args:
            plan: Sync plan to execute
            progress_callback: Optional progress callback

        Returns:
            Execution results
        """
        start_time = datetime.now()
        logger.info(
            f"Executing sync plan for {len(plan.full_sync_needed + plan.incremental_sync_needed)} conversations"
        )

        conversations_synced = 0
        conversations_failed = 0
        api_calls_made = 0
        errors = []
        detailed_results = {}

        # Execute full syncs first (they're typically more important)
        for i, conv_id in enumerate(plan.full_sync_needed):
            try:
                if progress_callback:
                    await progress_callback(
                        f"Full sync {i + 1}/{len(plan.full_sync_needed)}: {conv_id}"
                    )

                result = await self._execute_full_sync(conv_id)
                conversations_synced += 1
                api_calls_made += result.get("api_calls", 1)
                detailed_results[conv_id] = result

            except Exception as e:
                logger.error(f"Full sync failed for conversation {conv_id}: {e}")
                conversations_failed += 1
                errors.append(f"Full sync {conv_id}: {str(e)}")

                # Mark as failed in state tracking
                self.sync_tracker.mark_sync_failed(
                    conv_id, SyncType.FULL, start_time, str(e)
                )

        # Execute incremental syncs
        for i, conv_id in enumerate(plan.incremental_sync_needed):
            try:
                if progress_callback:
                    await progress_callback(
                        f"Incremental sync {i + 1}/{len(plan.incremental_sync_needed)}: {conv_id}"
                    )

                result = await self._execute_incremental_sync(conv_id)
                conversations_synced += 1
                api_calls_made += result.get("api_calls", 1)
                detailed_results[conv_id] = result

            except Exception as e:
                logger.error(f"Incremental sync failed for conversation {conv_id}: {e}")
                conversations_failed += 1
                errors.append(f"Incremental sync {conv_id}: {str(e)}")

                # Mark as failed in state tracking
                self.sync_tracker.mark_sync_failed(
                    conv_id, SyncType.INCREMENTAL, start_time, str(e)
                )

        duration_seconds = (datetime.now() - start_time).total_seconds()

        result = SyncExecutionResult(
            conversations_synced=conversations_synced,
            conversations_failed=conversations_failed,
            api_calls_made=api_calls_made,
            duration_seconds=duration_seconds,
            errors=errors,
            detailed_results=detailed_results,
        )

        logger.info(
            f"Sync plan executed: {conversations_synced} synced, "
            f"{conversations_failed} failed in {duration_seconds:.1f}s"
        )

        return result

    async def _execute_full_sync(self, conversation_id: str) -> dict[str, Any]:
        """Execute a full sync for a specific conversation."""
        started_at = self.sync_tracker.mark_sync_started(conversation_id, SyncType.FULL)

        try:
            # Fetch complete conversation
            conversation = await self.intercom.fetch_individual_conversation(
                conversation_id
            )

            if conversation is None:
                raise Exception(f"Conversation {conversation_id} not found")

            # Store in database
            stored_count = self.db.store_conversations([conversation])

            # Mark as completed
            self.sync_tracker.mark_sync_completed(
                conversation_id,
                SyncType.FULL,
                started_at,
                message_count=len(conversation.messages),
                api_calls_made=1,
                metadata={"stored_count": stored_count},
            )

            return {
                "status": "completed",
                "message_count": len(conversation.messages),
                "api_calls": 1,
                "stored_count": stored_count,
            }

        except Exception as e:
            self.sync_tracker.mark_sync_failed(
                conversation_id, SyncType.FULL, started_at, str(e), api_calls_made=1
            )
            raise

    async def _execute_incremental_sync(self, conversation_id: str) -> dict[str, Any]:
        """Execute an incremental sync for a specific conversation."""
        started_at = self.sync_tracker.mark_sync_started(
            conversation_id, SyncType.INCREMENTAL
        )

        try:
            # Get current state
            self.sync_tracker.get_conversation_sync_state(conversation_id)

            # Fetch current conversation
            current_conversation = await self.intercom.fetch_individual_conversation(
                conversation_id
            )

            if current_conversation is None:
                raise Exception(f"Conversation {conversation_id} not found")

            # Get existing conversation from database
            existing_conversations = self.db.search_conversations(limit=1000)
            existing_conversation = None
            for conv in existing_conversations:
                if conv.id == conversation_id:
                    existing_conversation = conv
                    break

            new_messages_count = 0
            if existing_conversation:
                # Compare messages to find new ones
                existing_message_ids = {
                    msg.id for msg in existing_conversation.messages
                }
                new_messages = [
                    msg
                    for msg in current_conversation.messages
                    if msg.id not in existing_message_ids
                ]
                new_messages_count = len(new_messages)
            else:
                # New conversation
                new_messages_count = len(current_conversation.messages)

            # Store updated conversation
            stored_count = self.db.store_conversations([current_conversation])

            # Mark as completed
            self.sync_tracker.mark_sync_completed(
                conversation_id,
                SyncType.INCREMENTAL,
                started_at,
                message_count=len(current_conversation.messages),
                api_calls_made=1,
                metadata={
                    "new_messages_count": new_messages_count,
                    "stored_count": stored_count,
                },
            )

            return {
                "status": "completed",
                "message_count": len(current_conversation.messages),
                "new_messages_count": new_messages_count,
                "api_calls": 1,
                "stored_count": stored_count,
            }

        except Exception as e:
            self.sync_tracker.mark_sync_failed(
                conversation_id,
                SyncType.INCREMENTAL,
                started_at,
                str(e),
                api_calls_made=1,
            )
            raise

    def _get_conversations_in_progress(self) -> list[str]:
        """Get conversations currently being synced."""
        # This would query the sync_state table for in_progress status
        # For now, return empty list
        return []

    def _get_conversations_with_recent_failures(self) -> list[str]:
        """Get conversations that failed recently and shouldn't be retried yet."""
        error_conversations = self.sync_tracker.get_conversations_with_sync_errors(
            hours_back=self.error_retry_delay_hours
        )
        return [conv["conversation_id"] for conv in error_conversations]

    async def _prioritize_conversations(self, conversation_ids: list[str]) -> list[str]:
        """Prioritize conversations based on various factors."""
        if not conversation_ids:
            return []

        # Get conversation metadata for prioritization
        conversations = self.db.search_conversations(limit=10000)
        conv_by_id = {
            conv.id: conv for conv in conversations if conv.id in conversation_ids
        }

        # Priority scoring
        prioritized = []
        for conv_id in conversation_ids:
            conv = conv_by_id.get(conv_id)
            if conv is None:
                continue

            # Score based on:
            # 1. Recent activity (updated_at)
            # 2. Message count
            # 3. Customer email presence
            score = 0

            # Recent activity (higher score for more recent)
            hours_since_update = (
                datetime.now() - conv.updated_at
            ).total_seconds() / 3600
            score += max(
                0, 100 - hours_since_update
            )  # Up to 100 points for very recent

            # Message count (more messages = higher priority)
            score += min(50, len(conv.messages))  # Up to 50 points

            # Has customer email
            if conv.customer_email:
                score += 25

            prioritized.append((conv_id, score))

        # Sort by score (highest first)
        prioritized.sort(key=lambda x: x[1], reverse=True)

        return [conv_id for conv_id, score in prioritized]

    def get_sync_health_report(self) -> dict[str, Any]:
        """Generate a comprehensive sync health report."""
        stats = self.sync_tracker.get_sync_statistics()

        # Calculate health metrics
        total_conversations = stats.get("total_conversations", 0)
        full_synced = stats.get("full_synced", 0)
        failed = stats.get("failed", 0)

        health_score = 0
        if total_conversations > 0:
            sync_coverage = (full_synced / total_conversations) * 100
            error_rate = (failed / total_conversations) * 100
            health_score = max(0, sync_coverage - error_rate)

        # Identify issues
        issues = []
        if stats.get("conversations_with_errors", 0) > 0:
            issues.append(
                f"{stats['conversations_with_errors']} conversations have sync errors"
            )

        if stats.get("in_progress", 0) > 10:
            issues.append(f"{stats['in_progress']} syncs appear stuck in progress")

        if stats.get("avg_completion_percentage", 0) < 50:
            issues.append("Low average sync completion percentage")

        return {
            "health_score": round(health_score, 1),
            "total_conversations": total_conversations,
            "sync_coverage_percentage": round(
                (full_synced / total_conversations) * 100, 1
            )
            if total_conversations > 0
            else 0,
            "error_rate_percentage": round((failed / total_conversations) * 100, 1)
            if total_conversations > 0
            else 0,
            "recent_activity": {
                "full_syncs_24h": stats.get("recent_full_syncs", 0),
                "incremental_syncs_24h": stats.get("recent_incremental_syncs", 0),
                "failures_24h": stats.get("recent_failures", 0),
                "avg_duration_seconds": stats.get("avg_duration_seconds", 0),
            },
            "issues": issues,
            "recommendations": self._generate_health_recommendations(stats),
            "last_updated": datetime.now().isoformat(),
        }

    def _generate_health_recommendations(self, stats: dict[str, Any]) -> list[str]:
        """Generate recommendations based on sync statistics."""
        recommendations = []

        if stats.get("conversations_with_errors", 0) > 5:
            recommendations.append(
                "Review and fix sync errors for affected conversations"
            )

        if stats.get("avg_duration_seconds", 0) > 30:
            recommendations.append(
                "Consider optimizing sync performance - operations are taking longer than expected"
            )

        total_conversations = stats.get("total_conversations", 0)
        full_synced = stats.get("full_synced", 0)

        if total_conversations > 0 and (full_synced / total_conversations) < 0.8:
            recommendations.append(
                "Many conversations haven't been fully synced - consider running a comprehensive sync"
            )

        if stats.get("recent_failures", 0) > stats.get(
            "recent_full_syncs", 0
        ) + stats.get("recent_incremental_syncs", 0):
            recommendations.append(
                "High failure rate detected - check API connectivity and rate limits"
            )

        return recommendations
