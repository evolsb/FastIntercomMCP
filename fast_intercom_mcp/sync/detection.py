"""Change detection for efficient incremental synchronization."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..database import DatabaseManager
from ..intercom_client import IntercomClient
from ..models import Conversation, Message

logger = logging.getLogger(__name__)


@dataclass
class ConversationChange:
    """Represents a detected change in a conversation."""

    conversation_id: str
    change_type: str  # 'new_messages', 'state_change', 'tags_updated'
    detected_at: datetime
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChangeDetectionResult:
    """Results from conversation change detection."""

    conversations_checked: int
    changes_detected: list[ConversationChange]
    api_calls_made: int
    detection_duration_seconds: float

    def get_conversations_with_changes(self) -> set[str]:
        """Get set of conversation IDs that have changes."""
        return {change.conversation_id for change in self.changes_detected}

    def get_changes_by_type(self, change_type: str) -> list[ConversationChange]:
        """Get changes of a specific type."""
        return [
            change
            for change in self.changes_detected
            if change.change_type == change_type
        ]


class ConversationChangeDetector:
    """Detects changes in conversations for efficient incremental sync."""

    def __init__(self, intercom_client: IntercomClient, database: DatabaseManager):
        self.intercom = intercom_client
        self.db = database

        # Detection thresholds
        self.message_timestamp_tolerance_seconds = (
            5  # Allow small timestamp differences
        )
        self.batch_size = 25  # How many conversations to check in one batch

    async def detect_changes_in_timeframe(
        self,
        start_time: datetime,
        end_time: datetime,
        change_types: list[str] | None = None,
    ) -> ChangeDetectionResult:
        """Detect changes in conversations within a specific timeframe.

        Args:
            start_time: Start of timeframe to check
            end_time: End of timeframe to check
            change_types: Types of changes to detect (default: all)

        Returns:
            Change detection results
        """
        start_detection = datetime.now()

        if change_types is None:
            change_types = ["new_messages", "state_change", "tags_updated"]

        logger.info(f"Detecting changes from {start_time} to {end_time}")

        # Get conversations that were updated in this timeframe from Intercom
        updated_conversations = await self.intercom.fetch_conversations_for_period(
            start_time, end_time
        )

        # Get our local versions of these conversations
        local_conversations = self._get_local_conversations(
            [conv.id for conv in updated_conversations]
        )
        local_by_id = {conv.id: conv for conv in local_conversations}

        changes_detected = []
        api_calls = 1  # The initial search

        # Compare each updated conversation with our local version
        for remote_conv in updated_conversations:
            local_conv = local_by_id.get(remote_conv.id)

            if local_conv is None:
                # New conversation - not really a "change" for incremental sync
                continue

            # Detect specific types of changes
            conv_changes = self._detect_conversation_changes(
                local_conv, remote_conv, change_types
            )
            changes_detected.extend(conv_changes)

        detection_duration = (datetime.now() - start_detection).total_seconds()

        result = ChangeDetectionResult(
            conversations_checked=len(updated_conversations),
            changes_detected=changes_detected,
            api_calls_made=api_calls,
            detection_duration_seconds=detection_duration,
        )

        logger.info(
            f"Change detection completed: {len(changes_detected)} changes found "
            f"in {len(updated_conversations)} conversations"
        )

        return result

    async def detect_changes_in_conversations(
        self, conversation_ids: list[str], change_types: list[str] | None = None
    ) -> ChangeDetectionResult:
        """Detect changes in specific conversations.

        Args:
            conversation_ids: Specific conversations to check
            change_types: Types of changes to detect

        Returns:
            Change detection results
        """
        start_detection = datetime.now()

        if change_types is None:
            change_types = ["new_messages", "state_change", "tags_updated"]

        logger.info(
            f"Detecting changes in {len(conversation_ids)} specific conversations"
        )

        # Get local versions
        local_conversations = self._get_local_conversations(conversation_ids)
        local_by_id = {conv.id: conv for conv in local_conversations}

        # Fetch current versions from Intercom
        remote_conversations = await self.intercom.fetch_individual_conversations(
            conversation_ids
        )

        changes_detected = []
        api_calls = len(conversation_ids)  # One call per conversation

        for remote_conv in remote_conversations:
            local_conv = local_by_id.get(remote_conv.id)

            if local_conv is None:
                # Don't have this conversation locally
                continue

            conv_changes = self._detect_conversation_changes(
                local_conv, remote_conv, change_types
            )
            changes_detected.extend(conv_changes)

        detection_duration = (datetime.now() - start_detection).total_seconds()

        result = ChangeDetectionResult(
            conversations_checked=len(conversation_ids),
            changes_detected=changes_detected,
            api_calls_made=api_calls,
            detection_duration_seconds=detection_duration,
        )

        logger.info(
            f"Specific change detection completed: {len(changes_detected)} changes found"
        )

        return result

    def _get_local_conversations(
        self, conversation_ids: list[str]
    ) -> list[Conversation]:
        """Get local versions of conversations from database."""
        # This is a simplified implementation
        # In practice, we'd want a more efficient query that gets specific conversations by ID
        all_local = self.db.search_conversations(
            limit=10000
        )  # Large limit to get most conversations

        return [conv for conv in all_local if conv.id in conversation_ids]

    def _detect_conversation_changes(
        self,
        local_conv: Conversation,
        remote_conv: Conversation,
        change_types: list[str],
    ) -> list[ConversationChange]:
        """Detect specific changes between local and remote conversation versions."""
        changes = []
        now = datetime.now()

        # Detect new messages
        if "new_messages" in change_types:
            new_messages = self._detect_new_messages(local_conv, remote_conv)
            if new_messages:
                changes.append(
                    ConversationChange(
                        conversation_id=local_conv.id,
                        change_type="new_messages",
                        detected_at=now,
                        details={
                            "new_message_count": len(new_messages),
                            "new_message_ids": [msg.id for msg in new_messages],
                            "latest_message_time": max(
                                msg.created_at for msg in new_messages
                            )
                            if new_messages
                            else None,
                        },
                    )
                )

        # Detect state changes (updated_at timestamp)
        if (
            "state_change" in change_types
            and remote_conv.updated_at > local_conv.updated_at
        ):
            time_diff = (remote_conv.updated_at - local_conv.updated_at).total_seconds()
            if time_diff > self.message_timestamp_tolerance_seconds:
                changes.append(
                    ConversationChange(
                        conversation_id=local_conv.id,
                        change_type="state_change",
                        detected_at=now,
                        details={
                            "old_updated_at": local_conv.updated_at,
                            "new_updated_at": remote_conv.updated_at,
                            "time_difference_seconds": time_diff,
                        },
                    )
                )

        # Detect tag changes
        if "tags_updated" in change_types and set(local_conv.tags) != set(
            remote_conv.tags
        ):
            added_tags = set(remote_conv.tags) - set(local_conv.tags)
            removed_tags = set(local_conv.tags) - set(remote_conv.tags)

            changes.append(
                ConversationChange(
                    conversation_id=local_conv.id,
                    change_type="tags_updated",
                    detected_at=now,
                    details={
                        "old_tags": local_conv.tags,
                        "new_tags": remote_conv.tags,
                        "added_tags": list(added_tags),
                        "removed_tags": list(removed_tags),
                    },
                )
            )

        return changes

    def _detect_new_messages(
        self, local_conv: Conversation, remote_conv: Conversation
    ) -> list[Message]:
        """Detect new messages in a conversation."""
        # Create a set of local message IDs for efficient lookup
        local_message_ids = {msg.id for msg in local_conv.messages}

        # Find messages in remote that aren't in local
        new_messages = []
        for msg in remote_conv.messages:
            if msg.id not in local_message_ids:
                new_messages.append(msg)

        return new_messages

    async def detect_stale_conversations(
        self, staleness_threshold_minutes: int = 30, max_conversations: int = 100
    ) -> list[str]:
        """Detect conversations that haven't been synced recently and might be stale.

        Args:
            staleness_threshold_minutes: Consider conversations stale after this many minutes
            max_conversations: Maximum number of conversations to return

        Returns:
            List of conversation IDs that might be stale
        """
        cutoff_time = datetime.now() - timedelta(minutes=staleness_threshold_minutes)

        # Get conversations that haven't been synced recently
        # This is a simplified query - in practice, we'd want to track last_sync_time per conversation
        stale_conversations = self.db.search_conversations(
            end_date=cutoff_time, limit=max_conversations
        )

        # Filter to conversations that have been active recently (according to Intercom)
        # but haven't been synced locally
        recent_remote = await self.intercom.fetch_conversations_for_period(
            cutoff_time, datetime.now()
        )

        remote_ids = {conv.id for conv in recent_remote}
        stale_ids = []

        for conv in stale_conversations:
            if conv.id in remote_ids:
                # This conversation is active remotely but stale locally
                stale_ids.append(conv.id)

        logger.info(f"Detected {len(stale_ids)} potentially stale conversations")
        return stale_ids

    def analyze_change_patterns(
        self, changes: list[ConversationChange], time_window_hours: int = 24
    ) -> dict[str, Any]:
        """Analyze patterns in detected changes to optimize sync scheduling.

        Args:
            changes: List of detected changes
            time_window_hours: Time window for pattern analysis

        Returns:
            Analysis of change patterns
        """
        now = datetime.now()
        cutoff_time = now - timedelta(hours=time_window_hours)

        # Filter to recent changes
        recent_changes = [
            change for change in changes if change.detected_at >= cutoff_time
        ]

        # Analyze by change type
        change_counts = {}
        for change in recent_changes:
            change_counts[change.change_type] = (
                change_counts.get(change.change_type, 0) + 1
            )

        # Analyze by conversation
        conversation_activity = {}
        for change in recent_changes:
            conv_id = change.conversation_id
            if conv_id not in conversation_activity:
                conversation_activity[conv_id] = []
            conversation_activity[conv_id].append(change)

        # Find most active conversations
        most_active = sorted(
            conversation_activity.items(), key=lambda x: len(x[1]), reverse=True
        )[:10]

        # Calculate change frequency
        hours_elapsed = (
            min(
                time_window_hours,
                (
                    now - min(change.detected_at for change in recent_changes)
                ).total_seconds()
                / 3600,
            )
            if recent_changes
            else time_window_hours
        )
        change_frequency = (
            len(recent_changes) / hours_elapsed if hours_elapsed > 0 else 0
        )

        return {
            "total_changes": len(recent_changes),
            "changes_by_type": change_counts,
            "unique_conversations_affected": len(conversation_activity),
            "most_active_conversations": [
                (conv_id, len(changes)) for conv_id, changes in most_active
            ],
            "average_changes_per_hour": change_frequency,
            "recommended_sync_frequency_minutes": self._calculate_recommended_frequency(
                change_frequency
            ),
            "analysis_time_window_hours": time_window_hours,
        }

    def _calculate_recommended_frequency(self, changes_per_hour: float) -> int:
        """Calculate recommended sync frequency based on change rate.

        Args:
            changes_per_hour: Average changes detected per hour

        Returns:
            Recommended sync frequency in minutes
        """
        if changes_per_hour < 1:
            # Low activity - sync every 2 hours
            return 120
        if changes_per_hour < 5:
            # Medium activity - sync every hour
            return 60
        if changes_per_hour < 15:
            # High activity - sync every 30 minutes
            return 30
        # Very high activity - sync every 15 minutes
        return 15
