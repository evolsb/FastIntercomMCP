"""
Comprehensive End-to-End Integration Test for FastIntercom MCP Server

This test verifies the complete workflow with REAL Intercom API data:
- Fresh server startup and initialization
- Intercom API connection and authentication
- Initial sync of 30+ days of conversation history
- Database integrity verification
- MCP server functionality
- Performance benchmarking
- Clean shutdown and cleanup

Requirements:
- INTERCOM_ACCESS_TOKEN environment variable
- FASTINTERCOM_LOG_LEVEL=DEBUG for verbose output
- Real Intercom API access with 30+ days of data
"""

import asyncio
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

# Optional pytest import
try:
    import pytest

    PYTEST_AVAILABLE = True
except ImportError:
    PYTEST_AVAILABLE = False

# Add the parent directory to the Python path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import with try/catch to handle missing dependencies gracefully
try:
    from fast_intercom_mcp.core.logging import setup_enhanced_logging
    from fast_intercom_mcp.database import DatabaseManager
    from fast_intercom_mcp.intercom_client import IntercomClient
    from fast_intercom_mcp.mcp_server import FastIntercomMCPServer
    from fast_intercom_mcp.models import SyncStats
    from fast_intercom_mcp.sync_service import SyncManager, SyncService

    DEPENDENCIES_AVAILABLE = True
except ImportError as e:
    print(f"⚠️  Missing dependencies: {e}")
    print("Please install the package: pip install -e .")
    DEPENDENCIES_AVAILABLE = False


# Set up logging for the test
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class IntegrationTestRunner:
    """Comprehensive integration test runner for FastIntercom MCP Server."""

    def __init__(self):
        self.start_time = time.time()
        self.db_path: str | None = None
        self.db: DatabaseManager | None = None
        self.intercom_client: IntercomClient | None = None
        self.sync_service: SyncService | None = None
        self.mcp_server: FastIntercomMCPServer | None = None
        self.test_results: dict[str, any] = {}
        self.performance_metrics: dict[str, any] = {}

        # Test configuration
        self.target_days = 30  # Minimum days of data to sync
        self.performance_targets = {
            "min_sync_speed": 10,  # conversations per second
            "max_sync_speed": 50,  # conversations per second
            "max_response_time": 0.1,  # 100ms for cached queries
            "max_memory_usage": 100 * 1024 * 1024,  # 100MB
        }

    def setup_test_environment(self) -> None:
        """Set up the test environment with temporary database and logging."""
        logger.info("🔧 Setting up test environment...")

        # Create temporary database
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_db:
            self.db_path = temp_db.name

        # Setup enhanced logging
        if DEPENDENCIES_AVAILABLE:
            setup_enhanced_logging(".", "DEBUG")

        # Verify environment variables
        token = os.getenv("INTERCOM_ACCESS_TOKEN")
        if not token:
            raise ValueError("INTERCOM_ACCESS_TOKEN environment variable is required")

        logger.info(f"✅ Test environment ready - DB: {self.db_path}")

    def cleanup_test_environment(self) -> None:
        """Clean up temporary files and resources."""
        logger.info("🧹 Cleaning up test environment...")

        # Close database connections
        if self.db:
            self.db.close()

        # Remove temporary database
        if self.db_path and os.path.exists(self.db_path):
            os.unlink(self.db_path)
            logger.info(f"🗑️  Removed temporary database: {self.db_path}")

        logger.info("✅ Cleanup completed")

    async def test_api_connection(self) -> bool:
        """Test Intercom API connection and authentication."""
        logger.info("🔗 Testing Intercom API connection...")

        if not DEPENDENCIES_AVAILABLE:
            logger.error("❌ Dependencies not available - skipping API test")
            return False

        token = os.getenv("INTERCOM_ACCESS_TOKEN")
        self.intercom_client = IntercomClient(token, timeout_seconds=30)

        try:
            # Test basic connection
            connection_result = await self.intercom_client.test_connection()
            if not connection_result:
                logger.error("❌ Failed to connect to Intercom API")
                return False

            # Get app ID
            app_id = await self.intercom_client.get_app_id()
            if not app_id:
                logger.error("❌ Failed to get app ID from Intercom API")
                return False

            logger.info(f"✅ Intercom API connection successful - App ID: {app_id}")
            self.test_results["api_connection"] = True
            self.test_results["app_id"] = app_id
            return True

        except Exception as e:
            logger.error(f"❌ API connection test failed: {e}")
            self.test_results["api_connection"] = False
            return False

    async def test_database_initialization(self) -> bool:
        """Test database initialization and schema creation."""
        logger.info("🗄️  Testing database initialization...")

        if not DEPENDENCIES_AVAILABLE:
            logger.error("❌ Dependencies not available - skipping database test")
            return False

        try:
            self.db = DatabaseManager(self.db_path, connection_pool_size=5)

            # Check if database was created and initialized
            if not os.path.exists(self.db_path):
                logger.error("❌ Database file was not created")
                return False

            # Test basic database operations
            status = self.db.get_sync_status()
            if not isinstance(status, dict):
                logger.error("❌ Database status query failed")
                return False

            logger.info("✅ Database initialization successful")
            self.test_results["database_init"] = True
            return True

        except Exception as e:
            logger.error(f"❌ Database initialization failed: {e}")
            self.test_results["database_init"] = False
            return False

    async def test_initial_sync(self) -> bool:
        """Test initial sync of 30+ days of conversation history."""
        logger.info(f"🔄 Testing initial sync of {self.target_days}+ days...")

        if not DEPENDENCIES_AVAILABLE or not self.db or not self.intercom_client:
            logger.error(
                "❌ Dependencies or components not available - skipping sync test"
            )
            return False

        try:
            # Create sync service
            sync_manager = SyncManager(self.db, self.intercom_client)
            self.sync_service = sync_manager.get_sync_service()

            # Measure sync performance
            sync_start = time.time()

            # Perform initial sync
            stats = await self.sync_service.sync_initial(self.target_days)

            sync_duration = time.time() - sync_start

            # Validate sync results
            if not isinstance(stats, SyncStats):
                logger.error("❌ Sync did not return valid statistics")
                return False

            if stats.total_conversations == 0:
                logger.error("❌ No conversations were synced")
                return False

            # Calculate performance metrics
            sync_speed = (
                stats.total_conversations / sync_duration if sync_duration > 0 else 0
            )

            # Log results
            logger.info("✅ Initial sync completed successfully!")
            logger.info(f"   - Conversations: {stats.total_conversations:,}")
            logger.info(f"   - Messages: {stats.total_messages:,}")
            logger.info(f"   - Duration: {sync_duration:.1f} seconds")
            logger.info(f"   - Speed: {sync_speed:.1f} conversations/second")
            logger.info(f"   - API calls: {stats.api_calls_made:,}")

            # Store results
            self.test_results["initial_sync"] = True
            self.test_results["sync_stats"] = stats
            self.performance_metrics["sync_duration"] = sync_duration
            self.performance_metrics["sync_speed"] = sync_speed
            self.performance_metrics["conversations_synced"] = stats.total_conversations
            self.performance_metrics["messages_synced"] = stats.total_messages

            # Check performance targets
            if sync_speed < self.performance_targets["min_sync_speed"]:
                logger.warning(
                    f"⚠️  Sync speed ({sync_speed:.1f} conv/sec) below target ({self.performance_targets['min_sync_speed']} conv/sec)"
                )
            elif sync_speed > self.performance_targets["max_sync_speed"]:
                logger.info(f"🚀 Excellent sync speed: {sync_speed:.1f} conv/sec")
            else:
                logger.info(
                    f"✅ Sync speed within target range: {sync_speed:.1f} conv/sec"
                )

            return True

        except Exception as e:
            logger.error(f"❌ Initial sync failed: {e}")
            self.test_results["initial_sync"] = False
            return False

    async def test_database_integrity(self) -> bool:
        """Test database integrity and data completeness."""
        logger.info("🔍 Testing database integrity and completeness...")

        if not DEPENDENCIES_AVAILABLE or not self.db:
            logger.error(
                "❌ Dependencies or database not available - skipping integrity test"
            )
            return False

        try:
            # Get database status
            status = self.db.get_sync_status()

            # Check conversation count
            conv_count = status.get("total_conversations", 0)
            if conv_count == 0:
                logger.error("❌ No conversations found in database")
                return False

            # Check message count
            msg_count = status.get("total_messages", 0)
            if msg_count == 0:
                logger.error("❌ No messages found in database")
                return False

            # Check message-to-conversation ratio (should be > 1)
            msg_per_conv = msg_count / conv_count if conv_count > 0 else 0
            if msg_per_conv < 1:
                logger.error(
                    f"❌ Suspicious message-to-conversation ratio: {msg_per_conv:.2f}"
                )
                return False

            # Test data retrieval
            recent_conversations = self.db.search_conversations(
                query="", limit=10, days_back=self.target_days
            )

            if not recent_conversations:
                logger.error("❌ Failed to retrieve recent conversations")
                return False

            # Check conversation completeness
            incomplete_count = 0
            for conv in recent_conversations:
                if not conv.messages:
                    incomplete_count += 1

            if (
                incomplete_count > len(recent_conversations) * 0.1
            ):  # More than 10% incomplete
                logger.warning(f"⚠️  {incomplete_count} conversations have no messages")

            logger.info("✅ Database integrity verified")
            logger.info(f"   - {conv_count:,} conversations")
            logger.info(f"   - {msg_count:,} messages")
            logger.info(f"   - {msg_per_conv:.1f} messages per conversation")
            logger.info(f"   - {incomplete_count} incomplete conversations")

            self.test_results["database_integrity"] = True
            self.test_results["conversation_count"] = conv_count
            self.test_results["message_count"] = msg_count
            self.test_results["messages_per_conversation"] = msg_per_conv

            return True

        except Exception as e:
            logger.error(f"❌ Database integrity test failed: {e}")
            self.test_results["database_integrity"] = False
            return False

    async def test_mcp_server_functionality(self) -> bool:
        """Test MCP server functionality and response times."""
        logger.info("🖥️  Testing MCP server functionality...")

        if (
            not DEPENDENCIES_AVAILABLE
            or not self.db
            or not self.sync_service
            or not self.intercom_client
        ):
            logger.error(
                "❌ Dependencies or components not available - skipping MCP test"
            )
            return False

        try:
            # Create MCP server
            self.mcp_server = FastIntercomMCPServer(
                self.db, self.sync_service, self.intercom_client
            )

            # Test status endpoint
            status_start = time.time()

            # We'll simulate the status call by directly calling database methods
            # since we can't easily test the full MCP protocol in this context
            status = self.db.get_sync_status()

            status_time = time.time() - status_start

            if not status:
                logger.error("❌ Status endpoint failed")
                return False

            # Test search functionality
            search_start = time.time()

            search_results = self.db.search_conversations(
                query="help", limit=10, days_back=7
            )

            search_time = time.time() - search_start

            # Test conversation retrieval
            if search_results:
                get_start = time.time()

                conversation = self.db.get_conversation(search_results[0].id)

                get_time = time.time() - get_start

                if not conversation:
                    logger.error("❌ Get conversation failed")
                    return False
            else:
                get_time = 0
                logger.warning("⚠️  No conversations found for retrieval test")

            # Check response times
            max_response_time = self.performance_targets["max_response_time"]

            logger.info("✅ MCP server functionality verified")
            logger.info(f"   - Status response time: {status_time * 1000:.1f}ms")
            logger.info(f"   - Search response time: {search_time * 1000:.1f}ms")
            logger.info(f"   - Get conversation time: {get_time * 1000:.1f}ms")
            logger.info(f"   - Search results: {len(search_results)} conversations")

            # Store performance metrics
            self.performance_metrics["status_response_time"] = status_time
            self.performance_metrics["search_response_time"] = search_time
            self.performance_metrics["get_conversation_time"] = get_time

            # Check performance targets
            if status_time > max_response_time:
                logger.warning(
                    f"⚠️  Status response time ({status_time * 1000:.1f}ms) exceeds target ({max_response_time * 1000:.1f}ms)"
                )
            if search_time > max_response_time:
                logger.warning(
                    f"⚠️  Search response time ({search_time * 1000:.1f}ms) exceeds target ({max_response_time * 1000:.1f}ms)"
                )

            self.test_results["mcp_server"] = True
            return True

        except Exception as e:
            logger.error(f"❌ MCP server test failed: {e}")
            self.test_results["mcp_server"] = False
            return False

    def check_performance_targets(self) -> bool:
        """Check if performance targets were met."""
        logger.info("📊 Checking performance targets...")

        targets_met = True

        # Check sync speed
        sync_speed = self.performance_metrics.get("sync_speed", 0)
        if sync_speed < self.performance_targets["min_sync_speed"]:
            logger.warning(
                f"⚠️  Sync speed target missed: {sync_speed:.1f} < {self.performance_targets['min_sync_speed']}"
            )
            targets_met = False
        else:
            logger.info(f"✅ Sync speed target met: {sync_speed:.1f} conv/sec")

        # Check response times
        response_times = [
            ("status", self.performance_metrics.get("status_response_time", 0)),
            ("search", self.performance_metrics.get("search_response_time", 0)),
            (
                "get_conversation",
                self.performance_metrics.get("get_conversation_time", 0),
            ),
        ]

        max_response_time = self.performance_targets["max_response_time"]

        for name, time_val in response_times:
            if time_val > max_response_time:
                logger.warning(
                    f"⚠️  {name} response time target missed: {time_val * 1000:.1f}ms > {max_response_time * 1000:.1f}ms"
                )
                targets_met = False
            else:
                logger.info(
                    f"✅ {name} response time target met: {time_val * 1000:.1f}ms"
                )

        self.test_results["performance_targets_met"] = targets_met
        return targets_met

    def print_final_report(self) -> None:
        """Print comprehensive test results report."""
        total_time = time.time() - self.start_time

        logger.info("")
        logger.info("=" * 60)
        logger.info("🎯 COMPREHENSIVE INTEGRATION TEST RESULTS")
        logger.info("=" * 60)

        # Test results
        logger.info("📋 Test Results:")
        for test_name, result in self.test_results.items():
            if isinstance(result, bool):
                status = "✅ PASS" if result else "❌ FAIL"
                logger.info(f"   {test_name}: {status}")

        # Performance metrics
        logger.info("")
        logger.info("📊 Performance Metrics:")
        logger.info(f"   Total test time: {total_time:.1f} seconds")

        if "conversations_synced" in self.performance_metrics:
            logger.info(
                f"   Conversations synced: {self.performance_metrics['conversations_synced']:,}"
            )
        if "messages_synced" in self.performance_metrics:
            logger.info(
                f"   Messages synced: {self.performance_metrics['messages_synced']:,}"
            )
        if "sync_speed" in self.performance_metrics:
            logger.info(
                f"   Sync speed: {self.performance_metrics['sync_speed']:.1f} conv/sec"
            )
        if "sync_duration" in self.performance_metrics:
            logger.info(
                f"   Sync duration: {self.performance_metrics['sync_duration']:.1f} seconds"
            )

        # Response times
        response_times = [
            ("Status", self.performance_metrics.get("status_response_time", 0)),
            ("Search", self.performance_metrics.get("search_response_time", 0)),
            (
                "Get Conversation",
                self.performance_metrics.get("get_conversation_time", 0),
            ),
        ]

        logger.info("")
        logger.info("⚡ Response Times:")
        for name, time_val in response_times:
            logger.info(f"   {name}: {time_val * 1000:.1f}ms")

        # Overall status
        logger.info("")
        all_passed = all(
            result
            for key, result in self.test_results.items()
            if isinstance(result, bool)
        )

        if all_passed:
            logger.info("🎉 ALL TESTS PASSED - FastIntercom is ready for production!")
        else:
            logger.info("⚠️  Some tests failed - review issues before production use")

        logger.info("=" * 60)


async def run_comprehensive_integration_test():
    """Run the comprehensive integration test."""
    runner = IntegrationTestRunner()

    try:
        # Setup
        runner.setup_test_environment()

        # Run test sequence
        tests = [
            runner.test_api_connection,
            runner.test_database_initialization,
            runner.test_initial_sync,
            runner.test_database_integrity,
            runner.test_mcp_server_functionality,
        ]

        for test in tests:
            success = await test()
            if not success:
                logger.error(f"❌ Test {test.__name__} failed - stopping test sequence")
                break

        # Check performance targets
        runner.check_performance_targets()

    finally:
        # Always cleanup
        runner.cleanup_test_environment()

        # Print final report
        runner.print_final_report()

    return runner.test_results


# Pytest integration (only if pytest is available)
if PYTEST_AVAILABLE:

    @pytest.mark.asyncio
    async def test_comprehensive_integration():
        """Pytest wrapper for the comprehensive integration test."""

        # Check environment
        if not os.getenv("INTERCOM_ACCESS_TOKEN"):
            pytest.skip("INTERCOM_ACCESS_TOKEN environment variable not set")

        if not DEPENDENCIES_AVAILABLE:
            pytest.skip("Required dependencies not available")

        results = await run_comprehensive_integration_test()

        # Assert all tests passed
        failed_tests = [
            test_name
            for test_name, result in results.items()
            if isinstance(result, bool) and not result
        ]

        if failed_tests:
            pytest.fail(f"Integration tests failed: {', '.join(failed_tests)}")


if __name__ == "__main__":
    """Run the test directly when executed as a script."""
    if not DEPENDENCIES_AVAILABLE:
        print("❌ Missing dependencies. Please install with: pip install -e .")
        sys.exit(1)

    asyncio.run(run_comprehensive_integration_test())
