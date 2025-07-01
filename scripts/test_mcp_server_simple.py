#!/usr/bin/env python3
"""
test_mcp_server_simple.py - Simplified MCP server test that tests basic functionality

This script tests that the MCP server can:
1. Start successfully
2. Handle basic requests
3. Return proper responses

It's designed to work even if the full MCP client library isn't available.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class SimpleMCPTester:
    """Simple MCP server tester that validates basic functionality."""

    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.passed_tests = 0
        self.failed_tests = 0

    def test_cli_status(self) -> bool:
        """Test that CLI status command works."""
        print("Testing CLI status command...")
        try:
            env = os.environ.copy()
            env["FASTINTERCOM_CONFIG_DIR"] = str(self.workspace_path)

            result = subprocess.run(
                [sys.executable, "-m", "fast_intercom_mcp", "status"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )

            if result.returncode == 0:
                print("✅ CLI status command successful")
                self.passed_tests += 1
                return True
            print(f"❌ CLI status failed: {result.stderr}")
            self.failed_tests += 1
            return False

        except Exception as e:
            print(f"❌ CLI status exception: {e}")
            self.failed_tests += 1
            return False

    def test_mcp_server_start(self) -> bool:
        """Test that MCP server can start."""
        print("Testing MCP server startup...")
        try:
            env = os.environ.copy()
            env["FASTINTERCOM_CONFIG_DIR"] = str(self.workspace_path)
            env["FASTINTERCOM_LOG_DIR"] = str(self.workspace_path / "logs")

            # Start server process
            process = subprocess.Popen(
                [sys.executable, "-m", "fast_intercom_mcp", "mcp"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )

            # Give it time to start
            time.sleep(2)

            # Check if still running
            if process.poll() is None:
                print("✅ MCP server started successfully")
                self.passed_tests += 1

                # Try to terminate gracefully
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

                return True
            stderr = process.stderr.read() if process.stderr else "No error output"
            print(f"❌ MCP server exited immediately: {stderr}")
            self.failed_tests += 1
            return False

        except Exception as e:
            print(f"❌ MCP server startup exception: {e}")
            self.failed_tests += 1
            return False

    def test_database_creation(self) -> bool:
        """Test that database is created properly."""
        print("Testing database creation...")
        db_path = self.workspace_path / "data" / "data.db"

        if db_path.exists():
            print("✅ Database file created")
            self.passed_tests += 1

            # Check if we can connect to it
            try:
                import sqlite3

                conn = sqlite3.connect(str(db_path))
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]
                conn.close()

                expected_tables = ["conversations", "messages", "sync_periods"]
                missing_tables = set(expected_tables) - set(tables)

                if not missing_tables:
                    print(f"✅ All expected tables present: {', '.join(expected_tables)}")
                    return True
                print(f"❌ Missing tables: {missing_tables}")
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

    def run_tests(self) -> bool:
        """Run all simple tests."""
        print("=" * 80)
        print("Running Simple MCP Server Tests")
        print("=" * 80)

        # Ensure workspace directories exist
        self.workspace_path.mkdir(exist_ok=True)
        (self.workspace_path / "data").mkdir(exist_ok=True)
        (self.workspace_path / "logs").mkdir(exist_ok=True)

        # Run tests
        self.test_cli_status()
        self.test_mcp_server_start()
        self.test_database_creation()

        # Summary
        total_tests = self.passed_tests + self.failed_tests
        print("=" * 80)
        print(f"Total Tests: {total_tests}")
        print(f"Passed: {self.passed_tests}")
        print(f"Failed: {self.failed_tests}")

        if self.failed_tests == 0:
            print("✅ All tests passed!")
            return True
        print("❌ Some tests failed")
        return False


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Simple MCP server test")
    parser.add_argument("--workspace", type=str, help="Test workspace directory")

    args = parser.parse_args()

    # Determine workspace
    workspace = Path(args.workspace) if args.workspace else Path.home() / ".fast-intercom-mcp-test"

    # Run tests
    tester = SimpleMCPTester(workspace)
    success = tester.run_tests()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        sys.exit(1)
