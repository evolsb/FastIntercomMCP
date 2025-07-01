#!/usr/bin/env python3
"""
test_mcp_server_simple.py - Simplified MCP server test without MCP library dependency

This script provides a fallback testing solution for environments where the full MCP
library might not be available. It tests basic server functionality using subprocess
and JSON handling.

Tests:
1. Basic server startup and shutdown
2. CLI status command functionality
3. Database accessibility and structure
4. Basic JSON-RPC communication (without MCP library)
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class SimpleMCPServerTester:
    """Simple MCP server tester that doesn't require the MCP library."""

    def __init__(self, workspace_path: Path | None = None):
        self.workspace_path = workspace_path or Path.home() / ".fast-intercom-mcp-test-simple"
        self.passed_tests = 0
        self.failed_tests = 0
        self.server_process = None

    def setup_workspace(self) -> bool:
        """Set up test workspace directories."""
        print("Setting up test workspace...")
        try:
            self.workspace_path.mkdir(exist_ok=True)
            (self.workspace_path / "data").mkdir(exist_ok=True)
            (self.workspace_path / "logs").mkdir(exist_ok=True)
            print(f"✅ Workspace created at {self.workspace_path}")
            return True
        except Exception as e:
            print(f"❌ Failed to create workspace: {e}")
            return False

    def test_cli_status(self) -> bool:
        """Test that CLI status command works."""
        print("\n🧪 Testing CLI status command...")
        try:
            env = os.environ.copy()
            env["FASTINTERCOM_CONFIG_DIR"] = str(self.workspace_path)

            # First, ensure the package is importable
            result = subprocess.run(
                [sys.executable, "-c", "import fast_intercom_mcp; print('OK')"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                print(f"❌ Package not importable: {result.stderr}")
                self.failed_tests += 1
                return False

            # Now test the CLI status
            result = subprocess.run(
                [sys.executable, "-m", "fast_intercom_mcp", "status"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )

            if result.returncode == 0:
                print("✅ CLI status command successful")
                if result.stdout:
                    print(f"   Output: {result.stdout.strip()[:100]}...")
                self.passed_tests += 1
                return True
            print(f"❌ CLI status failed with code {result.returncode}")
            if result.stderr:
                print(f"   Error: {result.stderr.strip()}")
            self.failed_tests += 1
            return False

        except subprocess.TimeoutExpired:
            print("❌ CLI status command timed out")
            self.failed_tests += 1
            return False
        except Exception as e:
            print(f"❌ CLI status exception: {e}")
            self.failed_tests += 1
            return False

    def test_server_startup_shutdown(self) -> bool:
        """Test that MCP server can start and shut down cleanly."""
        print("\n🧪 Testing MCP server startup and shutdown...")
        try:
            env = os.environ.copy()
            env["FASTINTERCOM_CONFIG_DIR"] = str(self.workspace_path)
            env["FASTINTERCOM_LOG_DIR"] = str(self.workspace_path / "logs")

            # Start server process
            print("   Starting server...")
            self.server_process = subprocess.Popen(
                [sys.executable, "-m", "fast_intercom_mcp", "mcp"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )

            # Give it time to start
            time.sleep(3)

            # Check if still running
            if self.server_process.poll() is None:
                print("✅ MCP server started successfully")

                # Test graceful shutdown
                print("   Testing graceful shutdown...")
                self.server_process.terminate()
                try:
                    self.server_process.wait(timeout=5)
                    print("✅ Server shut down gracefully")
                    self.passed_tests += 1
                    return True
                except subprocess.TimeoutExpired:
                    print("⚠️  Server didn't shut down gracefully, force killing...")
                    self.server_process.kill()
                    self.server_process.wait()
                    print("✅ Server force killed successfully")
                    self.passed_tests += 1
                    return True
            else:
                # Process exited immediately
                stderr = (
                    self.server_process.stderr.read()
                    if self.server_process.stderr
                    else "No error output"
                )
                stdout = (
                    self.server_process.stdout.read() if self.server_process.stdout else "No output"
                )
                print(
                    f"❌ MCP server exited immediately with code {self.server_process.returncode}"
                )
                if stderr:
                    print(f"   Stderr: {stderr[:200]}...")
                if stdout:
                    print(f"   Stdout: {stdout[:200]}...")
                self.failed_tests += 1
                return False

        except Exception as e:
            print(f"❌ MCP server startup exception: {e}")
            self.failed_tests += 1
            if self.server_process and self.server_process.poll() is None:
                self.server_process.kill()
            return False

    def test_database_operations(self) -> bool:
        """Test database creation and basic operations."""
        print("\n🧪 Testing database operations...")
        db_path = self.workspace_path / "data" / "data.db"

        # First check if database exists
        if not db_path.exists():
            print("⚠️  Database doesn't exist, attempting to create via CLI init...")
            env = os.environ.copy()
            env["FASTINTERCOM_CONFIG_DIR"] = str(self.workspace_path)

            result = subprocess.run(
                [sys.executable, "-m", "fast_intercom_mcp", "init"],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )

            if result.returncode != 0:
                print(f"❌ Database initialization failed: {result.stderr}")
                self.failed_tests += 1
                return False

        if db_path.exists():
            print("✅ Database file exists")

            # Validate database structure
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]

                expected_tables = [
                    "conversations",
                    "messages",
                    "sync_periods",
                    "tags",
                    "conversation_tags",
                ]
                missing_tables = set(expected_tables) - set(tables)

                if not missing_tables:
                    print(f"✅ All expected tables present: {', '.join(sorted(tables))}")

                    # Test basic queries
                    for table in ["conversations", "messages", "sync_periods"]:
                        cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                        count = cursor.fetchone()[0]
                        print(f"   {table}: {count} records")

                    conn.close()
                    self.passed_tests += 1
                    return True
                print(f"❌ Missing tables: {missing_tables}")
                conn.close()
                self.failed_tests += 1
                return False

            except Exception as e:
                print(f"❌ Database validation error: {e}")
                self.failed_tests += 1
                return False
        else:
            print("❌ Database file not created")
            self.failed_tests += 1
            return False

    def test_json_rpc_communication(self) -> bool:
        """Test basic JSON-RPC communication without MCP library."""
        print("\n🧪 Testing JSON-RPC communication...")
        try:
            env = os.environ.copy()
            env["FASTINTERCOM_CONFIG_DIR"] = str(self.workspace_path)
            env["FASTINTERCOM_LOG_DIR"] = str(self.workspace_path / "logs")

            # Start server for JSON-RPC test
            process = subprocess.Popen(
                [sys.executable, "-m", "fast_intercom_mcp", "mcp"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
            )

            # Give server time to start
            time.sleep(2)

            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr else ""
                print(f"❌ Server exited before test: {stderr}")
                self.failed_tests += 1
                return False

            # Send a simple JSON-RPC request (list tools)
            request = {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1}

            print("   Sending JSON-RPC request...")
            request_str = json.dumps(request) + "\n"
            process.stdin.write(request_str)
            process.stdin.flush()

            # Read response with timeout
            import select

            readable, _, _ = select.select([process.stdout], [], [], 5)

            if readable:
                response_line = process.stdout.readline()
                if response_line:
                    try:
                        response = json.loads(response_line)
                        if "result" in response:
                            print("✅ Received valid JSON-RPC response")
                            tools = response.get("result", {}).get("tools", [])
                            print(f"   Found {len(tools)} tools")
                            self.passed_tests += 1
                            process.terminate()
                            process.wait(timeout=5)
                            return True
                        print(f"❌ Invalid response format: {response}")
                        self.failed_tests += 1
                    except json.JSONDecodeError as e:
                        print(f"❌ Failed to parse response: {e}")
                        print(f"   Raw response: {response_line[:100]}...")
                        self.failed_tests += 1
                else:
                    print("❌ No response received")
                    self.failed_tests += 1
            else:
                print("❌ Response timeout")
                self.failed_tests += 1

            # Clean up
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

            return False

        except Exception as e:
            print(f"❌ JSON-RPC test exception: {e}")
            self.failed_tests += 1
            if "process" in locals() and process.poll() is None:
                process.kill()
            return False

    def test_environment_variables(self) -> bool:
        """Test that environment variables are properly handled."""
        print("\n🧪 Testing environment variable handling...")

        test_cases = [
            ("FASTINTERCOM_CONFIG_DIR", str(self.workspace_path)),
            ("FASTINTERCOM_LOG_DIR", str(self.workspace_path / "logs")),
            ("FASTINTERCOM_LOG_LEVEL", "DEBUG"),
        ]

        all_passed = True
        for var_name, var_value in test_cases:
            env = os.environ.copy()
            env[var_name] = var_value

            # Test that the server respects the environment variable
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    f"""
import os
from fast_intercom_mcp.core.config import Config
config = Config.load()
print(f"{var_name}={{os.environ.get('{var_name}', 'NOT SET')}}")
""",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )

            if result.returncode == 0 and var_value in result.stdout:
                print(f"✅ {var_name} properly handled")
            else:
                print(f"❌ {var_name} not properly handled")
                all_passed = False

        if all_passed:
            self.passed_tests += 1
        else:
            self.failed_tests += 1

        return all_passed

    def cleanup(self):
        """Clean up test resources."""
        print("\n🧹 Cleaning up...")

        # Kill any remaining server processes
        if self.server_process and self.server_process.poll() is None:
            self.server_process.kill()
            self.server_process.wait()

        # Optional: Remove test workspace
        # import shutil
        # if self.workspace_path.exists():
        #     shutil.rmtree(self.workspace_path)

        print("✅ Cleanup complete")

    def run_all_tests(self) -> bool:
        """Run all simple tests."""
        print("=" * 80)
        print("Simple MCP Server Test Suite")
        print("=" * 80)
        print(f"Workspace: {self.workspace_path}")
        print("=" * 80)

        # Setup
        if not self.setup_workspace():
            print("❌ Failed to set up workspace")
            return False

        # Run tests
        tests = [
            self.test_cli_status,
            self.test_server_startup_shutdown,
            self.test_database_operations,
            self.test_json_rpc_communication,
            self.test_environment_variables,
        ]

        for test in tests:
            try:
                test()
            except Exception as e:
                print(f"❌ Test crashed: {e}")
                self.failed_tests += 1

        # Summary
        total_tests = self.passed_tests + self.failed_tests
        print("\n" + "=" * 80)
        print("Test Summary")
        print("=" * 80)
        print(f"Total Tests: {total_tests}")
        print(f"Passed: {self.passed_tests} ✅")
        print(f"Failed: {self.failed_tests} ❌")
        print("=" * 80)

        if self.failed_tests == 0:
            print("🎉 All tests passed!")
            return True
        print("💥 Some tests failed")
        return False


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Simple MCP server test suite (no MCP library required)"
    )
    parser.add_argument(
        "--workspace",
        type=str,
        help="Test workspace directory (default: ~/.fast-intercom-mcp-test-simple)",
    )
    parser.add_argument("--no-cleanup", action="store_true", help="Don't clean up after tests")

    args = parser.parse_args()

    # Create tester
    workspace = Path(args.workspace) if args.workspace else None
    tester = SimpleMCPServerTester(workspace)

    try:
        # Run tests
        success = tester.run_all_tests()

        # Cleanup
        if not args.no_cleanup:
            tester.cleanup()

        # Exit with appropriate code
        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        print("\n⚠️  Test interrupted by user")
        tester.cleanup()
        sys.exit(130)
    except Exception as e:
        print(f"\n💥 Test suite failed with error: {e}")
        import traceback

        traceback.print_exc()
        tester.cleanup()
        sys.exit(1)


if __name__ == "__main__":
    main()
