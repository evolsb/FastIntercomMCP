#!/usr/bin/env python3
"""
Full workspace sync with comprehensive performance monitoring
This will sync ALL available conversations and capture detailed metrics
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil


class ComprehensiveMonitor:
    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.monitoring = False
        self.metrics = {
            "sync_duration": 0,
            "conversations_synced": 0,
            "messages_synced": 0,
            "api_calls_made": 0,
            "peak_memory_mb": 0,
            "average_memory_mb": 0,
            "peak_cpu_percent": 0,
            "database_size_mb": 0,
            "sync_rate_conversations_per_second": 0,
            "sync_rate_messages_per_second": 0,
            "storage_efficiency_conversations_per_mb": 0,
            "memory_efficiency_conversations_per_mb_ram": 0,
            "checkpoint_times": [],
            "performance_samples": [],
        }
        self.monitor_thread = None
        self.samples = []

    def start_monitoring(self):
        self.start_time = time.time()
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        print(
            f"🔍 Performance monitoring started at {datetime.now().strftime('%H:%M:%S')}"
        )

    def stop_monitoring(self):
        self.end_time = time.time()
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)

        self.metrics["sync_duration"] = self.end_time - self.start_time

        if self.samples:
            memory_values = [s["memory_mb"] for s in self.samples]
            cpu_values = [s["cpu_percent"] for s in self.samples]

            self.metrics["peak_memory_mb"] = max(memory_values)
            self.metrics["average_memory_mb"] = sum(memory_values) / len(memory_values)
            self.metrics["peak_cpu_percent"] = max(cpu_values)

        print(
            f"🔍 Performance monitoring stopped at {datetime.now().strftime('%H:%M:%S')}"
        )

    def _monitor_loop(self):
        process = psutil.Process()
        while self.monitoring:
            try:
                sample = {
                    "timestamp": time.time(),
                    "memory_mb": process.memory_info().rss / 1024 / 1024,
                    "cpu_percent": process.cpu_percent(),
                    "elapsed_time": time.time() - self.start_time,
                }
                self.samples.append(sample)

                # Print periodic updates
                if len(self.samples) % 20 == 0:  # Every 10 seconds
                    elapsed = sample["elapsed_time"]
                    print(
                        f"⏱️ {elapsed:.0f}s - Memory: {sample['memory_mb']:.1f}MB, CPU: {sample['cpu_percent']:.1f}%"
                    )

                time.sleep(0.5)
            except Exception:
                break

    def add_checkpoint(self, name, conversations_so_far=0):
        checkpoint = {
            "name": name,
            "timestamp": time.time(),
            "elapsed_time": time.time() - self.start_time if self.start_time else 0,
            "conversations_so_far": conversations_so_far,
        }
        self.metrics["checkpoint_times"].append(checkpoint)
        print(
            f"📍 Checkpoint: {name} at {checkpoint['elapsed_time']:.1f}s ({conversations_so_far} conversations)"
        )


def check_database_stats(db_path):
    """Get comprehensive database statistics"""
    if not Path(db_path).exists():
        return None

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get table counts
        stats = {}
        tables = [
            "conversations",
            "messages",
            "admins",
            "companies",
            "conversation_parts",
            "customers",
            "tags",
            "teams",
            "sync_periods",
        ]

        for table in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                stats[f"{table}_count"] = cursor.fetchone()[0]
            except sqlite3.Error:
                stats[f"{table}_count"] = 0

        # Get database size
        stats["size_mb"] = Path(db_path).stat().st_size / 1024 / 1024

        # Get some sample data insights
        cursor.execute("""
            SELECT
                MIN(created_at) as earliest_conversation,
                MAX(created_at) as latest_conversation,
                AVG(LENGTH(conversation_summary)) as avg_summary_length
            FROM conversations
            WHERE created_at IS NOT NULL
        """)
        row = cursor.fetchone()
        if row and row[0]:
            stats["date_range"] = {
                "earliest": row[0],
                "latest": row[1],
                "avg_summary_length": row[2] or 0,
            }

        conn.close()
        return stats

    except Exception as e:
        print(f"⚠️ Database stats error: {e}")
        return None


def run_full_workspace_sync():
    """Run comprehensive full workspace sync with monitoring"""
    print("🚀 Starting FULL WORKSPACE SYNC")
    print("=" * 60)

    # Setup clean environment
    test_dir = Path.home() / ".fast-intercom-mcp-full-test"
    test_dir.mkdir(exist_ok=True)

    # Remove existing database for clean test
    db_path = test_dir / "data.db"
    if db_path.exists():
        db_path.unlink()
        print("🗑️ Removed existing test database")

    # Set environment
    os.environ["FASTINTERCOM_CONFIG_DIR"] = str(test_dir)

    # Initialize monitoring
    monitor = ComprehensiveMonitor()

    print(f"📂 Test database: {db_path}")
    print(f"🔑 API token configured: {bool(os.environ.get('INTERCOM_ACCESS_TOKEN'))}")

    # Start monitoring
    monitor.start_monitoring()
    monitor.add_checkpoint("Sync started")

    try:
        # Run the sync with maximum scope
        print("🔄 Starting sync with maximum scope...")
        import subprocess

        # Use the CLI to run a comprehensive sync
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "fast_intercom_mcp",
                "sync",
                "--force",
                "--days",
                "365",  # Full year to get everything
            ],
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour timeout
        )

        monitor.add_checkpoint("Sync command completed")

        if result.returncode != 0:
            print(f"❌ Sync failed: {result.stderr}")
            monitor.stop_monitoring()
            return None

        print("✅ Sync command completed successfully")
        print(f"📄 Output: {result.stdout}")

        # Get final database stats
        final_stats = check_database_stats(db_path)
        monitor.add_checkpoint(
            "Database analysis completed",
            final_stats["conversations_count"] if final_stats else 0,
        )

        # Stop monitoring
        monitor.stop_monitoring()

        # Calculate final metrics
        if final_stats:
            duration = monitor.metrics["sync_duration"]
            conversations = final_stats["conversations_count"]
            messages = final_stats["messages_count"]
            db_size = final_stats["size_mb"]

            monitor.metrics.update(
                {
                    "conversations_synced": conversations,
                    "messages_synced": messages,
                    "database_size_mb": db_size,
                    "sync_rate_conversations_per_second": conversations
                    / max(duration, 1),
                    "sync_rate_messages_per_second": messages / max(duration, 1),
                    "storage_efficiency_conversations_per_mb": conversations
                    / max(db_size, 0.1),
                    "memory_efficiency_conversations_per_mb_ram": conversations
                    / max(monitor.metrics["peak_memory_mb"], 1),
                }
            )

        return monitor, final_stats, result.stdout

    except subprocess.TimeoutExpired:
        print("❌ Sync timed out after 1 hour")
        monitor.stop_monitoring()
        return None
    except Exception as e:
        print(f"❌ Sync error: {e}")
        monitor.stop_monitoring()
        return None


def test_server_with_full_data(_db_path):
    """Test server performance with full dataset"""
    print("\n🖥️ Testing server with full dataset...")
    # _db_path parameter is used by the server internally

    # Test various server operations
    server_tests = {}

    # Test 1: Status command
    start_time = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "fast_intercom_mcp", "status"],
        capture_output=True,
        text=True,
    )

    server_tests["status_command"] = {
        "duration": time.time() - start_time,
        "success": result.returncode == 0,
        "output": result.stdout,
    }

    # Test 2: Server help (startup test)
    start_time = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "fast_intercom_mcp", "serve", "--help"],
        capture_output=True,
        text=True,
    )

    server_tests["serve_help"] = {
        "duration": time.time() - start_time,
        "success": result.returncode == 0,
    }

    # Test 3: MCP help (startup test)
    start_time = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "fast_intercom_mcp", "mcp", "--help"],
        capture_output=True,
        text=True,
    )

    server_tests["mcp_help"] = {
        "duration": time.time() - start_time,
        "success": result.returncode == 0,
    }

    return server_tests


def generate_full_performance_report(monitor, db_stats, server_tests, sync_output):
    """Generate comprehensive performance report"""

    report = {
        "test_info": {
            "test_type": "FULL_WORKSPACE_SYNC",
            "timestamp": datetime.now().isoformat(),
            "test_duration_seconds": monitor.metrics["sync_duration"],
            "python_version": sys.version,
            "platform": sys.platform,
            "environment": os.environ.get("FASTINTERCOM_CONFIG_DIR", "default"),
        },
        "sync_performance": {
            "total_duration_seconds": monitor.metrics["sync_duration"],
            "conversations_synced": monitor.metrics["conversations_synced"],
            "messages_synced": monitor.metrics["messages_synced"],
            "sync_rate_conversations_per_second": monitor.metrics[
                "sync_rate_conversations_per_second"
            ],
            "sync_rate_messages_per_second": monitor.metrics[
                "sync_rate_messages_per_second"
            ],
            "peak_memory_mb": monitor.metrics["peak_memory_mb"],
            "average_memory_mb": monitor.metrics["average_memory_mb"],
            "peak_cpu_percent": monitor.metrics["peak_cpu_percent"],
        },
        "database_metrics": db_stats,
        "efficiency_metrics": {
            "storage_efficiency_conversations_per_mb": monitor.metrics[
                "storage_efficiency_conversations_per_mb"
            ],
            "memory_efficiency_conversations_per_mb_ram": monitor.metrics[
                "memory_efficiency_conversations_per_mb_ram"
            ],
            "average_conversation_size_kb": (monitor.metrics["database_size_mb"] * 1024)
            / max(monitor.metrics["conversations_synced"], 1),
            "messages_per_conversation": monitor.metrics["messages_synced"]
            / max(monitor.metrics["conversations_synced"], 1),
        },
        "server_performance": server_tests,
        "performance_timeline": monitor.metrics["checkpoint_times"],
        "detailed_samples": monitor.samples[-100:]
        if len(monitor.samples) > 100
        else monitor.samples,  # Last 100 samples
        "sync_output": sync_output,
    }

    # Performance rating
    conversations_per_sec = monitor.metrics["sync_rate_conversations_per_second"]
    memory_mb = monitor.metrics["peak_memory_mb"]

    score = 0
    if conversations_per_sec >= 15:
        score += 3
    elif conversations_per_sec >= 10:
        score += 2
    elif conversations_per_sec >= 5:
        score += 1

    if memory_mb <= 150:
        score += 3
    elif memory_mb <= 250:
        score += 2
    elif memory_mb <= 500:
        score += 1

    if monitor.metrics["sync_duration"] <= 1800:
        score += 2  # Under 30 minutes
    elif monitor.metrics["sync_duration"] <= 3600:
        score += 1  # Under 1 hour

    if monitor.metrics["conversations_synced"] > 10000:
        score += 2  # Large dataset

    rating = (
        "EXCELLENT"
        if score >= 8
        else "GOOD"
        if score >= 6
        else "FAIR"
        if score >= 4
        else "NEEDS_IMPROVEMENT"
    )

    report["assessment"] = {
        "overall_rating": rating,
        "performance_score": f"{score}/10",
        "production_ready": score >= 6,
        "dataset_size": "ENTERPRISE"
        if monitor.metrics["conversations_synced"] > 10000
        else "MEDIUM"
        if monitor.metrics["conversations_synced"] > 1000
        else "SMALL",
    }

    return report


def main():
    print("🚀 FastIntercom MCP - FULL WORKSPACE SYNC TEST")
    print("=" * 60)
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Check environment
    if not os.environ.get("INTERCOM_ACCESS_TOKEN"):
        print("❌ INTERCOM_ACCESS_TOKEN not set")
        return 1

    # Run full sync
    result = run_full_workspace_sync()
    if not result:
        print("❌ Full sync failed")
        return 1

    monitor, db_stats, sync_output = result

    # Test server with full data
    server_tests = test_server_with_full_data(
        Path.home() / ".fast-intercom-mcp-full-test" / "data.db"
    )

    # Generate comprehensive report
    report = generate_full_performance_report(
        monitor, db_stats, server_tests, sync_output
    )

    # Save detailed report
    report_path = Path("full_workspace_performance_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("📊 FULL WORKSPACE SYNC RESULTS")
    print("=" * 60)

    sync_perf = report["sync_performance"]
    assessment = report["assessment"]
    efficiency = report["efficiency_metrics"]

    print(f"🏆 Overall Rating: {assessment['overall_rating']}")
    print(f"📈 Performance Score: {assessment['performance_score']}")
    print(f"🚀 Production Ready: {'YES' if assessment['production_ready'] else 'NO'}")
    print(f"📊 Dataset Size: {assessment['dataset_size']}")
    print()
    print("🔄 Sync Performance:")
    print(
        f"  • Duration: {sync_perf['total_duration_seconds']:.1f} seconds ({sync_perf['total_duration_seconds'] / 60:.1f} minutes)"
    )
    print(f"  • Conversations: {sync_perf['conversations_synced']:,}")
    print(f"  • Messages: {sync_perf['messages_synced']:,}")
    print(
        f"  • Sync Rate: {sync_perf['sync_rate_conversations_per_second']:.1f} conversations/sec"
    )
    print(
        f"  • Message Rate: {sync_perf['sync_rate_messages_per_second']:.1f} messages/sec"
    )
    print(f"  • Peak Memory: {sync_perf['peak_memory_mb']:.1f}MB")
    print(f"  • Average Memory: {sync_perf['average_memory_mb']:.1f}MB")
    print(f"  • Peak CPU: {sync_perf['peak_cpu_percent']:.1f}%")
    print()
    print("💾 Database Metrics:")
    if db_stats:
        print(f"  • Database Size: {db_stats['size_mb']:.1f}MB")
        print(f"  • Conversations: {db_stats['conversations_count']:,}")
        print(f"  • Messages: {db_stats['messages_count']:,}")
        if "date_range" in db_stats:
            print(
                f"  • Date Range: {db_stats['date_range']['earliest']} to {db_stats['date_range']['latest']}"
            )
    print()
    print("⚡ Efficiency Metrics:")
    print(
        f"  • Storage: {efficiency['storage_efficiency_conversations_per_mb']:.1f} conversations/MB"
    )
    print(
        f"  • Memory: {efficiency['memory_efficiency_conversations_per_mb_ram']:.1f} conversations/MB RAM"
    )
    print(
        f"  • Avg Conversation Size: {efficiency['average_conversation_size_kb']:.1f}KB"
    )
    print(
        f"  • Messages per Conversation: {efficiency['messages_per_conversation']:.1f}"
    )
    print()
    print(f"📄 Full report saved to: {report_path.absolute()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
