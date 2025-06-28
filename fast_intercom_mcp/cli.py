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
    if click.confirm(
        f"Would you like to sync {sync_days} days of conversation history now?"
    ):
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
            click.echo(
                "Initial sync failed, but you can retry later with 'fastintercom sync'"
            )


@cli.command()
@click.option("--daemon", "-d", is_flag=True, help="Run as daemon (background process)")
@click.option(
    "--port",
    default=None,
    type=int,
    help="Port for HTTP MCP server (default: stdio mode)",
)
@click.option(
    "--host", default="0.0.0.0", help="Host for HTTP server (default: 0.0.0.0)"
)
@click.option(
    "--api-key", help="API key for HTTP authentication (auto-generated if not provided)"
)
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
        server = FastIntercomMCPServer(
            db, sync_manager.get_sync_service(), intercom_client
        )

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
@click.option(
    "--api-key", help="API key for authentication (auto-generated if not provided)"
)
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
    mcp_server = FastIntercomMCPServer(
        db, sync_manager.get_sync_service(), intercom_client
    )

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
@click.option(
    "--days", "-d", default=1, type=int, help="Number of days to sync (for force mode)"
)
@click.pass_context
def sync(ctx, force, days):
    """Manually trigger conversation sync."""
    config = ctx.obj["config"]

    click.echo("🔄 Starting manual sync...")

    async def run_sync():
        db = DatabaseManager(config.database_path, config.connection_pool_size)
        intercom_client = IntercomClient(
            config.intercom_token, config.api_timeout_seconds
        )
        sync_manager = SyncManager(db, intercom_client)
        sync_service = sync_manager.get_sync_service()

        try:
            if force:
                # Force sync specified days
                days_clamped = min(days, 30)  # Max 30 days
                click.echo(f"📅 Force syncing last {days_clamped} days...")
                now = datetime.now()
                start_date = now - timedelta(days=days_clamped)
                stats = await sync_service.sync_period(start_date, now)
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

            if stats.errors_encountered > 0:
                click.echo(f"   - ⚠️  {stats.errors_encountered} errors")

        except Exception as e:
            click.echo(f"❌ Sync failed: {e}")
            sys.exit(1)

    asyncio.run(run_sync())


@cli.command()
@click.pass_context
def logs(_ctx):
    """Show recent log entries."""
    log_file = Path.home() / ".fastintercom" / "logs" / "fastintercom.log"

    if not log_file.exists():
        click.echo("No log file found.")
        return

    # Show last 50 lines
    try:
        with open(log_file) as f:
            lines = f.readlines()
            for line in lines[-50:]:
                click.echo(line.rstrip())
    except Exception as e:
        click.echo(f"Error reading log file: {e}")


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
