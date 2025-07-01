#!/usr/bin/env python3
"""
Content Verification Script for FastIntercom MCP

This script analyzes synced conversation data to verify:
1. Customer vs admin message filtering is working correctly
2. Message content quality and completeness
3. Conversation metadata accuracy
4. Proper handling of different conversation types

Purpose: Verify the service is working as intended by examining actual content.
"""

import asyncio
import json
import logging
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Add the project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fast_intercom_mcp import Config, DatabaseManager  # noqa: E402
from fast_intercom_mcp.models import Conversation, Message  # noqa: E402

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class ContentVerifier:
    """Verifies the quality and accuracy of synced conversation data."""

    def __init__(self, database_path: str = None):
        """Initialize with database connection."""
        if database_path:
            self.db_path = database_path
            self.db = DatabaseManager(self.db_path)
        else:
            config = Config.load()
            # Let DatabaseManager handle None path (it will use default)
            self.db = DatabaseManager(config.database_path)
            self.db_path = str(self.db.db_path)

    async def verify_content(self, days: int = 1, detailed: bool = False) -> dict[str, Any]:
        """
        Perform comprehensive content verification.

        Args:
            days: Number of recent days to analyze
            detailed: Whether to include detailed examples and breakdowns

        Returns:
            Verification results with quality metrics
        """
        logger.info(f"🔍 Starting content verification for last {days} days")

        # Get recent conversations
        cutoff_date = datetime.now() - timedelta(days=days)
        conversations = self._get_recent_conversations(cutoff_date)

        if not conversations:
            return {"error": "No conversations found in the specified period"}

        logger.info(f"📊 Analyzing {len(conversations)} conversations...")

        results = {
            "summary": {
                "total_conversations": len(conversations),
                "analysis_period_days": days,
                "verification_timestamp": datetime.now().isoformat(),
            },
            "message_analysis": await self._analyze_messages(conversations, detailed),
            "conversation_analysis": await self._analyze_conversations(conversations, detailed),
            "quality_metrics": await self._calculate_quality_metrics(conversations),
            "filtering_effectiveness": await self._verify_filtering(conversations),
        }

        if detailed:
            results["examples"] = await self._get_examples(conversations)

        # Generate overall assessment
        results["assessment"] = self._generate_assessment(results)

        return results

    def _get_recent_conversations(self, cutoff_date: datetime) -> list[Conversation]:
        """Get conversations updated since cutoff date."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        conversations = []
        try:
            cursor = conn.execute(
                """
                SELECT * FROM conversations
                WHERE updated_at >= ?
                ORDER BY updated_at DESC
            """,
                (cutoff_date.timestamp(),),
            )

            for row in cursor:
                # Get messages for this conversation
                msg_cursor = conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at ASC
                """,
                    (row["id"],),
                )

                messages = []
                for msg_row in msg_cursor:
                    message = Message(
                        id=msg_row["id"],
                        conversation_id=msg_row["conversation_id"],
                        author_type=msg_row["author_type"],
                        author_name=msg_row["author_name"],
                        body=msg_row["body"],
                        created_at=datetime.fromtimestamp(msg_row["created_at"]),
                        intercom_url=msg_row["intercom_url"],
                    )
                    messages.append(message)

                conversation = Conversation(
                    id=row["id"],
                    created_at=datetime.fromtimestamp(row["created_at"]),
                    updated_at=datetime.fromtimestamp(row["updated_at"]),
                    state=row["state"],
                    subject=row["subject"],
                    messages=messages,
                    tags=json.loads(row["tags"]) if row["tags"] else [],
                    priority=row["priority"],
                    assignee_name=row["assignee_name"],
                    team_name=row["team_name"],
                    intercom_url=row["intercom_url"],
                )
                conversations.append(conversation)

        finally:
            conn.close()

        return conversations

    async def _analyze_messages(
        self, conversations: list[Conversation], detailed: bool
    ) -> dict[str, Any]:
        """Analyze message content and types."""
        total_messages = 0
        author_types = Counter()
        message_lengths = []
        empty_messages = 0
        customer_messages = []
        admin_messages = []

        for conv in conversations:
            for msg in conv.messages:
                total_messages += 1
                author_types[msg.author_type] += 1

                if msg.body:
                    message_lengths.append(len(msg.body))
                    if msg.author_type == "user":
                        customer_messages.append(msg)
                    else:
                        admin_messages.append(msg)
                else:
                    empty_messages += 1

        # Calculate statistics
        avg_length = sum(message_lengths) / len(message_lengths) if message_lengths else 0

        analysis = {
            "total_messages": total_messages,
            "empty_messages": empty_messages,
            "author_type_distribution": dict(author_types),
            "average_message_length": round(avg_length, 1),
            "customer_messages": len(customer_messages),
            "admin_messages": len(admin_messages),
            "customer_vs_admin_ratio": round(
                len(customer_messages) / max(len(admin_messages), 1), 2
            ),
        }

        if detailed:
            analysis["sample_customer_messages"] = [
                {
                    "author": msg.author_name,
                    "body": msg.body[:100] + "..." if len(msg.body) > 100 else msg.body,
                }
                for msg in customer_messages[:3]
            ]
            analysis["sample_admin_messages"] = [
                {
                    "author": msg.author_name,
                    "body": msg.body[:100] + "..." if len(msg.body) > 100 else msg.body,
                }
                for msg in admin_messages[:3]
            ]

        return analysis

    async def _analyze_conversations(
        self, conversations: list[Conversation], detailed: bool
    ) -> dict[str, Any]:
        """Analyze conversation metadata and structure."""
        states = Counter()
        priorities = Counter()
        has_subject = 0
        has_assignee = 0
        has_team = 0
        has_tags = 0
        conversation_lengths = []

        for conv in conversations:
            states[conv.state] += 1
            priorities[conv.priority or "none"] += 1

            if conv.subject:
                has_subject += 1
            if conv.assignee_name:
                has_assignee += 1
            if conv.team_name:
                has_team += 1
            if conv.tags:
                has_tags += 1

            conversation_lengths.append(len(conv.messages))

        avg_messages_per_conv = (
            sum(conversation_lengths) / len(conversation_lengths) if conversation_lengths else 0
        )

        return {
            "state_distribution": dict(states),
            "priority_distribution": dict(priorities),
            "metadata_completeness": {
                "has_subject": f"{has_subject}/{len(conversations)} ({100*has_subject/len(conversations):.1f}%)",
                "has_assignee": f"{has_assignee}/{len(conversations)} ({100*has_assignee/len(conversations):.1f}%)",
                "has_team": f"{has_team}/{len(conversations)} ({100*has_team/len(conversations):.1f}%)",
                "has_tags": f"{has_tags}/{len(conversations)} ({100*has_tags/len(conversations):.1f}%)",
            },
            "average_messages_per_conversation": round(avg_messages_per_conv, 1),
        }

    async def _calculate_quality_metrics(self, conversations: list[Conversation]) -> dict[str, Any]:
        """Calculate data quality metrics."""
        total_convs = len(conversations)

        # Check for conversations with no messages
        empty_conversations = sum(1 for conv in conversations if not conv.messages)

        # Check for conversations with only admin messages
        admin_only_conversations = sum(
            1
            for conv in conversations
            if conv.messages and all(msg.author_type != "user" for msg in conv.messages)
        )

        # Check for conversations with customer messages
        customer_conversations = sum(
            1 for conv in conversations if any(msg.author_type == "user" for msg in conv.messages)
        )

        # Check for recent customer activity
        now = datetime.now()
        recent_customer_activity = sum(
            1
            for conv in conversations
            if any(
                msg.author_type == "user" and (now - msg.created_at).days <= 1
                for msg in conv.messages
            )
        )

        return {
            "data_completeness": {
                "conversations_with_messages": f"{total_convs - empty_conversations}/{total_convs}",
                "conversations_with_customer_messages": f"{customer_conversations}/{total_convs}",
                "admin_only_conversations": f"{admin_only_conversations}/{total_convs}",
            },
            "customer_engagement": {
                "conversations_with_recent_customer_activity": f"{recent_customer_activity}/{total_convs}",
                "customer_engagement_rate": f"{100*customer_conversations/total_convs:.1f}%",
            },
            "quality_score": self._calculate_quality_score(
                empty_conversations, admin_only_conversations, customer_conversations, total_convs
            ),
        }

    def _calculate_quality_score(
        self, empty: int, admin_only: int, customer: int, total: int
    ) -> float:
        """Calculate overall quality score (0-100)."""
        if total == 0:
            return 0.0

        # Penalize empty conversations and admin-only conversations
        completeness_score = 100 * (total - empty) / total
        engagement_score = 100 * customer / total

        # Weight: 70% completeness, 30% engagement
        overall_score = 0.7 * completeness_score + 0.3 * engagement_score
        return round(overall_score, 1)

    async def _verify_filtering(self, conversations: list[Conversation]) -> dict[str, Any]:
        """Verify that filtering is working correctly."""
        total_messages = sum(len(conv.messages) for conv in conversations)
        customer_messages = sum(
            sum(1 for msg in conv.messages if msg.author_type == "user") for conv in conversations
        )
        total_messages - customer_messages

        # Check if we have a reasonable balance
        customer_ratio = customer_messages / total_messages if total_messages > 0 else 0

        # Expected: 30-70% customer messages (varies by workspace)
        filtering_quality = "good"
        if customer_ratio < 0.1:
            filtering_quality = "possibly_over_filtering"
        elif customer_ratio > 0.9:
            filtering_quality = "possibly_under_filtering"

        return {
            "customer_message_ratio": round(customer_ratio, 3),
            "admin_message_ratio": round(1 - customer_ratio, 3),
            "filtering_assessment": filtering_quality,
            "recommendation": self._get_filtering_recommendation(customer_ratio),
        }

    def _get_filtering_recommendation(self, customer_ratio: float) -> str:
        """Get recommendation based on customer message ratio."""
        if customer_ratio < 0.1:
            return "Very low customer ratio - check if customer messages are being filtered out incorrectly"
        if customer_ratio < 0.2:
            return "Low customer ratio - verify filtering logic is working as intended"
        if customer_ratio > 0.8:
            return "High customer ratio - check if admin messages are being filtered correctly"
        if customer_ratio > 0.9:
            return "Very high customer ratio - verify admin message filtering is working"
        return "Customer/admin ratio appears normal"

    async def _get_examples(self, conversations: list[Conversation]) -> dict[str, Any]:
        """Get examples for detailed analysis."""
        # Find interesting examples
        customer_conv = next(
            (
                conv
                for conv in conversations
                if any(msg.author_type == "user" for msg in conv.messages)
            ),
            None,
        )
        admin_conv = next(
            (
                conv
                for conv in conversations
                if all(msg.author_type != "user" for msg in conv.messages)
            ),
            None,
        )

        examples = {}

        if customer_conv:
            examples["customer_conversation_sample"] = {
                "id": customer_conv.id,
                "subject": customer_conv.subject,
                "message_count": len(customer_conv.messages),
                "customer_messages": [
                    {"author": msg.author_name, "body": msg.body[:100] + "..."}
                    for msg in customer_conv.messages[:3]
                    if msg.author_type == "user"
                ],
            }

        if admin_conv:
            examples["admin_only_conversation_sample"] = {
                "id": admin_conv.id,
                "subject": admin_conv.subject,
                "message_count": len(admin_conv.messages),
                "admin_messages": [
                    {"author": msg.author_name, "body": msg.body[:100] + "..."}
                    for msg in admin_conv.messages[:3]
                ],
            }

        return examples

    def _generate_assessment(self, results: dict[str, Any]) -> dict[str, Any]:
        """Generate overall assessment of content quality."""
        quality_score = results["quality_metrics"]["quality_score"]
        customer_ratio = results["filtering_effectiveness"]["customer_message_ratio"]

        # Overall health assessment
        if quality_score >= 90 and 0.2 <= customer_ratio <= 0.8:
            health = "excellent"
        elif quality_score >= 75 and 0.1 <= customer_ratio <= 0.9:
            health = "good"
        elif quality_score >= 60:
            health = "fair"
        else:
            health = "poor"

        issues = []
        if quality_score < 75:
            issues.append("Low data quality score")
        if customer_ratio < 0.1:
            issues.append("Very low customer message ratio")
        if customer_ratio > 0.9:
            issues.append("Very high customer message ratio")

        return {
            "overall_health": health,
            "quality_score": quality_score,
            "customer_ratio": customer_ratio,
            "issues_found": issues,
            "recommendation": self._get_overall_recommendation(health, issues),
        }

    def _get_overall_recommendation(self, health: str, issues: list[str]) -> str:
        """Get overall recommendation based on assessment."""
        if health == "excellent":
            return "✅ Content verification passed - service is working as intended"
        if health == "good":
            return "✅ Content verification mostly passed - minor issues may need attention"
        if health == "fair":
            return "⚠️ Content verification found some issues - review filtering and data quality"
        return "❌ Content verification failed - significant issues need immediate attention"


async def main():
    """Main function to run content verification."""
    import argparse

    parser = argparse.ArgumentParser(description="Verify FastIntercom MCP content quality")
    parser.add_argument(
        "--days", type=int, default=1, help="Number of days to analyze (default: 1)"
    )
    parser.add_argument(
        "--detailed", action="store_true", help="Include detailed examples and breakdowns"
    )
    parser.add_argument("--database", type=str, help="Database path (default: from config)")
    parser.add_argument("--output", type=str, help="Save results to JSON file")

    args = parser.parse_args()

    try:
        verifier = ContentVerifier(args.database)
        results = await verifier.verify_content(args.days, args.detailed)

        # Print summary
        print("\n" + "=" * 60)
        print("📊 CONTENT VERIFICATION RESULTS")
        print("=" * 60)

        summary = results["summary"]
        print(f"📈 Analyzed: {summary['total_conversations']} conversations ({args.days} days)")

        assessment = results["assessment"]
        print(f"🎯 Overall Health: {assessment['overall_health'].upper()}")
        print(f"📊 Quality Score: {assessment['quality_score']}/100")
        print(f"👥 Customer Ratio: {assessment['customer_ratio']:.1%}")

        if assessment["issues_found"]:
            print(f"⚠️  Issues: {', '.join(assessment['issues_found'])}")

        print(f"\n{assessment['recommendation']}")

        # Print key metrics
        msg_analysis = results["message_analysis"]
        print(f"\n📨 Messages: {msg_analysis['total_messages']} total")
        print(f"   👥 Customer: {msg_analysis['customer_messages']}")
        print(f"   🔧 Admin: {msg_analysis['admin_messages']}")
        print(f"   📏 Avg Length: {msg_analysis['average_message_length']} chars")

        filtering = results["filtering_effectiveness"]
        print(f"\n🔍 Filtering: {filtering['filtering_assessment']}")
        print(f"   {filtering['recommendation']}")

        # Save to file if requested
        if args.output:
            with open(args.output, "w") as f:
                json.dump(results, f, indent=2, default=str)
            print(f"\n💾 Results saved to: {args.output}")

        # Exit code based on health
        if assessment["overall_health"] in ["excellent", "good"]:
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        logger.error(f"Content verification failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
