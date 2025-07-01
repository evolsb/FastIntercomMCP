"""
Performance Benchmarking Suite for FastIntercom MCP Server

This module contains comprehensive performance tests to measure:
- Sync rate (target: 3-5 conversations/second)
- API response times (target: <100ms for cached queries)
- Memory usage during sync operations
- Progress callback performance

Tests can run standalone or as part of CI pipeline with performance regression detection.
"""

import asyncio
import contextlib
import os
import resource
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.intercom_client import IntercomClient
from fast_intercom_mcp.mcp_server import FastIntercomMCPServer
from fast_intercom_mcp.models import Conversation, Message
from fast_intercom_mcp.sync_service import SyncManager, SyncService

# Performance targets
SYNC_RATE_TARGET_MIN = 3.0  # conversations per second
SYNC_RATE_TARGET_MAX = 5.0  # conversations per second
API_RESPONSE_TIME_TARGET = 0.1  # 100ms
MEMORY_USAGE_LIMIT_MB = 500  # Maximum memory usage during sync


class PerformanceMetrics:
    """Helper class to track performance metrics."""

    def __init__(self):
        self.start_time: float | None = None
        self.end_time: float | None = None
        self.start_memory: int | None = None
        self.peak_memory: int | None = None
        self.operations_count: int = 0
        self.response_times: list[float] = []

    def start(self):
        """Start tracking metrics."""
        self.start_time = time.time()
        self.start_memory = self._get_memory_usage()

    def stop(self):
        """Stop tracking metrics."""
        self.end_time = time.time()
        self.peak_memory = self._get_memory_usage()

    def record_operation(self):
        """Record a completed operation."""
        self.operations_count += 1

    def record_response_time(self, response_time: float):
        """Record an API response time."""
        self.response_times.append(response_time)

    @property
    def duration(self) -> float:
        """Get the duration in seconds."""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return 0.0

    @property
    def operations_per_second(self) -> float:
        """Calculate operations per second."""
        if self.duration > 0:
            return self.operations_count / self.duration
        return 0.0

    @property
    def memory_usage_mb(self) -> float:
        """Get peak memory usage in MB."""
        if self.peak_memory and self.start_memory:
            return (self.peak_memory - self.start_memory) / (1024 * 1024)
        return 0.0

    @property
    def avg_response_time(self) -> float:
        """Get average response time."""
        if self.response_times:
            return sum(self.response_times) / len(self.response_times)
        return 0.0

    @property
    def p95_response_time(self) -> float:
        """Get 95th percentile response time."""
        if self.response_times:
            sorted_times = sorted(self.response_times)
            index = int(len(sorted_times) * 0.95)
            return sorted_times[min(index, len(sorted_times) - 1)]
        return 0.0

    def _get_memory_usage(self) -> int:
        """Get current memory usage in bytes."""
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    def get_report(self) -> dict[str, Any]:
        """Generate a performance report."""
        return {
            "duration_seconds": round(self.duration, 2),
            "operations_count": self.operations_count,
            "operations_per_second": round(self.operations_per_second, 2),
            "memory_usage_mb": round(self.memory_usage_mb, 2),
            "avg_response_time_ms": round(self.avg_response_time * 1000, 2),
            "p95_response_time_ms": round(self.p95_response_time * 1000, 2),
            "response_time_samples": len(self.response_times),
        }


def generate_test_conversations(count: int, days_back: int) -> list[Conversation]:
    """Generate test conversations for benchmarking."""
    conversations = []
    base_time = datetime.now(UTC) - timedelta(days=days_back)

    for i in range(count):
        # Vary conversation age across the time period
        conv_time = base_time + timedelta(days=i * days_back / count, hours=i % 24, minutes=i % 60)

        # Vary message count (1-20 messages per conversation)
        message_count = (i % 20) + 1

        messages = []
        for j in range(message_count):
            msg_time = conv_time + timedelta(minutes=j * 5)
            messages.append(
                Message(
                    id=f"msg_{i}_{j}",
                    author_type="user" if j % 2 == 0 else "admin",
                    body=f"Test message {j} in conversation {i}" * 10,  # Some bulk
                    created_at=msg_time,
                    part_type="comment",
                )
            )

        conversations.append(
            Conversation(
                id=f"conv_{i}",
                created_at=conv_time,
                updated_at=messages[-1].created_at if messages else conv_time,
                customer_email=f"user{i}@example.com",
                tags=[f"tag{i % 5}", "performance-test"],
                messages=messages,
            )
        )

    return conversations


@pytest.fixture
def performance_db():
    """Create a temporary database for performance testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        yield db_path
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


@pytest.fixture
def mock_intercom_client_performance():
    """Create a mock Intercom client for performance testing."""
    client = Mock(spec=IntercomClient)
    client.test_connection = AsyncMock(return_value=True)
    client.get_app_id = AsyncMock(return_value="perf_test_app")
    return client


class TestSyncPerformance:
    """Test suite for sync performance benchmarking."""

    async def test_sync_rate_7_days(self, performance_db, mock_intercom_client_performance):
        """Test sync rate for 7 days of data."""
        # Generate test data: 500 conversations over 7 days
        test_conversations = generate_test_conversations(500, 7)

        # Setup mock to return conversations
        mock_intercom_client_performance.fetch_conversations_for_period = AsyncMock(
            return_value=test_conversations
        )

        # Initialize services
        db_manager = DatabaseManager(db_path=performance_db, pool_size=5)
        sync_manager = SyncManager(db_manager, mock_intercom_client_performance)

        # Track performance
        metrics = PerformanceMetrics()
        metrics.start()

        # Define progress callback that handles both string and detailed formats
        def progress_callback(*_args, **_kwargs):
            metrics.record_operation()

        # Run sync
        stats = await sync_manager.sync_service.sync_initial(
            days_back=7, progress_callback=progress_callback
        )

        metrics.stop()

        # Generate report
        report = metrics.get_report()
        print(f"\n7-Day Sync Performance Report: {report}")

        # Assertions
        assert stats.total_conversations == 500
        assert (
            SYNC_RATE_TARGET_MIN <= metrics.operations_per_second <= SYNC_RATE_TARGET_MAX * 2
        ), f"Sync rate {metrics.operations_per_second} conv/s outside target range"
        assert (
            metrics.memory_usage_mb < MEMORY_USAGE_LIMIT_MB
        ), f"Memory usage {metrics.memory_usage_mb}MB exceeds limit"

    async def test_sync_rate_30_days(self, performance_db, mock_intercom_client_performance):
        """Test sync rate for 30 days of data."""
        # Generate test data: 2000 conversations over 30 days
        test_conversations = generate_test_conversations(2000, 30)

        # Setup mock to return conversations
        mock_intercom_client_performance.fetch_conversations_for_period = AsyncMock(
            return_value=test_conversations
        )

        # Initialize services
        db_manager = DatabaseManager(db_path=performance_db, pool_size=10)
        sync_manager = SyncManager(db_manager, mock_intercom_client_performance)

        # Track performance
        metrics = PerformanceMetrics()
        metrics.start()

        # Define progress callback that handles both string and detailed formats
        def progress_callback(*_args, **_kwargs):
            metrics.record_operation()

        # Run sync
        stats = await sync_manager.sync_service.sync_initial(
            days_back=30, progress_callback=progress_callback
        )

        metrics.stop()

        # Generate report
        report = metrics.get_report()
        print(f"\n30-Day Sync Performance Report: {report}")

        # Assertions
        assert stats.total_conversations == 2000
        assert (
            metrics.operations_per_second >= SYNC_RATE_TARGET_MIN
        ), f"Sync rate {metrics.operations_per_second} conv/s below minimum target"
        assert (
            metrics.memory_usage_mb < MEMORY_USAGE_LIMIT_MB
        ), f"Memory usage {metrics.memory_usage_mb}MB exceeds limit"

    async def test_sync_progress_monitoring(self, performance_db, mock_intercom_client_performance):
        """Test progress callback performance during sync."""
        # Generate test data
        test_conversations = generate_test_conversations(100, 1)

        mock_intercom_client_performance.fetch_conversations_for_period = AsyncMock(
            return_value=test_conversations
        )

        # Initialize services
        db_manager = DatabaseManager(db_path=performance_db)
        sync_manager = SyncManager(db_manager, mock_intercom_client_performance)

        # Track callback performance
        callback_times = []

        def progress_callback(current: int, _total: int, _elapsed_seconds: float):
            start = time.time()
            # Simulate some callback work
            _ = f"Progress: {current}/{_total} - {_elapsed_seconds:.2f}s"
            callback_times.append(time.time() - start)

        # Run sync
        await sync_manager.sync_service.sync_initial(
            days_back=1, progress_callback=progress_callback
        )

        # Analyze callback performance
        avg_callback_time = sum(callback_times) / len(callback_times) if callback_times else 0
        max_callback_time = max(callback_times) if callback_times else 0

        print(
            f"\nProgress Callback Performance: "
            f"avg={avg_callback_time * 1000:.2f}ms, max={max_callback_time * 1000:.2f}ms, "
            f"calls={len(callback_times)}"
        )

        # Assertions
        assert avg_callback_time < 0.001, "Progress callback taking too long on average"
        assert max_callback_time < 0.01, "Progress callback max time too high"


class TestAPIPerformance:
    """Test suite for API response time benchmarking."""

    @pytest.fixture
    async def mcp_server(self, performance_db, mock_intercom_client_performance):
        """Create an MCP server instance for testing."""
        # Pre-populate database with test data
        db_manager = DatabaseManager(db_path=performance_db)
        test_conversations = generate_test_conversations(1000, 30)

        # Store conversations in database
        for conv in test_conversations:
            db_manager.upsert_conversation(conv)

        # Create sync service
        sync_service = SyncService(db_manager, mock_intercom_client_performance)

        # Create MCP server
        server = FastIntercomMCPServer(
            database_manager=db_manager,
            sync_service=sync_service,
            intercom_client=mock_intercom_client_performance,
        )

        yield server

    async def test_search_response_time(self, mcp_server):
        """Test search tool response time."""
        metrics = PerformanceMetrics()

        # Test various search queries
        search_queries = [
            "test message",
            "user@example.com",
            "tag1",
            "conversation",
            "admin response",
        ]

        for query in search_queries:
            start_time = time.time()

            # Call the internal search method directly
            args = {"query": query, "limit": 10}
            results = await mcp_server._search_conversations(args)

            response_time = time.time() - start_time
            metrics.record_response_time(response_time)

            # Verify results structure
            assert isinstance(results, list), "Search should return a list"

        # Generate report
        report = metrics.get_report()
        print(f"\nSearch API Performance Report: {report}")

        # Assertions
        assert (
            metrics.avg_response_time < API_RESPONSE_TIME_TARGET
        ), f"Average response time {metrics.avg_response_time * 1000:.2f}ms exceeds target"
        assert (
            metrics.p95_response_time < API_RESPONSE_TIME_TARGET * 2
        ), f"P95 response time {metrics.p95_response_time * 1000:.2f}ms too high"

    async def test_get_conversation_response_time(self, mcp_server):
        """Test get_conversation tool response time."""
        metrics = PerformanceMetrics()

        # Test fetching individual conversations
        for i in range(50):
            conversation_id = f"conv_{i}"
            start_time = time.time()

            # Call the internal get conversation method
            args = {"conversation_id": conversation_id}
            with contextlib.suppress(Exception):
                # Some conversations might not exist, that's OK for this test
                _ = await mcp_server._get_conversation(args)

            response_time = time.time() - start_time
            metrics.record_response_time(response_time)

        # Generate report
        report = metrics.get_report()
        print(f"\nGet Conversation API Performance Report: {report}")

        # Assertions
        assert (
            metrics.avg_response_time < API_RESPONSE_TIME_TARGET
        ), f"Average response time {metrics.avg_response_time * 1000:.2f}ms exceeds target"

    async def test_concurrent_request_handling(self, mcp_server):
        """Test API performance under concurrent load."""
        metrics = PerformanceMetrics()

        async def make_request(request_type: str, param: str):
            """Make a single API request and record timing."""
            start_time = time.time()

            try:
                if request_type == "search":
                    await mcp_server._search_conversations({"query": param, "limit": 5})
                elif request_type == "get":
                    await mcp_server._get_conversation({"conversation_id": param})
            except Exception:
                pass  # OK for performance testing

            return time.time() - start_time

        # Create mixed workload
        tasks = []
        for i in range(100):
            if i % 2 == 0:
                tasks.append(make_request("search", f"test{i % 10}"))
            else:
                tasks.append(make_request("get", f"conv_{i % 50}"))

        # Run concurrent requests
        metrics.start()
        response_times = await asyncio.gather(*tasks)
        metrics.stop()

        # Record all response times
        for rt in response_times:
            metrics.record_response_time(rt)
            metrics.record_operation()

        # Generate report
        report = metrics.get_report()
        print(f"\nConcurrent Request Performance Report: {report}")

        # Assertions
        assert (
            metrics.avg_response_time < API_RESPONSE_TIME_TARGET * 2
        ), "Average response time under load too high"
        assert metrics.operations_per_second > 10, "Throughput too low under concurrent load"

    async def test_server_status_response_time(self, mcp_server):
        """Test get_server_status tool response time."""
        metrics = PerformanceMetrics()

        # Test server status calls
        for _ in range(20):
            start_time = time.time()

            # Call the internal server status method
            results = await mcp_server._get_server_status({})

            response_time = time.time() - start_time
            metrics.record_response_time(response_time)

            # Verify results structure
            assert isinstance(results, list), "Status should return a list"

        # Generate report
        report = metrics.get_report()
        print(f"\nServer Status API Performance Report: {report}")

        # Assertions
        assert (
            metrics.avg_response_time < API_RESPONSE_TIME_TARGET / 2
        ), f"Server status should be very fast, avg: {metrics.avg_response_time * 1000:.2f}ms"


class TestMemoryProfiling:
    """Test suite for memory usage profiling."""

    async def test_memory_usage_during_sync(self, performance_db, mock_intercom_client_performance):
        """Profile memory usage during large sync operation."""
        # Generate large dataset
        test_conversations = generate_test_conversations(5000, 30)

        mock_intercom_client_performance.fetch_conversations_for_period = AsyncMock(
            return_value=test_conversations
        )

        # Initialize services
        db_manager = DatabaseManager(db_path=performance_db, pool_size=10)
        sync_manager = SyncManager(db_manager, mock_intercom_client_performance)

        # Track memory during sync
        memory_samples = []

        def progress_callback(current: int, _total: int, _elapsed_seconds: float):
            # Sample memory usage periodically
            if current % 100 == 0:
                memory_samples.append(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
                )

        # Run sync
        start_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
        await sync_manager.sync_service.sync_initial(
            days_back=30, progress_callback=progress_callback
        )
        end_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)

        # Analyze memory usage
        memory_increase = end_memory - start_memory
        peak_memory = max(memory_samples) if memory_samples else end_memory
        avg_memory = sum(memory_samples) / len(memory_samples) if memory_samples else 0

        print(
            f"\nMemory Usage Report: "
            f"start={start_memory:.1f}MB, end={end_memory:.1f}MB, "
            f"peak={peak_memory:.1f}MB, avg={avg_memory:.1f}MB, "
            f"increase={memory_increase:.1f}MB"
        )

        # Assertions
        assert memory_increase < MEMORY_USAGE_LIMIT_MB, "Memory increase during sync too high"
        assert peak_memory < MEMORY_USAGE_LIMIT_MB * 1.5, "Peak memory usage too high"

    async def test_memory_cleanup_after_operations(
        self, performance_db, mock_intercom_client_performance
    ):
        """Test that memory is properly released after operations."""
        db_manager = DatabaseManager(db_path=performance_db)

        # Measure baseline memory
        import gc

        gc.collect()
        baseline_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)

        # Perform multiple operations
        for _ in range(5):
            # Generate and process data
            test_conversations = generate_test_conversations(1000, 7)
            for conv in test_conversations:
                db_manager.upsert_conversation(conv)

            # Force garbage collection
            gc.collect()

        # Final memory measurement
        final_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
        memory_leak = final_memory - baseline_memory

        print(
            f"\nMemory Cleanup Report: "
            f"baseline={baseline_memory:.1f}MB, final={final_memory:.1f}MB, "
            f"potential_leak={memory_leak:.1f}MB"
        )

        # Assertions - allow some growth but not excessive
        assert memory_leak < 100, f"Potential memory leak detected: {memory_leak:.1f}MB"


def generate_performance_report(results: dict[str, Any]) -> str:
    """Generate a comprehensive performance report."""
    return """
Performance Benchmark Report
===========================

Sync Performance:
- 7-day sync rate: {sync_7_day_rate} conv/s (target: {sync_target_min}-{sync_target_max})
- 30-day sync rate: {sync_30_day_rate} conv/s (target: {sync_target_min}+)
- Memory usage: {memory_usage}MB (limit: {memory_limit}MB)

API Performance:
- Search avg response: {search_avg}ms (target: <{api_target}ms)
- Search P95 response: {search_p95}ms
- Get conversation avg: {get_avg}ms (target: <{api_target}ms)
- Concurrent throughput: {concurrent_ops}/s

Performance Status: {status}
""".format(
        sync_7_day_rate=results.get("sync_7_day_rate", "N/A"),
        sync_30_day_rate=results.get("sync_30_day_rate", "N/A"),
        sync_target_min=SYNC_RATE_TARGET_MIN,
        sync_target_max=SYNC_RATE_TARGET_MAX,
        memory_usage=results.get("memory_usage", "N/A"),
        memory_limit=MEMORY_USAGE_LIMIT_MB,
        search_avg=results.get("search_avg_ms", "N/A"),
        search_p95=results.get("search_p95_ms", "N/A"),
        get_avg=results.get("get_avg_ms", "N/A"),
        api_target=int(API_RESPONSE_TIME_TARGET * 1000),
        concurrent_ops=results.get("concurrent_ops_per_sec", "N/A"),
        status="PASS" if results.get("all_passed", False) else "FAIL",
    )


if __name__ == "__main__":
    # Allow running as standalone script
    print("FastIntercom MCP Performance Benchmark Suite")
    print("=" * 50)

    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print(
            """
Usage: python test_performance_benchmarks.py [test_name]

Available tests:
- test_sync_rate_7_days
- test_sync_rate_30_days
- test_search_response_time
- test_memory_usage_during_sync
- all (run all tests)

Environment variables:
- PERFORMANCE_VERBOSE=1 for detailed output
"""
        )
        sys.exit(0)

    # Run tests using pytest if available
    try:
        test_args = [__file__, "-v", "-s"]
        if len(sys.argv) > 1 and sys.argv[1] != "all":
            test_args.extend(["-k", sys.argv[1]])
        pytest.main(test_args)
    except NameError:
        print("pytest not available. Install with: pip install pytest pytest-asyncio")
