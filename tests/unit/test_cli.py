"""Unit tests for CLI module."""

from click.testing import CliRunner

from fast_intercom_mcp.cli import cli


def test_cli_help():
    """Test CLI help command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "FastIntercom MCP Server" in result.output
    assert "Commands:" in result.output


def test_cli_version():
    """Test CLI version command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "fast-intercom-mcp" in result.output


def test_cli_run_missing_token():
    """Test CLI run command without token."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run"])
    # Should fail due to missing INTERCOM_TOKEN
    assert result.exit_code != 0


def test_cli_status_command():
    """Test CLI status command exists."""
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--help"])
    assert result.exit_code == 0
    assert "Show server status" in result.output or "status" in result.output
