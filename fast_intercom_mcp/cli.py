"""Command line interface for FastIntercom MCP server."""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import click

from .config import Config
from .database import DatabaseManager
from .intercom_client import IntercomClient
from .mcp_server import FastIntercomMCPServer

logger = logging.getLogger(__name__)

def setup_basic_logging(log_level="INFO"):
    """Setup basic logging."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )




@click.group()
@click.option("--config", "-c", help="Configuration file path")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.pass_context
def cli(ctx, config, verbose):
    """FastIntercom MCP Server - Local Intercom conversation access."""
    ctx.ensure_object(dict)

    # Setup logging
    log_level = "DEBUG" if verbose else "INFO"
    setup_basic_logging(log_level)

    # Load configuration
    try:
        ctx.obj["config"] = Config.load(config)
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

    click.echo("\n🎉 FastIntercom is ready to use!")
    click.echo("Next steps:")
    click.echo("  1. Run 'fastintercom start' to start the MCP server")
    click.echo("  2. Configure Claude Desktop to use this MCP server")
    click.echo("  3. Start asking questions about your Intercom conversations!")


@cli.command()
@click.pass_context
def start(ctx):
    """Start the FastIntercom MCP server in stdio mode."""
    config = ctx.obj["config"]

    click.echo("🚀 Starting FastIntercom MCP Server (stdio mode)...")

    # Initialize components
    db = DatabaseManager(config.database_path, config.connection_pool_size)
    intercom_client = IntercomClient(config.intercom_token, config.api_timeout_seconds)
    
    # Create basic MCP server (no complex sync service)
    server = FastIntercomMCPServer(db, None, intercom_client)

    async def run_server():
        # Test connection
        if not await intercom_client.test_connection():
            click.echo("❌ Failed to connect to Intercom API. Check your token.")
            return False

        click.echo("✅ Connected to Intercom API")
        click.echo("📡 MCP server listening for requests...")
        click.echo("   (Press Ctrl+C to stop)")

        try:
            await server.run()
        except KeyboardInterrupt:
            pass

        return True

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully without error message
        pass
    except Exception as e:
        click.echo(f"❌ Server error: {e}")
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
