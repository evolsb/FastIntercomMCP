#!/usr/bin/env python3
"""
test_mcp_server.py - Comprehensive MCP protocol testing for FastIntercom MCP

This script tests the MCP server by:
1. Starting the server in stdio mode as a subprocess
2. Sending proper JSON-RPC requests to test all 8 MCP tools
3. Validating responses match expected schemas
4. Handling errors gracefully
5. Providing detailed output for debugging
6. Returning appropriate exit codes

Tools tested:
- search_conversations
- get_conversation
- get_server_status
- sync_conversations
- get_data_info
- check_coverage
- get_sync_status
- force_sync
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fast_intercom_mcp.core.config import setup_logging

# Set up enhanced logging
log_level = os.getenv("FASTINTERCOM_LOG_LEVEL", "INFO")
setup_logging(log_level)

logger = logging.getLogger(__name__)


class Colors:
    """ANSI color codes for terminal output."""

    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    PURPLE = "\033[0;35m"
    CYAN = "\033[0;36m"
    WHITE = "\033[1;37m"
    NC = "\033[0m"  # No Color


class MCPServerTester:
    """Comprehensive MCP server testing class."""

    def __init__(self, workspace_path: Path, verbose: bool = False, timeout: int = 30):
        self.workspace_path = workspace_path
        self.verbose = verbose
        self.timeout = timeout
        self.server_process = None
        self.results = []
        self.total_tests = 0
        self.passed_tests = 0
        self.failed_tests = 0
        self.request_id = 1

        # Ensure workspace directories exist
        self.workspace_path.mkdir(exist_ok=True)
        (self.workspace_path / "logs").mkdir(exist_ok=True)
        (self.workspace_path / "data").mkdir(exist_ok=True)

    def log_info(self, message: str):
        """Log info message."""
        print(f"{Colors.BLUE}ℹ️  {message}{Colors.NC}")
        logger.info(message)

    def log_success(self, message: str):
        """Log success message."""
        print(f"{Colors.GREEN}✅ {message}{Colors.NC}")
        logger.info(f"SUCCESS: {message}")

    def log_warning(self, message: str):
        """Log warning message."""
        print(f"{Colors.YELLOW}⚠️  {message}{Colors.NC}")
        logger.warning(message)

    def log_error(self, message: str):
        """Log error message."""
        print(f"{Colors.RED}❌ {message}{Colors.NC}")
        logger.error(message)

    def log_section(self, message: str):
        """Log section header."""
        print(f"\n{Colors.PURPLE}🔍 {message}{Colors.NC}")
        print("=" * 80)
        logger.info(f"=== {message} ===")

    async def start_mcp_server(self) -> bool:
        """Start the MCP server in stdio mode."""
        self.log_info("Starting MCP server in stdio mode...")

        # Set up environment
        env = os.environ.copy()
        env["FASTINTERCOM_CONFIG_DIR"] = str(self.workspace_path)
        env["FASTINTERCOM_LOG_DIR"] = str(self.workspace_path / "logs")
        env["FASTINTERCOM_LOG_LEVEL"] = "DEBUG" if self.verbose else "INFO"

        # Ensure we have an API token for testing
        if "INTERCOM_ACCESS_TOKEN" not in env:
            self.log_warning("INTERCOM_ACCESS_TOKEN not set - some tests may fail")

        # Start server process
        try:
            self.server_process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "fast_intercom_mcp",
                "mcp",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            # Wait a bit for server to initialize
            await asyncio.sleep(2)

            # Check if server is still running
            if self.server_process.returncode is not None:
                stderr = await self.server_process.stderr.read()
                self.log_error(f"Server exited with code {self.server_process.returncode}")
                self.log_error(f"Error: {stderr.decode()}")
                return False

            self.log_success(f"MCP server started (PID: {self.server_process.pid})")
            return True

        except Exception as e:
            self.log_error(f"Failed to start MCP server: {e}")
            logger.exception("Server startup failed")
            return False

    async def stop_mcp_server(self):
        """Stop the MCP server gracefully."""
        if self.server_process:
            self.log_info("Stopping MCP server...")
            try:
                # Send terminate signal
                self.server_process.terminate()
                await asyncio.wait_for(self.server_process.wait(), timeout=5)
                self.log_success("MCP server stopped gracefully")
            except TimeoutError:
                self.log_warning("Server didn't stop gracefully, killing...")
                self.server_process.kill()
                await self.server_process.wait()
                self.log_success("MCP server killed")

    async def send_mcp_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send an MCP request and get response."""
        if not self.server_process:
            raise RuntimeError("Server not started")

        # Create JSON-RPC request
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self.request_id,
            "params": params or {},
        }
        self.request_id += 1

        # Send request
        request_json = json.dumps(request) + "\n"
        if self.verbose:
            self.log_info(f"Sending: {request_json.strip()}")

        self.server_process.stdin.write(request_json.encode())
        await self.server_process.stdin.drain()

        # Read response with timeout
        try:
            response_line = await asyncio.wait_for(
                self.server_process.stdout.readline(), timeout=self.timeout
            )
        except TimeoutError:
            raise TimeoutError(f"No response after {self.timeout}s for request: {method}")

        if not response_line:
            raise RuntimeError("No response from server")

        response = json.loads(response_line.decode())
        if self.verbose:
            self.log_info(f"Received: {json.dumps(response, indent=2)}")

        return response

    async def test_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        description: str,
        expected_error: str | None = None,
    ) -> dict[str, Any]:
        """Test a specific MCP tool."""
        self.total_tests += 1
        start_time = time.time()

        self.log_info(f"Testing {tool_name}: {description}")

        try:
            response = await self.send_mcp_request(
                "tools/call", {"name": tool_name, "arguments": arguments}
            )

            duration = time.time() - start_time

            # Check for errors
            if "error" in response:
                error_msg = str(response.get("error", {}).get("message", "Unknown error"))

                if expected_error and expected_error in error_msg:
                    # Expected error
                    self.log_success(f"Got expected error: {error_msg}")
                    self.passed_tests += 1
                    return {
                        "tool": tool_name,
                        "status": "PASSED",
                        "description": description,
                        "duration_ms": round(duration * 1000, 2),
                        "expected_error": True,
                        "error_message": error_msg,
                    }
                # Unexpected error
                self.log_error(f"Unexpected error: {error_msg}")
                self.failed_tests += 1
                return {
                    "tool": tool_name,
                    "status": "FAILED",
                    "description": description,
                    "duration_ms": round(duration * 1000, 2),
                    "error": error_msg,
                }

            # Validate successful response
            if "result" not in response:
                self.log_error("Missing 'result' in response")
                self.failed_tests += 1
                return {
                    "tool": tool_name,
                    "status": "FAILED",
                    "description": description,
                    "duration_ms": round(duration * 1000, 2),
                    "error": "Missing result field",
                }

            result = response["result"]
            content = result.get("content", [])

            # Validate content structure
            validation_errors = []
            if not isinstance(content, list):
                validation_errors.append("Content should be a list")
            else:
                for i, item in enumerate(content):
                    if not isinstance(item, dict):
                        validation_errors.append(f"Content item {i} should be a dict")
                    elif "type" not in item:
                        validation_errors.append(f"Content item {i} missing 'type'")
                    elif item["type"] == "text" and "text" not in item:
                        validation_errors.append(f"Text content item {i} missing 'text' field")

            if validation_errors:
                self.log_error(f"Validation errors: {', '.join(validation_errors)}")
                self.failed_tests += 1
                return {
                    "tool": tool_name,
                    "status": "FAILED",
                    "description": description,
                    "duration_ms": round(duration * 1000, 2),
                    "validation_errors": validation_errors,
                }

            # Success
            self.log_success(f"Tool returned valid response ({len(content)} content items)")
            if self.verbose and content:
                # Show first content item as sample
                first_text = content[0].get("text", "")[:200]
                self.log_info(f"Sample response: {first_text}...")

            self.passed_tests += 1
            return {
                "tool": tool_name,
                "status": "PASSED",
                "description": description,
                "duration_ms": round(duration * 1000, 2),
                "content_count": len(content),
            }

        except Exception as e:
            duration = time.time() - start_time
            self.log_error(f"Exception during test: {e}")
            self.failed_tests += 1
            return {
                "tool": tool_name,
                "status": "ERROR",
                "description": description,
                "duration_ms": round(duration * 1000, 2),
                "exception": str(e),
            }

    async def test_list_tools(self) -> bool:
        """Test listing available tools."""
        self.total_tests += 1
        self.log_info("Testing tools/list...")

        try:
            response = await self.send_mcp_request("tools/list")

            # Validate response structure
            if "error" in response:
                self.log_error(f"Error response: {response['error']}")
                self.failed_tests += 1
                return False

            if "result" not in response:
                self.log_error("Missing 'result' in response")
                self.failed_tests += 1
                return False

            tools = response["result"].get("tools", [])
            if not isinstance(tools, list):
                self.log_error("Tools should be a list")
                self.failed_tests += 1
                return False

            # Check we have all expected tools
            tool_names = [tool["name"] for tool in tools]
            expected_tools = [
                "search_conversations",
                "get_conversation",
                "get_server_status",
                "sync_conversations",
                "get_data_info",
                "get_sync_status",
                "check_coverage",
                "force_sync",
            ]

            missing_tools = set(expected_tools) - set(tool_names)
            if missing_tools:
                self.log_error(f"Missing expected tools: {missing_tools}")
                self.failed_tests += 1
                return False

            self.log_success(f"Found all {len(expected_tools)} expected tools")

            # Validate tool schemas
            for tool in tools:
                if "name" not in tool:
                    self.log_error("Tool missing 'name' field")
                    self.failed_tests += 1
                    return False
                if "description" not in tool:
                    self.log_warning(f"Tool {tool['name']} missing 'description' field")
                if "inputSchema" not in tool:
                    self.log_warning(f"Tool {tool['name']} missing 'inputSchema' field")

            self.passed_tests += 1
            return True

        except Exception as e:
            self.log_error(f"Exception during test: {e}")
            self.failed_tests += 1
            return False

    async def get_sample_conversation_id(self) -> str | None:
        """Get a sample conversation ID from the database for testing."""
        try:
            # First try to get from a search
            response = await self.send_mcp_request(
                "tools/call", {"name": "search_conversations", "arguments": {"limit": 1}}
            )

            if "result" in response:
                content = response["result"].get("content", [])
                if content and content[0].get("type") == "text":
                    text = content[0].get("text", "")
                    # Try to parse JSON from text
                    try:
                        data = json.loads(text)
                        conversations = data.get("conversations", [])
                        if conversations and "id" in conversations[0]:
                            return conversations[0]["id"]
                    except:
                        pass

            # Fallback: check database directly
            import sqlite3

            db_path = self.workspace_path / "data" / "data.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                cursor = conn.execute("SELECT id FROM conversations LIMIT 1")
                row = cursor.fetchone()
                conn.close()
                if row:
                    return row[0]

        except Exception as e:
            logger.debug(f"Could not get sample conversation ID: {e}")

        return None

    async def run_all_tests(self) -> bool:
        """Run all MCP server tests."""
        self.log_section("Comprehensive MCP Server Testing")

        # Start server
        if not await self.start_mcp_server():
            self.log_error("Failed to start MCP server")
            return False

        try:
            # Test 1: List tools
            self.log_section("Test 1: List Available Tools")
            await self.test_list_tools()

            # Test 2: Get server status
            self.log_section("Test 2: Server Status Tools")
            result = await self.test_tool("get_server_status", {}, "Get current server status")
            self.results.append(result)

            # Test 3: Get data info
            result = await self.test_tool("get_data_info", {}, "Get database information")
            self.results.append(result)

            # Test 4: Get sync status
            result = await self.test_tool("get_sync_status", {}, "Get synchronization status")
            self.results.append(result)

            # Test 5: Search conversations
            self.log_section("Test 3: Search Tools")

            # Basic search
            result = await self.test_tool(
                "search_conversations",
                {"query": "test", "limit": 5},
                "Search for 'test' conversations",
            )
            self.results.append(result)

            # Time-based search
            result = await self.test_tool(
                "search_conversations",
                {"timeframe": "last 7 days", "limit": 10},
                "Search last 7 days",
            )
            self.results.append(result)

            # Advanced search
            result = await self.test_tool(
                "search_conversations",
                {"query": "billing", "timeframe": "last 30 days", "status": "open", "limit": 3},
                "Advanced search with multiple filters",
            )
            self.results.append(result)

            # Test 6: Get conversation
            self.log_section("Test 4: Conversation Retrieval")

            # Try to get a sample conversation ID
            sample_id = await self.get_sample_conversation_id()
            if sample_id:
                result = await self.test_tool(
                    "get_conversation",
                    {"conversation_id": sample_id},
                    f"Get conversation {sample_id}",
                )
                self.results.append(result)
            else:
                # Test with missing ID (should fail gracefully)
                result = await self.test_tool(
                    "get_conversation",
                    {},
                    "Get conversation without ID (expected error)",
                    expected_error="conversation_id is required",
                )
                self.results.append(result)

            # Test 7: Check coverage
            self.log_section("Test 5: Coverage Analysis")

            # Check recent coverage
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            result = await self.test_tool(
                "check_coverage",
                {"start_date": start_date, "end_date": end_date},
                f"Check coverage for {start_date} to {end_date}",
            )
            self.results.append(result)

            # Test 8: Sync operations
            self.log_section("Test 6: Synchronization Tools")

            # Regular sync
            result = await self.test_tool(
                "sync_conversations", {"force": False}, "Trigger incremental sync"
            )
            self.results.append(result)

            # Force sync (be careful with this in production)
            if os.getenv("FASTINTERCOM_TEST_FORCE_SYNC", "false").lower() == "true":
                result = await self.test_tool("force_sync", {"days": 1}, "Force sync last 1 day")
                self.results.append(result)
            else:
                self.log_warning(
                    "Skipping force_sync test (set FASTINTERCOM_TEST_FORCE_SYNC=true to enable)"
                )

        finally:
            # Stop server
            await self.stop_mcp_server()

        return self.failed_tests == 0

    def generate_report(self) -> dict[str, Any]:
        """Generate comprehensive test report."""
        success_rate = (
            round((self.passed_tests / self.total_tests) * 100, 1) if self.total_tests > 0 else 0
        )

        # Calculate average response time
        response_times = [r["duration_ms"] for r in self.results if "duration_ms" in r]
        avg_response_time = (
            round(sum(response_times) / len(response_times), 2) if response_times else 0
        )

        return {
            "summary": {
                "total_tests": self.total_tests,
                "passed_tests": self.passed_tests,
                "failed_tests": self.failed_tests,
                "success_rate": success_rate,
                "average_response_ms": avg_response_time,
            },
            "test_results": self.results,
            "timestamp": datetime.now().isoformat(),
            "test_configuration": {
                "workspace": str(self.workspace_path),
                "timeout": self.timeout,
                "verbose": self.verbose,
            },
        }

    def print_summary(self):
        """Print test summary."""
        report = self.generate_report()
        summary = report["summary"]

        print(f"\n{Colors.CYAN}📊 MCP Server Test Summary{Colors.NC}")
        print("=" * 80)
        print(f"Total Tests: {summary['total_tests']}")
        print(f"Passed: {Colors.GREEN}{summary['passed_tests']}{Colors.NC}")
        print(f"Failed: {Colors.RED}{summary['failed_tests']}{Colors.NC}")
        print(f"Success Rate: {summary['success_rate']}%")
        print(f"Average Response Time: {summary['average_response_ms']}ms")
        print("=" * 80)

        # Show failed tests
        if self.failed_tests > 0:
            print(f"\n{Colors.RED}Failed Tests:{Colors.NC}")
            for result in self.results:
                if result.get("status") in ["FAILED", "ERROR"]:
                    print(
                        f"  - {result['tool']}: {result.get('error', result.get('validation_errors', 'Unknown error'))}"
                    )

        # Log summary for automated parsing
        logger.info(
            f"MCP_TEST_SUMMARY: total={self.total_tests}, passed={self.passed_tests}, "
            f"failed={self.failed_tests}, success_rate={summary['success_rate']}%, "
            f"avg_response_ms={summary['average_response_ms']}"
        )


async def main():
    """Main test execution."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Comprehensive MCP server testing for FastIntercom MCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic test run
    python3 scripts/test_mcp_server.py

    # Verbose output with custom workspace
    python3 scripts/test_mcp_server.py --verbose --workspace /tmp/mcp-test

    # Extended timeout for slow connections
    python3 scripts/test_mcp_server.py --timeout 60

    # Save results to file
    python3 scripts/test_mcp_server.py --output results.json

Environment Variables:
    INTERCOM_ACCESS_TOKEN - Required for API tests
    FASTINTERCOM_TEST_FORCE_SYNC - Set to 'true' to test force_sync
    FASTINTERCOM_LOG_LEVEL - Set logging level (DEBUG, INFO, WARNING, ERROR)
        """,
    )

    parser.add_argument("--workspace", type=str, help="Test workspace directory")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument(
        "--timeout", type=int, default=30, help="Request timeout in seconds (default: 30)"
    )
    parser.add_argument("--output", help="Save results to JSON file")

    args = parser.parse_args()

    # Determine workspace
    if args.workspace:
        workspace = Path(args.workspace)
    else:
        # Use default test workspace with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace = Path.home() / f".fast-intercom-mcp-test-{timestamp}"

    # Ensure workspace exists
    workspace.mkdir(exist_ok=True, parents=True)

    logger.info(f"Starting MCP server test with workspace: {workspace}")

    # Run tests
    tester = MCPServerTester(workspace, verbose=args.verbose, timeout=args.timeout)
    success = await tester.run_all_tests()
    tester.print_summary()

    # Save results if requested
    if args.output:
        report = tester.generate_report()
        output_path = Path(args.output)

        # Make path absolute if relative
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path

        output_path.parent.mkdir(exist_ok=True, parents=True)

        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

        print(f"\n{Colors.GREEN}Results saved to: {output_path}{Colors.NC}")
        logger.info(f"Test results saved to: {output_path}")

    # Exit with appropriate code
    exit_code = 0 if success else 1
    logger.info(f"Test completed with exit code: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Test interrupted by user{Colors.NC}")
        logger.info("Test interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n{Colors.RED}Test failed with error: {e}{Colors.NC}")
        logger.exception("Test execution failed")
        sys.exit(1)
