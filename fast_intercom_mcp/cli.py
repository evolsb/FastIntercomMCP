"""Command line interface for FastIntercom MCP server."""

import asyncio
import contextlib
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click

from .config import Config
from .core.logging import setup_enhanced_logging
from .database import DatabaseManager
from .http_server import FastIntercomHTTPServer
from .intercom_client import IntercomClient
from .mcp_server import FastIntercomMCPServer
from .sync_service import SyncManager

logger = logging.getLogger(__name__)


def _daemonize():
    """Daemonize the current process (Unix/Linux only)."""
    if os.name != "posix":
        click.echo("⚠️  Daemon mode only supported on Unix/Linux systems")
        return

    try:
        # Fork first child
        pid = os.fork()
        if pid > 0:
            sys.exit(0)  # Exit parent
    except OSError as e:
        sys.stderr.write(f"Fork #1 failed: {e}\n")
        sys.exit(1)

    # Decouple from parent environment
    os.chdir("/")
    os.setsid()
    os.umask(0)

    # Fork second child
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)  # Exit second parent
    except OSError as e:
        sys.stderr.write(f"Fork #2 failed: {e}\n")
        sys.exit(1)

    # Redirect standard file descriptors to avoid blocking
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = "/dev/null"
    if hasattr(os, "devnull"):
        devnull = os.devnull

    with open(devnull) as si, open(devnull, "a+") as so, open(devnull, "a+") as se:
        os.dup2(si.fileno(), sys.stdin.fileno())
        os.dup2(so.fileno(), sys.stdout.fileno())
        os.dup2(se.fileno(), sys.stderr.fileno())


@click.group()
@click.option("--config", "-c", help="Configuration file path")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.pass_context
def cli(ctx, config, verbose):
    """FastIntercom MCP Server - Local Intercom conversation access."""
    ctx.ensure_object(dict)

    # Setup logging
    log_level = "DEBUG" if verbose else "INFO"
    setup_enhanced_logging(".", log_level)

    # Load configuration
    try:
        ctx.obj["config"] = Config.load(config)
        if verbose:
            ctx.obj["config"].log_level = "DEBUG"
    except Exception as e:
        click.echo(f"Error loading configuration: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--token",
    prompt="Intercom Access Token",
    hide_input=True,
    help="Your Intercom access token",
)
@click.option(
    "--sync-days",
    default=7,
    type=int,
    help="Number of days of history to sync initially (0 for ALL history)",
)
@click.pass_context
def init(_ctx, token, sync_days):
    """Initialize FastIntercom with your Intercom credentials."""
    click.echo("🚀 Initializing FastIntercom MCP Server...")

    # Validate sync_days (0 means ALL history, no upper limit)
    if sync_days < 0:
        sync_days = 7  # Default to 7 if negative

    # Save configuration
    config = Config(intercom_token=token, initial_sync_days=sync_days)
    config.save()

    click.echo(f"✅ Configuration saved to {Config.get_default_config_path()}")

    # Test connection
    async def test_connection():
        client = IntercomClient(token, timeout=config.api_timeout_seconds)
        if await client.test_connection():
            click.echo("✅ Connection to Intercom API successful")
            app_id = await client.get_app_id()
            if app_id:
                click.echo(f"📱 App ID: {app_id}")
            return True
        click.echo("❌ Failed to connect to Intercom API")
        return False

    if not asyncio.run(test_connection()):
        click.echo("Please check your access token and try again.")
        sys.exit(1)

    # Initialize database
    db = DatabaseManager(config.database_path, config.connection_pool_size)
    click.echo(f"📁 Database initialized at {db.db_path}")

    # Perform initial sync
    if click.confirm(f"Would you like to sync {sync_days} days of conversation history now?"):
        click.echo("🔄 Starting initial sync (this may take a few minutes)...")

        async def initial_sync():
            client = IntercomClient(token, timeout=config.api_timeout_seconds)
            sync_manager = SyncManager(db, client)
            sync_service = sync_manager.get_sync_service()

            try:
                stats = await sync_service.sync_initial(sync_days)
                click.echo("✅ Initial sync completed!")
                click.echo(f"   - {stats.total_conversations:,} conversations")
                click.echo(f"   - {stats.total_messages:,} messages")
                click.echo(f"   - {stats.duration_seconds:.1f} seconds")
            except Exception as e:
                click.echo(f"❌ Initial sync failed: {e}")
                return False
            return True

        if asyncio.run(initial_sync()):
            click.echo("\n🎉 FastIntercom is ready to use!")
            click.echo("Next steps:")
            click.echo("  1. Run 'fastintercom start' to start the MCP server")
            click.echo("  2. Configure Claude Desktop to use this MCP server")
            click.echo("  3. Start asking questions about your Intercom conversations!")
        else:
            click.echo("Initial sync failed, but you can retry later with 'fastintercom sync'")


@cli.command()
@click.option("--daemon", "-d", is_flag=True, help="Run as daemon (background process)")
@click.option(
    "--port",
    default=None,
    type=int,
    help="Port for HTTP MCP server (default: stdio mode)",
)
@click.option("--host", default="0.0.0.0", help="Host for HTTP server (default: 0.0.0.0)")
@click.option("--api-key", help="API key for HTTP authentication (auto-generated if not provided)")
@click.pass_context
def start(ctx, daemon, port, host, api_key):
    """Start the FastIntercom MCP server."""
    config = ctx.obj["config"]

    if daemon:
        click.echo("🚀 Starting FastIntercom MCP Server in daemon mode...")
        _daemonize()

    # Determine transport mode
    if port:
        click.echo(f"🌐 Starting FastIntercom HTTP MCP Server on {host}:{port}...")
        transport_mode = "http"
    else:
        click.echo("🚀 Starting FastIntercom MCP Server (stdio mode)...")
        transport_mode = "stdio"

    # Initialize components
    db = DatabaseManager(config.database_path, config.connection_pool_size)
    intercom_client = IntercomClient(config.intercom_token, config.api_timeout_seconds)
    sync_manager = SyncManager(db, intercom_client)

    # Create appropriate server based on transport mode
    if transport_mode == "http":
        server = FastIntercomHTTPServer(
            db,
            sync_manager.get_sync_service(),
            intercom_client,
            api_key=api_key,
            host=host,
            port=port,
        )
    else:
        server = FastIntercomMCPServer(db, sync_manager.get_sync_service(), intercom_client)

    # Setup signal handlers for graceful shutdown
    def signal_handler(_signum, _frame):
        click.echo("\n🛑 Shutting down gracefully...")
        if transport_mode == "http":
            sync_manager.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    async def run_server():
        # Test connection
        if not await intercom_client.test_connection():
            click.echo("❌ Failed to connect to Intercom API. Check your token.")
            return False

        click.echo("✅ Connected to Intercom API")

        if transport_mode == "http":
            # HTTP mode: start external sync manager
            sync_manager.start()
            click.echo("🔄 Background sync service started")

            # Show connection info for HTTP mode
            conn_info = server.get_connection_info()
            click.echo("📡 HTTP MCP server ready!")
            click.echo(f"   URL: {conn_info['url']}")
            click.echo(f"   API Key: {conn_info['authentication']['token']}")
            click.echo(f"   Health: {conn_info['endpoints']['health']}")
            click.echo("   (Press Ctrl+C to stop)")

            try:
                await server.start()
            except KeyboardInterrupt:
                pass
            finally:
                await server.stop()
                sync_manager.stop()
        else:
            # Stdio mode: MCP server manages its own sync
            click.echo("🔄 Background sync service started")
            click.echo("📡 MCP server listening for requests...")
            click.echo("   (Press Ctrl+C to stop)")

            with contextlib.suppress(KeyboardInterrupt):
                await server.run()

        return True

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully without error message
        pass
    except Exception as e:
        with contextlib.suppress(Exception):
            click.echo(f"❌ Server error: {e}")
        sys.exit(1)


@cli.command()
@click.option("--port", default=8000, type=int, help="Port for HTTP server")
@click.option("--host", default="0.0.0.0", help="Host for HTTP server")
@click.option("--api-key", help="API key for authentication (auto-generated if not provided)")
@click.pass_context
def serve(ctx, port, host, api_key):
    """Start the FastIntercom HTTP MCP server."""
    config = ctx.obj["config"]

    click.echo(f"🌐 Starting FastIntercom HTTP MCP Server on {host}:{port}...")

    # Initialize components
    db = DatabaseManager(config.database_path, config.connection_pool_size)
    intercom_client = IntercomClient(config.intercom_token, config.api_timeout_seconds)
    sync_manager = SyncManager(db, intercom_client)

    server = FastIntercomHTTPServer(
        db,
        sync_manager.get_sync_service(),
        intercom_client,
        api_key=api_key,
        host=host,
        port=port,
    )

    # Setup signal handlers for graceful shutdown
    def signal_handler(_signum, _frame):
        click.echo("\n🛑 Shutting down gracefully...")
        sync_manager.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    async def run_server():
        # Start background sync
        sync_manager.start()

        # Test connection
        if not await intercom_client.test_connection():
            click.echo("❌ Failed to connect to Intercom API. Check your token.")
            return False

        click.echo("✅ Connected to Intercom API")
        click.echo("🔄 Background sync service started")

        # Show connection info
        conn_info = server.get_connection_info()
        click.echo("📡 HTTP MCP server ready!")
        click.echo(f"   URL: {conn_info['url']}")
        click.echo(f"   API Key: {conn_info['authentication']['token']}")
        click.echo(f"   Health: {conn_info['endpoints']['health']}")
        click.echo("   (Press Ctrl+C to stop)")

        try:
            await server.start()
        except KeyboardInterrupt:
            pass
        finally:
            await server.stop()
            sync_manager.stop()

        return True

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        click.echo(f"❌ Server error: {e}")
        sys.exit(1)


@cli.command()
@click.pass_context
def mcp(ctx):
    """Start the FastIntercom MCP server in stdio mode (for MCP clients)."""
    config = ctx.obj["config"]

    # Initialize components
    db = DatabaseManager(config.database_path, config.connection_pool_size)
    intercom_client = IntercomClient(config.intercom_token, config.api_timeout_seconds)
    sync_manager = SyncManager(db, intercom_client)
    mcp_server = FastIntercomMCPServer(db, sync_manager.get_sync_service(), intercom_client)

    async def run_mcp_server():
        # Note: MCP server will start its own background sync
        try:
            await mcp_server.run()
        finally:
            # Ensure cleanup
            pass

    try:
        asyncio.run(run_mcp_server())
    except Exception as e:
        # Log error but don't print to stdout (would interfere with MCP protocol)
        logger.error(f"MCP server error: {e}")
        sys.exit(1)


@cli.command()
@click.pass_context
def status(ctx):
    """Show server status and statistics."""
    config = ctx.obj["config"]

    # Check if database exists
    db_path = config.database_path or (Path.home() / ".fastintercom" / "data.db")
    if not Path(db_path).exists():
        click.echo("❌ Database not found. Run 'fastintercom init' first.")
        return

    db = DatabaseManager(config.database_path, config.connection_pool_size)
    status = db.get_sync_status()

    click.echo("📊 FastIntercom Server Status")
    click.echo("=" * 40)
    click.echo(f"💾 Storage: {status['database_size_mb']} MB")
    click.echo(f"💬 Conversations: {status['total_conversations']:,}")
    click.echo(f"✉️  Messages: {status['total_messages']:,}")

    if status["last_sync"]:
        last_sync = datetime.fromisoformat(status["last_sync"])
        time_diff = datetime.now() - last_sync
        if time_diff.total_seconds() < 60:
            time_str = "just now"
        elif time_diff.total_seconds() < 3600:
            time_str = f"{int(time_diff.total_seconds() / 60)} minutes ago"
        else:
            time_str = f"{int(time_diff.total_seconds() / 3600)} hours ago"
        click.echo(f"🕒 Last Sync: {time_str}")
    else:
        click.echo("🕒 Last Sync: Never")

    click.echo(f"📁 Database: {status['database_path']}")

    # Recent sync activity
    if status["recent_syncs"]:
        click.echo("\n📈 Recent Sync Activity:")
        for sync in status["recent_syncs"][:5]:
            sync_time = datetime.fromisoformat(sync["last_synced"])
            click.echo(
                f"  {sync_time.strftime('%m/%d %H:%M')}: "
                f"{sync['conversation_count']} conversations "
                f"({sync.get('new_conversations', 0)} new)"
            )


@cli.command()
@click.option("--force", "-f", is_flag=True, help="Force full sync of recent data")
@click.option("--days", "-d", default=1, type=int, help="Number of days to sync (for force mode)")
@click.pass_context
def sync(ctx, force, days):
    """Manually trigger conversation sync."""
    config = ctx.obj["config"]

    click.echo("🔄 Starting manual sync...")

    async def run_sync():
        db = DatabaseManager(config.database_path, config.connection_pool_size)
        intercom_client = IntercomClient(config.intercom_token, config.api_timeout_seconds)
        sync_manager = SyncManager(db, intercom_client)
        sync_service = sync_manager.get_sync_service()

        try:
            if force:
                # Force sync specified days
                days_clamped = min(days, 30)  # Max 30 days
                click.echo(f"📅 Force syncing last {days_clamped} days...")
                now = datetime.now()
                start_date = now - timedelta(days=days_clamped)

                # Add progress callback for better UX
                def progress_callback(current: int, total: int, elapsed: float):
                    # This will be called by sync_service
                    pass

                stats = await sync_service.sync_period(start_date, now, progress_callback)
            else:
                # Incremental sync
                click.echo("⚡ Running incremental sync...")
                stats = await sync_service.sync_recent()

            click.echo("✅ Sync completed!")
            click.echo(f"   - {stats.total_conversations:,} conversations")
            click.echo(f"   - {stats.new_conversations:,} new")
            click.echo(f"   - {stats.updated_conversations:,} updated")
            click.echo(f"   - {stats.total_messages:,} messages")
            click.echo(f"   - {stats.duration_seconds:.1f} seconds")

            # Show per-date breakdown if available
            if stats.conversations_by_date:
                click.echo("   📅 By date:")
                for date_key in sorted(stats.conversations_by_date.keys()):
                    conv_count = stats.conversations_by_date[date_key]
                    msg_count = (
                        stats.messages_by_date.get(date_key, 0) if stats.messages_by_date else 0
                    )
                    date_str = (
                        date_key.strftime("%b %d")
                        if hasattr(date_key, "strftime")
                        else str(date_key)
                    )
                    click.echo(
                        f"     {date_str}: {conv_count:,} conversations, {msg_count:,} messages"
                    )

            if stats.errors_encountered > 0:
                click.echo(f"   - ⚠️  {stats.errors_encountered} errors")

        except Exception as e:
            click.echo(f"❌ Sync failed: {e}")
            sys.exit(1)

    asyncio.run(run_sync())


@cli.group()
def logs():
    """Enhanced log management and monitoring commands."""
    pass


@logs.command()
@click.option("--follow", "-f", is_flag=True, help="Follow log output in real-time")
@click.option("--filter", "-F", help="Filter logs by level (DEBUG, INFO, WARNING, ERROR)")
@click.option("--component", "-c", help="Filter logs by component (sync, api, database)")
@click.option("--lines", "-n", default=50, type=int, help="Number of lines to show")
@click.option("--since", "-s", help='Show logs since time (e.g., "1h", "30m", "2024-07-01")')
def show(follow, filter, component, lines, since):
    """Show recent log entries with filtering options."""
    import subprocess

    # Determine log directory
    log_dir = Path.home() / ".fastintercom" / "logs"

    # Check for test workspace logs
    if os.getenv("FASTINTERCOM_LOG_DIR"):
        log_dir = Path(os.getenv("FASTINTERCOM_LOG_DIR"))

    if not log_dir.exists():
        click.echo("❌ No log directory found.")
        click.echo(f"Expected location: {log_dir}")
        return

    # Find the main log file
    main_log = log_dir / "main.log"
    if not main_log.exists():
        # Fallback to legacy log file
        main_log = log_dir / "fastintercom.log"
        if not main_log.exists():
            click.echo("❌ No log file found.")
            click.echo(f"Searched: {log_dir}")
            return

    try:
        if follow:
            # Real-time following
            click.echo(f"📄 Following logs from {main_log}")
            click.echo("Press Ctrl+C to stop")
            click.echo("-" * 60)

            # Use tail -f for real-time following
            cmd = ["tail", "-f", str(main_log)]
            if lines > 0:
                cmd = ["tail", "-f", "-n", str(lines), str(main_log)]

            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            try:
                for line in iter(process.stdout.readline, ""):
                    if line:
                        formatted_line = _format_log_line(line.rstrip(), filter, component)
                        if formatted_line:
                            click.echo(formatted_line)
            except KeyboardInterrupt:
                process.terminate()
                click.echo("\n📄 Log following stopped")
        else:
            # Static log display
            with open(main_log) as f:
                all_lines = f.readlines()

                # Apply time filtering if specified
                if since:
                    filtered_lines = _filter_logs_by_time(all_lines, since)
                else:
                    filtered_lines = all_lines[-lines:] if lines > 0 else all_lines

                # Apply content filtering
                for line in filtered_lines:
                    formatted_line = _format_log_line(line.rstrip(), filter, component)
                    if formatted_line:
                        click.echo(formatted_line)

    except Exception as e:
        click.echo(f"❌ Error reading log file: {e}")


@logs.command()
@click.option("--since", "-s", default="1h", help="Show errors since time period")
@click.option("--count", "-n", default=20, type=int, help="Number of errors to show")
@click.option("--summary", is_flag=True, help="Show error summary instead of details")
def errors(since, count, summary):
    # TODO: Implement since filtering
    _ = since  # Acknowledge unused parameter
    """Show recent errors with analysis."""
    log_dir = Path.home() / ".fastintercom" / "logs"

    # Check for test workspace logs
    if os.getenv("FASTINTERCOM_LOG_DIR"):
        log_dir = Path(os.getenv("FASTINTERCOM_LOG_DIR"))

    # Check errors.log first, then fall back to main.log
    error_log = log_dir / "errors.log"
    main_log = log_dir / "main.log"

    log_file = error_log if error_log.exists() else main_log

    if not log_file.exists():
        click.echo("❌ No log files found for error analysis.")
        return

    try:
        with open(log_file) as f:
            lines = f.readlines()

        # Filter for ERROR level logs
        error_lines = []
        for line in lines:
            if "[ERROR]" in line or "ERROR" in line:
                error_lines.append(line.strip())

        if not error_lines:
            click.echo("✅ No errors found in recent logs!")
            return

        if summary:
            _show_error_summary(error_lines)
        else:
            click.echo(f"🚨 Recent Errors (last {count})")
            click.echo("═" * 60)

            for line in error_lines[-count:]:
                # Extract timestamp and error
                click.echo(click.style(line, fg="red"))

    except Exception as e:
        click.echo(f"❌ Error analyzing logs: {e}")


@logs.command()
@click.option("--output", "-o", help="Output file path")
@click.option(
    "--format",
    "output_format",
    default="text",
    type=click.Choice(["text", "json"]),
    help="Output format",
)
@click.option("--since", "-s", help="Export logs since time period")
@click.option("--level", help="Filter by log level")
def export(output, output_format, since, level):
    """Export logs for external analysis."""
    import json
    from datetime import datetime

    log_dir = Path.home() / ".fastintercom" / "logs"

    # Check for test workspace logs
    if os.getenv("FASTINTERCOM_LOG_DIR"):
        log_dir = Path(os.getenv("FASTINTERCOM_LOG_DIR"))

    if not log_dir.exists():
        click.echo("❌ No log directory found.")
        return

    # Collect all log files
    log_files = {
        "main": log_dir / "main.log",
        "sync": log_dir / "sync.log",
        "errors": log_dir / "errors.log",
    }

    if not output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = f"fastintercom_logs_{timestamp}.{output_format}"

    try:
        exported_data = {
            "export_time": datetime.now().isoformat(),
            "log_directory": str(log_dir),
            "filters": {"since": since, "level": level},
            "logs": {},
        }

        for log_type, log_file in log_files.items():
            if log_file.exists():
                with open(log_file) as f:
                    content = f.readlines()

                # Apply filters
                if level:
                    content = [line for line in content if f"[{level.upper()}]" in line]

                if since:
                    content = _filter_logs_by_time(content, since)

                if output_format == "json":
                    exported_data["logs"][log_type] = [line.strip() for line in content]
                else:
                    exported_data["logs"][log_type] = "".join(content)

        # Write output
        with open(output, "w") as f:
            if output_format == "json":
                json.dump(exported_data, f, indent=2)
            else:
                f.write("FastIntercom MCP Logs Export\n")
                f.write(f"Generated: {exported_data['export_time']}\n")
                f.write(f"Source: {exported_data['log_directory']}\n")
                f.write("=" * 60 + "\n\n")

                for log_type, content in exported_data["logs"].items():
                    f.write(f"\n=== {log_type.upper()} LOGS ===\n")
                    f.write(content)
                    f.write("\n")

        click.echo(f"✅ Logs exported to: {output}")

    except Exception as e:
        click.echo(f"❌ Error exporting logs: {e}")


def _format_log_line(line, level_filter=None, component_filter=None):
    """Format and filter a log line for display."""
    if not line.strip():
        return None

    # Apply level filter
    if level_filter and f"[{level_filter.upper()}]" not in line:
        return None

    # Apply component filter
    if component_filter:
        component_map = {"sync": "sync_service", "api": "intercom_client", "database": "database"}
        component_name = component_map.get(component_filter.lower(), component_filter)
        if component_name not in line:
            return None

    # Add color coding based on log level
    if "[ERROR]" in line:
        return click.style(line, fg="red")
    if "[WARNING]" in line:
        return click.style(line, fg="yellow")
    if "[DEBUG]" in line:
        return click.style(line, fg="cyan")
    return line


def _filter_logs_by_time(lines, since_str):
    """Filter log lines by time period."""
    # TODO: Implement time-based filtering
    _ = since_str  # Acknowledge unused parameter
    # This is a simplified version - in production you'd want more robust time parsing
    return lines  # For now, return all lines


def _show_error_summary(error_lines):
    """Show a summary of errors by type."""
    error_counts = {}

    for line in error_lines:
        # Extract error type (simplified)
        if "API" in line:
            error_type = "API Errors"
        elif "Database" in line or "database" in line:
            error_type = "Database Errors"
        elif "Sync" in line or "sync" in line:
            error_type = "Sync Errors"
        else:
            error_type = "Other Errors"

        error_counts[error_type] = error_counts.get(error_type, 0) + 1

    click.echo("📊 Error Summary:")
    click.echo("═" * 30)
    for error_type, count in error_counts.items():
        click.echo(f"   • {error_type}: {count} occurrences")

    if error_counts:
        click.echo("\n🔧 Suggested Actions:")
        if "API Errors" in error_counts:
            click.echo("   • Check network connectivity and API token")
        if "Database Errors" in error_counts:
            click.echo("   • Check database file permissions and disk space")
        if "Sync Errors" in error_counts:
            click.echo("   • Review sync configuration and recent changes")


@cli.group()
def monitor():
    """Real-time monitoring and status dashboard commands."""
    pass


@monitor.command()
@click.option("--refresh", "-r", default=5, type=int, help="Refresh interval in seconds")
@click.option("--json", "output_json", is_flag=True, help="Output JSON for automation")
def dashboard(refresh, output_json):
    """Real-time status monitoring dashboard."""
    import json
    import time
    from datetime import datetime

    if output_json:
        # Single JSON output for automation
        status_data = _get_system_status()
        click.echo(json.dumps(status_data, indent=2))
        return

    # Interactive dashboard
    click.echo("📊 FastIntercom MCP - Live Monitor")
    click.echo("Press Ctrl+C to stop")
    click.echo("=" * 60)

    try:
        while True:
            # Clear screen (works on most terminals)
            click.clear()

            # Header
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            click.echo(f"📊 FastIntercom MCP Monitor - {current_time}")
            click.echo("=" * 60)

            # Get current status
            status_data = _get_system_status()

            # Display status sections
            _display_status_section(
                "🔗 Connection Status",
                {
                    "API Connection": "✅ Connected"
                    if status_data["api"]["connected"]
                    else "❌ Disconnected",
                    "Database": "✅ Available"
                    if status_data["database"]["available"]
                    else "❌ Unavailable",
                    "Last Check": status_data["last_check"],
                },
            )

            _display_status_section(
                "📊 Data Summary",
                {
                    "Conversations": f"{status_data['database']['conversations']:,}",
                    "Messages": f"{status_data['database']['messages']:,}",
                    "Database Size": f"{status_data['database']['size_mb']} MB",
                },
            )

            _display_status_section(
                "⚡ Recent Activity",
                {
                    "Last Sync": status_data["sync"]["last_sync"],
                    "Sync Status": status_data["sync"]["status"],
                    "Recent Errors": f"{status_data['errors']['count']} in last hour",
                },
            )

            click.echo(f"\n🔄 Refreshing every {refresh}s (Press Ctrl+C to stop)")

            time.sleep(refresh)

    except KeyboardInterrupt:
        click.echo("\n📊 Monitoring stopped")


@monitor.command()
@click.option("--component", "-c", help="Monitor specific component (sync, api, database)")
@click.option("--interval", "-i", default=2, type=int, help="Update interval in seconds")
def logs(component, interval):
    """Monitor logs in real-time with filtering."""
    log_dir = Path.home() / ".fastintercom" / "logs"

    # Check for test workspace logs
    if os.getenv("FASTINTERCOM_LOG_DIR"):
        log_dir = Path(os.getenv("FASTINTERCOM_LOG_DIR"))

    if not log_dir.exists():
        click.echo("❌ No log directory found.")
        return

    # Determine which log file to monitor
    if component == "sync":
        log_file = log_dir / "sync.log"
    elif component == "api":
        log_file = log_dir / "main.log"  # API logs go to main.log
    elif component == "database":
        log_file = log_dir / "main.log"  # Database logs go to main.log
    else:
        log_file = log_dir / "main.log"

    if not log_file.exists():
        click.echo(f"❌ Log file not found: {log_file}")
        return

    click.echo(f"📄 Monitoring {log_file} (Press Ctrl+C to stop)")
    click.echo("-" * 60)

    # Use tail -f for real-time monitoring
    import subprocess

    try:
        process = subprocess.Popen(
            ["tail", "-f", str(log_file)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        for line in iter(process.stdout.readline, ""):
            if line:
                # Add timestamp and color coding
                timestamp = datetime.now().strftime("%H:%M:%S")
                formatted_line = _format_log_line(line.rstrip())
                if formatted_line:
                    click.echo(f"[{timestamp}] {formatted_line}")

    except KeyboardInterrupt:
        if "process" in locals():
            process.terminate()
        click.echo("\n📄 Log monitoring stopped")


@cli.group()
def debug():
    """Debugging and diagnostic commands."""
    pass


@debug.command()
@click.option("--verbose", "-v", is_flag=True, help="Show detailed health information")
@click.option("--json", "output_json", is_flag=True, help="Output JSON for automation")
def health(verbose, output_json):
    """Comprehensive system health check."""
    import json

    health_data = _run_health_checks(verbose)

    if output_json:
        click.echo(json.dumps(health_data, indent=2))
        return

    # Display health check results
    click.echo("🏥 FastIntercom MCP Health Check")
    click.echo("=" * 50)

    overall_health = "EXCELLENT"
    issues = []

    for category, checks in health_data["checks"].items():
        click.echo(f"\n{_get_category_emoji(category)} {category.title()}")

        for check_name, result in checks.items():
            status_icon = "✅" if result["status"] == "PASS" else "❌"
            click.echo(f"   ├─ {check_name}: {status_icon}")

            if verbose and result.get("details"):
                click.echo(f"      └─ {result['details']}")

            if result["status"] != "PASS":
                issues.append(f"{category}: {check_name}")
                overall_health = "NEEDS_ATTENTION"

    # Overall summary
    click.echo(f"\n🎯 Overall Health: {overall_health}")

    if issues:
        click.echo("\n💡 Issues Found:")
        for issue in issues:
            click.echo(f"   • {issue}")
    else:
        click.echo("\n✅ All systems operating normally")


@debug.command()
@click.option("--show-config", is_flag=True, help="Show current configuration")
@click.option("--test-api", is_flag=True, help="Test API connectivity")
@click.option("--test-database", is_flag=True, help="Test database operations")
def diagnose(show_config, test_api, test_database):
    """Run specific diagnostic tests."""
    if not any([show_config, test_api, test_database]):
        click.echo("Please specify at least one diagnostic option")
        click.echo("Use --help to see available options")
        return

    if show_config:
        _show_configuration()

    if test_api:
        _test_api_connectivity()

    if test_database:
        _test_database_operations()


def _get_system_status():
    """Get current system status data."""
    import os
    import sqlite3
    from datetime import datetime

    status = {
        "timestamp": datetime.now().isoformat(),
        "last_check": datetime.now().strftime("%H:%M:%S"),
        "api": {"connected": False},
        "database": {"available": False, "conversations": 0, "messages": 0, "size_mb": 0},
        "sync": {"last_sync": "Unknown", "status": "Unknown"},
        "errors": {"count": 0},
    }

    # Check database
    try:
        db_path = Path.home() / ".fastintercom" / "data.db"
        if os.getenv("FASTINTERCOM_CONFIG_DIR"):
            db_path = Path(os.getenv("FASTINTERCOM_CONFIG_DIR")) / "data.db"

        if db_path.exists():
            status["database"]["available"] = True
            status["database"]["size_mb"] = round(db_path.stat().st_size / (1024 * 1024), 1)

            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM conversations")
                status["database"]["conversations"] = cursor.fetchone()[0]

                cursor = conn.execute("SELECT COUNT(*) FROM messages")
                status["database"]["messages"] = cursor.fetchone()[0]
    except Exception:
        pass

    # Check API (simplified)
    try:
        import os

        if os.getenv("INTERCOM_ACCESS_TOKEN"):
            status["api"]["connected"] = True
    except Exception:
        pass

    return status


def _display_status_section(title, data):
    """Display a status section with consistent formatting."""
    click.echo(f"\n{title}")
    click.echo("-" * len(title))
    for key, value in data.items():
        click.echo(f"   {key}: {value}")


def _run_health_checks(verbose=False):
    """Run comprehensive health checks."""
    _ = verbose  # Acknowledge unused parameter
    import os
    import sqlite3

    import requests

    health_data = {
        "timestamp": datetime.now().isoformat(),
        "checks": {"configuration": {}, "api": {}, "database": {}, "logging": {}},
    }

    # Configuration checks
    try:
        config = Config.load()
        health_data["checks"]["configuration"]["config_file"] = {
            "status": "PASS",
            "details": "Configuration loaded successfully",
        }
        health_data["checks"]["configuration"]["api_token"] = {
            "status": "PASS" if config.intercom_token else "FAIL",
            "details": "Token present" if config.intercom_token else "Token missing",
        }
    except Exception as e:
        health_data["checks"]["configuration"]["config_file"] = {
            "status": "FAIL",
            "details": str(e),
        }

    # API checks
    try:
        if config.intercom_token:
            # Simple API test
            response = requests.get(
                "https://api.intercom.io/me",
                headers={"Authorization": f"Bearer {config.intercom_token}"},
                timeout=5,
            )
            health_data["checks"]["api"]["connectivity"] = {
                "status": "PASS" if response.status_code == 200 else "FAIL",
                "details": f"HTTP {response.status_code}",
            }
    except Exception as e:
        health_data["checks"]["api"]["connectivity"] = {"status": "FAIL", "details": str(e)}

    # Database checks
    try:
        db_path = Path.home() / ".fastintercom" / "data.db"
        if os.getenv("FASTINTERCOM_CONFIG_DIR"):
            db_path = Path(os.getenv("FASTINTERCOM_CONFIG_DIR")) / "data.db"

        if db_path.exists():
            health_data["checks"]["database"]["file_exists"] = {
                "status": "PASS",
                "details": f"Size: {db_path.stat().st_size / (1024*1024):.1f} MB",
            }

            with sqlite3.connect(str(db_path)) as conn:
                cursor = conn.execute("PRAGMA integrity_check")
                result = cursor.fetchone()[0]
                health_data["checks"]["database"]["integrity"] = {
                    "status": "PASS" if result == "ok" else "FAIL",
                    "details": result,
                }
        else:
            health_data["checks"]["database"]["file_exists"] = {
                "status": "FAIL",
                "details": "Database file not found",
            }
    except Exception as e:
        health_data["checks"]["database"]["connection"] = {"status": "FAIL", "details": str(e)}

    # Logging checks
    log_dir = Path.home() / ".fastintercom" / "logs"
    if os.getenv("FASTINTERCOM_LOG_DIR"):
        log_dir = Path(os.getenv("FASTINTERCOM_LOG_DIR"))

    health_data["checks"]["logging"]["directory"] = {
        "status": "PASS" if log_dir.exists() else "FAIL",
        "details": str(log_dir),
    }

    for log_file in ["main.log", "sync.log", "errors.log"]:
        log_path = log_dir / log_file
        health_data["checks"]["logging"][log_file] = {
            "status": "PASS" if log_path.exists() else "FAIL",
            "details": f"Size: {log_path.stat().st_size / 1024:.1f} KB"
            if log_path.exists()
            else "Not found",
        }

    return health_data


def _get_category_emoji(category):
    """Get emoji for health check category."""
    emojis = {"configuration": "⚙️", "api": "🔗", "database": "💾", "logging": "📄"}
    return emojis.get(category, "🔍")


def _show_configuration():
    """Show current configuration."""
    click.echo("\n⚙️ Configuration")
    click.echo("-" * 20)
    try:
        config = Config.load()
        click.echo(f"   Database Path: {config.database_path or 'Default'}")
        click.echo(f"   Log Level: {config.log_level}")
        click.echo(f"   Pool Size: {config.connection_pool_size}")
        click.echo(f"   Sync Interval: {config.background_sync_interval_minutes} minutes")
    except Exception as e:
        click.echo(f"   ❌ Error loading config: {e}")


def _test_api_connectivity():
    """Test API connectivity."""
    click.echo("\n🔗 API Connectivity Test")
    click.echo("-" * 25)
    try:
        config = Config.load()
        import requests

        response = requests.get(
            "https://api.intercom.io/me",
            headers={"Authorization": f"Bearer {config.intercom_token}"},
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            click.echo(f"   ✅ Connected to: {data.get('name', 'Unknown workspace')}")
            click.echo(f"   📧 Email: {data.get('email', 'Unknown')}")
        else:
            click.echo(f"   ❌ API error: HTTP {response.status_code}")

    except Exception as e:
        click.echo(f"   ❌ Connection failed: {e}")


def _test_database_operations():
    """Test database operations."""
    click.echo("\n💾 Database Operations Test")
    click.echo("-" * 30)

    try:
        import sqlite3

        db_path = Path.home() / ".fastintercom" / "data.db"
        if os.getenv("FASTINTERCOM_CONFIG_DIR"):
            db_path = Path(os.getenv("FASTINTERCOM_CONFIG_DIR")) / "data.db"

        if not db_path.exists():
            click.echo("   ❌ Database file not found")
            return

        with sqlite3.connect(str(db_path)) as conn:
            # Test basic query
            cursor = conn.execute("SELECT COUNT(*) FROM conversations")
            conv_count = cursor.fetchone()[0]
            click.echo(f"   ✅ Query test: {conv_count:,} conversations")

            # Test integrity
            cursor = conn.execute("PRAGMA integrity_check")
            integrity = cursor.fetchone()[0]
            if integrity == "ok":
                click.echo("   ✅ Integrity check: PASSED")
            else:
                click.echo(f"   ❌ Integrity check: {integrity}")

    except Exception as e:
        click.echo(f"   ❌ Database test failed: {e}")


@cli.command()
@click.confirmation_option(prompt="Are you sure you want to reset all data?")
@click.pass_context
def reset(_ctx):
    """Reset all data (database and configuration)."""
    config_dir = Path.home() / ".fastintercom"

    if config_dir.exists():
        import shutil

        shutil.rmtree(config_dir)
        click.echo("✅ All FastIntercom data has been reset.")
    else:
        click.echo("No data found to reset.")


if __name__ == "__main__":
    cli()
