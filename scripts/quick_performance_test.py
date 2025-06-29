#!/usr/bin/env python3
"""
Quick performance test using existing integration test data
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def get_test_workspace() -> Path:
    """Get the test workspace directory with organized subdirectories."""
    # Check environment variable first
    if workspace_env := os.environ.get("FASTINTERCOM_TEST_WORKSPACE"):
        workspace = Path(workspace_env)
    else:
        # Find project root (look for pyproject.toml)
        current_dir = Path.cwd()
        project_root = current_dir

        # Search up the directory tree for pyproject.toml
        while current_dir != current_dir.parent:
            if (current_dir / "pyproject.toml").exists():
                project_root = current_dir
                break
            current_dir = current_dir.parent

        workspace = project_root / ".test-workspace"

    # Create organized subdirectories
    workspace.mkdir(exist_ok=True)
    (workspace / "data").mkdir(exist_ok=True)
    (workspace / "logs").mkdir(exist_ok=True)
    (workspace / "results").mkdir(exist_ok=True)

    return workspace


def run_quick_sync_test(days=7, max_conversations=1000):
    """Run a quick sync test and capture performance metrics"""

    print(f"🚀 Running quick performance test ({days} days, max {max_conversations} conversations)")

    # Setup test environment using standardized workspace
    workspace = get_test_workspace()
    test_dir = workspace / "data"

    db_path = test_dir / "data.db"
    if db_path.exists():
        db_path.unlink()

    os.environ["FASTINTERCOM_CONFIG_DIR"] = str(test_dir)
    os.environ["FASTINTERCOM_TEST_WORKSPACE"] = str(workspace)

    # Run timed sync
    import subprocess

    start_time = time.time()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fast_intercom_mcp",
            "sync",
            "--force",
            "--days",
            str(days),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )

    sync_duration = time.time() - start_time

    if result.returncode != 0:
        print(f"❌ Sync failed: {result.stderr}")
        return None

    # Get database stats
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM conversations")
        conversations = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM messages")
        messages = cursor.fetchone()[0]

        conn.close()

        db_size_mb = db_path.stat().st_size / 1024 / 1024

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
        }

        print("✅ Quick sync completed:")
        print(f"  • Duration: {sync_duration:.1f}s")
        print(f"  • Conversations: {conversations:,}")
        print(f"  • Messages: {messages:,}")
        print(f"  • Database: {db_size_mb:.1f}MB")
        print(f"  • Sync Rate: {sync_rate:.1f} conv/sec")

        return metrics

    except Exception as e:
        print(f"❌ Error getting database stats: {e}")
        return None


def test_server_performance():
    """Test server startup and response times"""
    print("🖥️ Testing server performance...")

    tests = {}

    # Test 1: Status command
    start_time = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "fast_intercom_mcp", "status"],
        capture_output=True,
        text=True,
    )

    tests["status_command"] = {
        "duration": time.time() - start_time,
        "success": result.returncode == 0,
    }

    # Test 2: Help commands
    for cmd in ["serve", "mcp"]:
        start_time = time.time()
        result = subprocess.run(
            [sys.executable, "-m", "fast_intercom_mcp", cmd, "--help"],
            capture_output=True,
            text=True,
        )

        tests[f"{cmd}_help"] = {
            "duration": time.time() - start_time,
            "success": result.returncode == 0,
        }

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

    # Save report to workspace
    workspace = get_test_workspace()
    report_path = workspace / "results" / "quick_performance_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Print summary
    print("\n📊 QUICK PERFORMANCE TEST RESULTS")
    print("=" * 60)
    print(f"🏆 Performance Rating: {rating}")
    print(f"🚀 Production Ready: {'YES' if report['assessment']['production_ready'] else 'NO'}")
    print()
    print("📈 Key Metrics:")
    print(f"  • Sync Rate: {conv_per_sec:.1f} conversations/second")
    print(
        f"  • Storage Efficiency: "
        f"{sync_metrics['storage_efficiency_conversations_per_mb']:.1f} conv/MB"
    )
    print(f"  • Avg Conversation Size: {sync_metrics['avg_conversation_size_kb']:.1f}KB")
    print()
    print("⚡ Server Response Times:")
    for test_name, result in server_tests.items():
        status = "✅" if result["success"] else "❌"
        print(f"  • {test_name}: {status} {result['duration']:.3f}s")

    workspace = get_test_workspace()
    report_path = workspace / "results" / "quick_performance_report.json"
    print(f"\n📄 Report saved to: {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
