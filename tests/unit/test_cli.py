"""Unit tests for CLI module."""

from click.testing import CliRunner

from fast_intercom_mcp.cli import cli


def test_cli_help():
    """Test CLI help command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Commands:" in result.output
    assert "run" in result.output or "serve" in result.output


def test_cli_run_help():
    """Test CLI run help command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "Run" in result.output or "run" in result.output


def test_cli_serve_missing_token():
    """Test CLI serve command without token."""
    runner = CliRunner()
    # Use isolated filesystem to avoid side effects
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["serve"])
        # Should fail due to missing INTERCOM_ACCESS_TOKEN
        assert result.exit_code != 0


def test_cli_sync_help():
    """Test CLI sync command help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["sync", "--help"])
    assert result.exit_code == 0
    assert "sync" in result.output.lower()
