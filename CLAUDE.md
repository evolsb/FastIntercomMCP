# Claude Development Environment Guide - FastIntercom MCP

## Project Overview
This is the FastIntercom MCP server repository providing high-performance Model Context Protocol access to Intercom conversation data with intelligent caching and background synchronization.

## Directory Structure
- Main project: `/Users/chris-home/Developer/fast-intercom-mcp`
- Worktrees: `/Users/chris-home/Developer/fast-intercom-mcp-worktrees/`
- Documentation: `docs/` directory with comprehensive testing guides
- Scripts: `scripts/` directory with automated test and utility scripts

## ⚠️ CRITICAL WORKFLOW REQUIREMENTS ⚠️

**BEFORE STARTING ANY GITHUB ISSUE WORK:**

1. **ALWAYS create a worktree FIRST** - Do NOT work in main repository
2. **ALWAYS follow issue → worktree → branch → commit → PR → merge workflow**
3. **ALWAYS update issue task checkboxes when implementation is complete**
4. **ALWAYS create a PR immediately after implementation**

### MANDATORY Issue Workflow Steps:
```bash
# 1. Read issue and set status to in-progress
gh issue view {number}
gh issue edit {number} --remove-label "status-ready" --add-label "status-in-progress"

# 2. Create worktree BEFORE any coding
git worktree add ../fast-intercom-mcp-worktrees/issue-{number} -b {type}/issue-{number}-description

# 3. Work in the worktree (never in main repo)
cd ../fast-intercom-mcp-worktrees/issue-{number}

# 4. Set up environment (CRITICAL - required for testing)
ln -s ../../fast-intercom-mcp/.env .env              # Link to main .env file
ln -s ../../fast-intercom-mcp/CLAUDE.md CLAUDE.md    # Link to main CLAUDE.md (single source of truth)
poetry install                                        # Install dependencies
poetry run python -c "from fast_intercom_mcp import Config; print('✅ Environment ready')"

# 5. After implementation: commit, push, create PR
git add . && git commit -m "Fix #{number}: Brief description" 
git push -u origin {type}/issue-{number}-description
gh pr create --title "Fix #{number}: Brief description" --body "$(cat <<'EOF'
## Summary
Brief description of changes

## Related Issues
Closes #{number}

## Test Plan
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Performance targets met
EOF
)"

# 6. Update issue status and mark tasks complete
gh issue edit {number} --remove-label "status-in-progress" --add-label "status-awaiting-qc"
```

## Testing Commands

### Test Progression for Pagination Changes

**MANDATORY: Follow this exact order for testing pagination/sync changes:**

```bash
# Simple one-liner: sync data and then verify it (3-7 minutes total)
./scripts/run_integration_test.sh --days 1 --quick && poetry run python scripts/content_verification.py --database .test-workspace-*/data/data.db --days 1 --detailed

# OR step-by-step:
# 1. First: Run integration test to populate test data (2-5 minutes)
./scripts/run_integration_test.sh --days 1 --quick

# 2. Then: Run content verification to analyze the data (1-2 minutes)
poetry run python scripts/content_verification.py --database .test-workspace-*/data/data.db --days 1 --detailed

# 3. Optional: Run performance validation (3-5 minutes)
python3 scripts/quick_performance_test.py
```

### Integration Testing

**Quick Integration Test** (2-5 minutes):
```bash
# Test with last 7 days of data
export INTERCOM_ACCESS_TOKEN=your_token_here
./scripts/run_integration_test.sh

# Expected output:
# ✅ API Connection: Connected to workspace
# ✅ Sync: 1,247 conversations (7 days)
# ✅ Performance: 23.4 conv/sec, 47ms avg response
# ✅ Integration test PASSED
```

**Comprehensive Integration Test** (5-15 minutes):
```bash
# Full integration test with performance report
./scripts/run_integration_test.sh --performance-report

# Extended dataset test
./scripts/run_integration_test.sh --days 30

# Docker clean install test
./scripts/test_docker_install.sh --with-api-test
```

**GitHub Actions Integration**:
```bash
# Trigger integration test workflow
gh workflow run integration-test.yml

# Check workflow status
gh run list --workflow=integration-test.yml --limit=5

# View detailed logs
gh run view --log
```

### Unit Testing

**Quick Unit Tests** (30 seconds):
```bash
# Basic unit test run
pytest tests/ -x --tb=short

# With coverage report
pytest tests/ --cov=fast_intercom_mcp --cov-report=term-missing

# Specific test file
pytest tests/test_sync_service.py -v
```

**Code Quality Checks**:
```bash
# Linting (must pass for PR)
ruff check . --exclude venv

# Type checking
mypy fast_intercom_mcp/

# Import verification
python3 -c "import fast_intercom_mcp; print('✅ Import successful')"
```

### Performance Verification

**Expected Performance Targets**:
- **Sync Speed**: 10-50 conversations/second
- **Response Time**: <100ms for cached queries
- **Memory Usage**: <100MB for server process
- **Storage Efficiency**: ~2KB per conversation average

**Performance Commands**:
```bash
# Dedicated performance test
./scripts/run_performance_test.sh

# Performance monitoring during operations
./scripts/monitor_performance.sh &
# ... run tests or operations ...
pkill -f monitor_performance

# Memory usage monitoring
./scripts/monitor_memory_usage.sh
```

### Docker Testing

**Docker Functionality Test**:
```bash
# Basic Docker build and functionality
./scripts/test_docker_install.sh

# With API integration
./scripts/test_docker_install.sh --with-api-test

# Debug Docker issues
./scripts/test_docker_install.sh --debug --keep-container
```

### MCP Protocol Testing

**MCP Tools Testing**:
```bash
# Test all MCP tools
python3 scripts/test_mcp_tools.py

# Test specific tool
python3 scripts/test_mcp_tools.py --tool search_conversations

# Test with custom server
python3 scripts/test_mcp_tools.py --server-url http://localhost:3001
```

## Troubleshooting

### Common Issues and Quick Fixes

**Import Failures**:
```bash
# Ensure package is installed in development mode
pip install -e .
python3 -c "import fast_intercom_mcp; print('✅ Working')"
```

**API Connection Issues**:
```bash
# Verify API token
curl -H "Authorization: Bearer $INTERCOM_ACCESS_TOKEN" \
     https://api.intercom.io/me

# Check token permissions (must include 'conversations:read')
```

**Database Locked Errors**:
```bash
# Find and kill interfering processes
ps aux | grep fast-intercom-mcp
pkill -f fast-intercom-mcp

# Verify database integrity
sqlite3 ~/.fast-intercom-mcp/data.db "PRAGMA integrity_check;"
```

**Performance Issues**:
```bash
# Profile sync performance
./scripts/profile_sync_performance.sh

# Check database performance
./scripts/test_database_performance.sh

# Monitor system resources
./scripts/monitor_system_resources.sh
```

### Debug Mode

**Enable Debug Logging**:
```bash
# For unit tests
export FASTINTERCOM_LOG_LEVEL=DEBUG
pytest tests/ -s

# For integration tests
export FASTINTERCOM_TEST_LOG_LEVEL=DEBUG
./scripts/run_integration_test.sh --verbose

# For CLI operations
fast-intercom-mcp --verbose status
```

**Log Locations**:
- **Main logs**: `~/.fast-intercom-mcp/logs/fast-intercom-mcp.log`
- **Test logs**: `~/.fast-intercom-mcp-test/logs/`
- **CI logs**: GitHub Actions workflow logs

**Database Debugging**:
```bash
# View database statistics
sqlite3 ~/.fast-intercom-mcp/data.db "
SELECT 
    COUNT(*) as conversations,
    (SELECT COUNT(*) FROM messages) as messages,
    (SELECT COUNT(*) FROM sync_periods) as sync_periods
FROM conversations;
"

# Export database for analysis
sqlite3 ~/.fast-intercom-mcp/data.db .dump > db_dump.sql
```

## Script and Testing Logging Standards

### MANDATORY: All scripts must use proper logging infrastructure

**Required logging setup for all test scripts:**

```python
import logging
from fast_intercom_mcp.core.config import setup_logging

# Set up enhanced logging (console + files)
log_level = os.getenv("FASTINTERCOM_LOG_LEVEL", "INFO")
setup_logging(log_level)

logger = logging.getLogger(__name__)

# Examples of proper logging:
logger.info("Human-readable status message")         # Shows in console + files
logger.debug("Detailed machine-readable data")       # Only in files (unless DEBUG level)
logger.warning("Important notices for users")        # Shows in console + files
logger.error("Error conditions")                     # Shows in console + error.log
```

**Log file structure** (automatic):
- `main.log` - All levels (INFO, DEBUG, WARNING, ERROR)
- `sync.log` - Sync-specific operations
- `errors.log` - ERROR level only
- Console output - Based on log level setting

**Environment variables for logging:**
```bash
export FASTINTERCOM_LOG_LEVEL=DEBUG    # For detailed debugging
export FASTINTERCOM_LOG_LEVEL=INFO     # For normal operation (default)
export FASTINTERCOM_LOG_DIR=/path/to/logs  # Custom log directory
```

**Benefits:**
- ✅ **Console AND file logging** simultaneously
- ✅ **Human-readable INFO** for monitoring progress
- ✅ **Machine-readable DEBUG** for troubleshooting
- ✅ **Automatic log rotation** and management
- ✅ **Test workspace isolation** (logs go to test dirs)

## Development Workflow

### Pre-commit Testing Checklist
```bash
# 1. Unit tests pass
pytest tests/ -x --tb=short

# 2. Code quality checks pass
ruff check . --exclude venv
python3 -c "import fast_intercom_mcp"

# 3. Quick integration test (if API token available)
./scripts/run_integration_test.sh --quick

# 4. Performance check (for major changes)
./scripts/run_performance_test.sh --quick
```

### Pull Request Requirements
- All unit tests must pass
- Code quality checks must pass (ruff, mypy)
- Integration tests recommended for major changes
- Performance regression testing for optimization changes
- Documentation updated if adding new features

### Quality Control Process

**QC Labels and Workflow**:
- `status-awaiting-qc`: Implementation complete, needs QC review
- `status-qc-approved`: QC passed, ready for merge
- `status-qc-failed`: QC failed, needs rework

**Pre-merge Quick Checks** (run in worktree):
```bash
# Core functionality check (5 seconds)
python3 -c "import fast_intercom_mcp.cli"

# Linting check (10 seconds)
ruff check . --exclude venv

# CLI functionality check (2 seconds)
fast-intercom-mcp --help

# Optional: Quick test verification
python3 -m pytest tests/ -x --tb=no -q
```

**Post-merge Steps**:

After merging PRs and pulling changes to your main branch:

1. **Reinstall the package** (CRITICAL - or you'll run old code!):
```bash
# If using Poetry:
poetry install

# If using venv:
source venv/bin/activate
pip install -e .
```

2. **Run smoke tests** (every ~5 PRs):
```bash
# Quick functionality check
fast-intercom-mcp --help
fast-intercom-mcp status
python3 -c "from fast_intercom_mcp import cli"
```

⚠️ **Common Issue**: If you see old behavior after pulling changes, you forgot to reinstall!
The package must be reinstalled to pick up code changes from merged PRs.

**CRITICAL: After Making Code Changes**:

Before running any tests, you MUST reinstall the package or tests will use the old cached version:

```bash
# Poetry projects (recommended)
poetry install

# Virtual environment projects
source venv/bin/activate && pip install -e .

# Then run tests
./scripts/run_integration_test_detailed.sh --days 7
```

## Project Structure

```
fast-intercom-mcp/
├── fast_intercom_mcp/          # Main package
│   ├── cli.py                  # Command-line interface
│   ├── config.py               # Configuration management
│   ├── database.py             # SQLite database operations
│   ├── intercom_client.py      # Intercom API client
│   ├── mcp_server.py           # MCP server implementation
│   ├── models.py               # Data models
│   ├── sync_service.py         # Background sync service
│   ├── core/                   # Core utilities
│   ├── sync/                   # Sync strategies and coordination
│   └── transport/              # Network optimization
├── tests/                      # Test suite
│   ├── test_*.py               # Unit tests
│   └── integration/            # Integration tests
├── docs/                       # Documentation
│   ├── TESTING.md              # Complete testing guide
│   └── INTEGRATION_TESTING.md  # Integration test procedures
├── scripts/                    # Test and utility scripts
│   ├── run_integration_test.sh # Main integration test
│   ├── test_docker_install.sh  # Docker testing
│   ├── run_performance_test.sh # Performance benchmarking
│   └── README.md               # Script documentation
└── docker/                     # Docker configuration
```

## Environment Variables

### Required for Integration Tests
```bash
export INTERCOM_ACCESS_TOKEN=your_access_token_here
```

### Optional Test Configuration
```bash
export FASTINTERCOM_TEST_LOG_LEVEL=DEBUG
export FASTINTERCOM_TEST_TIMEOUT=300
export FASTINTERCOM_TEST_WORKSPACE=~/.fast-intercom-mcp-test
export FASTINTERCOM_CONFIG_DIR=~/.fast-intercom-mcp-test
```

### Performance Test Configuration
```bash
export FASTINTERCOM_PERF_TARGET_CONV_PER_SEC=10
export FASTINTERCOM_PERF_TARGET_RESPONSE_MS=100
export FASTINTERCOM_PERF_TARGET_MEMORY_MB=100
```

## Command Reference

### Most Common Commands

**Daily Development**:
```bash
pytest tests/ -x --tb=short                    # Quick unit tests
./scripts/run_integration_test.sh --quick      # Quick integration
```

**Before PR Submission**:
```bash
pytest tests/ --cov=fast_intercom_mcp         # Full unit tests
./scripts/run_integration_test.sh             # Full integration
ruff check . --exclude venv                   # Linting
```

**Before Release**:
```bash
./scripts/run_complete_test_suite.sh          # Everything
./scripts/test_docker_install.sh --with-api-test  # Docker validation
```

### Emergency Commands

**Test if server is functional**:
```bash
fast-intercom-mcp status
```

**Test basic API connectivity**:
```bash
python3 -c "
import asyncio
from fast_intercom_mcp import IntercomClient, Config
async def test(): 
    client = IntercomClient(Config.load().intercom_token)
    print('✅ Connected' if await client.test_connection() else '❌ Failed')
asyncio.run(test())
"
```

**Test database integrity**:
```bash
sqlite3 ~/.fast-intercom-mcp/data.db "PRAGMA integrity_check;"
```

### GitHub Integration

**Issue Management**:
```bash
# View issue details
gh issue view {number}

# Update issue status
gh issue edit {number} --add-label "status-in-progress"
gh issue edit {number} --remove-label "status-ready" --add-label "status-awaiting-qc"

# Close completed issues
gh issue close {number} --comment "Completed via PR #{pr_number}"
```

**Pull Request Management**:
```bash
# Create PR with proper formatting
gh pr create --title "Fix #{number}: Description" --body "$(cat <<'EOF'
## Summary
Brief description

## Related Issues
Closes #{number}

## Test Plan
- [x] Unit tests pass
- [x] Integration tests pass
- [x] Performance targets met
EOF
)"

# Merge PR after QC approval
gh pr merge {number} --merge --delete-branch
```

## Documentation

### Key Documentation Files
- [`docs/TESTING.md`](docs/TESTING.md) - Comprehensive testing guide
- [`docs/INTEGRATION_TESTING.md`](docs/INTEGRATION_TESTING.md) - Integration test details
- [`scripts/README.md`](scripts/README.md) - Test script documentation
- [`README.md`](README.md) - Project overview and quick start
- [`CONTRIBUTING.md`](CONTRIBUTING.md) - Development guidelines

### When to Update Documentation
- New features or API changes
- Changes to testing procedures
- Performance target modifications
- New configuration options
- Deployment process changes

This comprehensive guide ensures efficient development workflow and thorough testing of the FastIntercom MCP server.