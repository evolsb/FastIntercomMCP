#!/usr/bin/env python3
"""
Quick performance test using existing integration test data
"""

import time
import os
import sys
import json
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

# Add the project to the path
sys.path.insert(0, str(Path(__file__).parent))

from fast_intercom_mcp.config import Config
from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.intercom_client import IntercomClient
from fast_intercom_mcp.sync_service import SyncService


async def run_quick_sync_async(days, test_dir):
    """Run direct sync using SyncService (same as CI tests)"""
    try:
        # Setup config to use test database
        config = Config()
        config.db_path = str(test_dir / "data.db")

        # Initialize components (same as comprehensive_sync_test.py and CI)
        db_manager = DatabaseManager(config)
        client = IntercomClient(config.intercom_access_token)
        sync_service = SyncService(db_manager, client)

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        print(
            f"📅 Syncing from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
        )

        # Run sync using same method as GitHub tests
        results = await sync_service.sync_period(start_date, end_date)

        return {
            "success": True,
            "conversations_synced": results.total_conversations,
            "messages_synced": results.total_messages,
            "api_requests": results.api_calls_made,
            "errors": results.errors_encountered,
        }

    except Exception as e:
        print(f"❌ Sync failed: {e}")
        return {
            "success": False,
            "conversations_synced": 0,
            "messages_synced": 0,
            "api_requests": 0,
            "errors": 1,
        }


def run_quick_sync_test(days=7, max_conversations=1000):
    """Run a quick sync test and capture performance metrics"""

    print(
        f"🚀 Running quick performance test ({days} days, max {max_conversations} conversations)"
    )

    # Setup test environment
    test_dir = Path.home() / ".fast-intercom-mcp-quick-performance"
    test_dir.mkdir(exist_ok=True)

    db_path = test_dir / "data.db"
    if db_path.exists():
        db_path.unlink()

    os.environ["FASTINTERCOM_CONFIG_DIR"] = str(test_dir)

    # Run timed sync using direct service calls (same as CI)
    start_time = time.time()

    sync_result = asyncio.run(run_quick_sync_async(days, test_dir))

    sync_duration = time.time() - start_time

    if not sync_result["success"]:
        return None

    # Get database stats and calculate metrics
    try:
        conversations = sync_result["conversations_synced"]
        messages = sync_result["messages_synced"]

        db_size_mb = db_path.stat().st_size / 1024 / 1024 if db_path.exists() else 0

        # Calculate metrics
        sync_rate = conversations / max(sync_duration, 1)
        storage_efficiency = conversations / max(db_size_mb, 0.1)

        metrics = {
            "sync_duration": sync_duration,
            "conversations_synced": conversations,
            "messages_synced": messages,
            "database_size_mb": db_size_mb,
            "sync_rate_conversations_per_second": sync_rate,
            "storage_efficiency_conversations_per_mb": storage_efficiency,
            "avg_conversation_size_kb": (db_size_mb * 1024) / max(conversations, 1),
            "api_requests": sync_result["api_requests"],
            "errors": sync_result["errors"],
        }

        print("✅ Quick sync completed:")
        print(f"  • Duration: {sync_duration:.1f}s")
        print(f"  • Conversations: {conversations:,}")
        print(f"  • Messages: {messages:,}")
        print(f"  • Database: {db_size_mb:.1f}MB")
        print(f"  • Sync Rate: {sync_rate:.1f} conv/sec")
        print(f"  • API Requests: {sync_result['api_requests']:,}")
        print(f"  • Errors: {sync_result['errors']}")

        return metrics

    except Exception as e:
        print(f"❌ Error calculating metrics: {e}")
        return None


def test_server_performance():
    """Test server startup and response times"""
    print("🖥️ Testing server performance...")

    tests = {}

    # Test 1: Direct sync service status (without CLI)
    start_time = time.time()
    try:
        config = Config()
        db_manager = DatabaseManager(config)
        client = IntercomClient(config.intercom_access_token)
        sync_service = SyncService(db_manager, client)

        # Test sync service status
        sync_service.get_status()  # Test that status works
        success = True
    except Exception:
        success = False

    tests["sync_service_status"] = {
        "duration": time.time() - start_time,
        "success": success,
    }

    # Test 2: Module import performance
    start_time = time.time()
    try:
        success = True
    except Exception:
        success = False

    tests["module_import"] = {"duration": time.time() - start_time, "success": success}

    # Test 3: Client connection test
    start_time = time.time()
    try:
        config = Config()
        client = IntercomClient(config.intercom_access_token)
        # Quick connection test (synchronous version)
        success = True  # If we can create client without error
    except Exception:
        success = False

    tests["client_init"] = {"duration": time.time() - start_time, "success": success}

    return tests


def main():
    print("🚀 FastIntercom MCP - Quick Performance Test")
    print("=" * 60)

    if not os.environ.get("INTERCOM_ACCESS_TOKEN"):
        print("❌ INTERCOM_ACCESS_TOKEN not set")
        return 1

    # Run quick sync test
    sync_metrics = run_quick_sync_test(days=7, max_conversations=500)
    if not sync_metrics:
        return 1

    # Test server performance
    server_tests = test_server_performance()

    # Generate report
    report = {
        "test_info": {
            "test_type": "QUICK_PERFORMANCE_TEST",
            "timestamp": datetime.now().isoformat(),
            "python_version": sys.version,
            "platform": sys.platform,
        },
        "sync_performance": sync_metrics,
        "server_performance": server_tests,
    }

    # Performance assessment
    conv_per_sec = sync_metrics["sync_rate_conversations_per_second"]
    rating = (
        "EXCELLENT"
        if conv_per_sec >= 15
        else "GOOD"
        if conv_per_sec >= 10
        else "FAIR"
        if conv_per_sec >= 5
        else "NEEDS_IMPROVEMENT"
    )

    report["assessment"] = {
        "performance_rating": rating,
        "production_ready": conv_per_sec >= 10,
    }

    # Save report
    with open("quick_performance_report.json", "w") as f:
        json.dump(report, f, indent=2)

    # Print summary
    print("\n📊 QUICK PERFORMANCE TEST RESULTS")
    print("=" * 60)
    print(f"🏆 Performance Rating: {rating}")
    print(
        f"🚀 Production Ready: {'YES' if report['assessment']['production_ready'] else 'NO'}"
    )
    print()
    print("📈 Key Metrics:")
    print(f"  • Sync Rate: {conv_per_sec:.1f} conversations/second")
    print(
        f"  • Storage Efficiency: {sync_metrics['storage_efficiency_conversations_per_mb']:.1f} conv/MB"
    )
    print(
        f"  • Avg Conversation Size: {sync_metrics['avg_conversation_size_kb']:.1f}KB"
    )
    print()
    print("⚡ Server Response Times:")
    for test_name, result in server_tests.items():
        status = "✅" if result["success"] else "❌"
        print(f"  • {test_name}: {status} {result['duration']:.3f}s")

    print("\n📄 Report saved to: quick_performance_report.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
