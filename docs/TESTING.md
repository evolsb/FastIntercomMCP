# FastIntercom MCP Testing Guide

## Overview

This document provides comprehensive testing procedures for the FastIntercom MCP server. It serves as a complete guide for future agents and deployers to understand, run, and maintain the testing infrastructure.

The testing strategy covers multiple layers:
- Unit Tests (pytest)
- Integration Tests (real API)
- Docker Tests (clean install)
- Performance Tests (benchmarking)
- CI/CD Tests (automated)

## Test Path Consistency

### Critical Testing Requirement: Use Production Code Paths

**Important**: All local tests must use the same sync methods as CI/GitHub Actions to ensure consistency and catch bugs early.

#### Standard Sync Method
All tests should use `SyncService.sync_period()` method, which is the production sync method used in:
- GitHub Actions CI tests  
- comprehensive_sync_test.py
- Updated performance tests
- All production deployments

#### Deprecated Methods
❌ **Do NOT use these in tests**:
- `SyncManager` (deprecated)
- CLI subprocess calls for sync operations 
- Non-existent methods like `sync_conversations()`

#### Correct Test Pattern
```python
# ✅ CORRECT: Use direct SyncService calls (same as CI)
from fast_intercom_mcp.sync_service import SyncService
from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.intercom_client import IntercomClient

async def test_sync():
    db_manager = DatabaseManager(config)
    client = IntercomClient(token)
    sync_service = SyncService(db_manager, client)
    
    # Use same method as GitHub Actions
    stats = await sync_service.sync_period(start_date, end_date)
    return stats

# ❌ INCORRECT: Using CLI subprocess (different path)
result = subprocess.run(['fast-intercom-mcp', 'sync', '--days', '7'])
```

This ensures that local tests catch the same issues as CI tests and prevents inconsistencies between local and remote testing environments.

## Test Types

### 1. Unit Tests
**Purpose**: Test individual components in isolation  
**Framework**: pytest  
**Location**: `tests/` directory  
**Duration**: < 30 seconds  

```bash
# Run all unit tests
pytest tests/

# Run specific test file
pytest tests/test_sync_service.py

# Run with verbose output
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=fast_intercom_mcp
```

### 2. Integration Tests
**Purpose**: Test end-to-end functionality with real Intercom API  
**Framework**: Custom integration script with comprehensive testing  
**Location**: `scripts/run_integration_test.sh`  
**Duration**: 1-5 minutes  
**Prerequisites**: Valid Intercom API token  

```bash
# Quick integration test (1 day, 100 conversations)
./scripts/run_integration_test.sh --quick

# Standard integration test (7 days of data)
./scripts/run_integration_test.sh

# Extended integration test (30 days of data)
./scripts/run_integration_test.sh --days 30

# Performance benchmark test
./scripts/run_integration_test.sh --performance-report

# Debug test with verbose output
./scripts/run_integration_test.sh --verbose --no-cleanup
```

### 3. Docker Tests
**Purpose**: Test clean installation and deployment  
**Framework**: Comprehensive Docker testing script  
**Location**: `scripts/test_docker_install.sh`  
**Duration**: 2-10 minutes  

```bash
# Basic Docker functionality test
./scripts/test_docker_install.sh

# Full Docker test with API integration
./scripts/test_docker_install.sh --with-api-test

# Debug Docker issues with container preservation
./scripts/test_docker_install.sh --debug --keep-container

# Test with custom configuration
./scripts/test_docker_install.sh --config ./test-configs/docker-test.json
```

### 4. MCP Protocol Tests
**Purpose**: Test individual MCP tools and protocol compliance  
**Framework**: Python-based MCP tool tester  
**Location**: `scripts/test_mcp_tools.py`  
**Duration**: 30 seconds - 2 minutes  

```bash
# Test all MCP tools
python3 scripts/test_mcp_tools.py

# Test specific tool
python3 scripts/test_mcp_tools.py --tool search_conversations

# Verbose testing with results output
python3 scripts/test_mcp_tools.py --verbose --output mcp_results.json

# Test with custom timeout
python3 scripts/test_mcp_tools.py --timeout 60
```

## Running Tests

### Local Development

#### Prerequisites
- Python 3.11+
- Virtual environment activated
- Intercom API token (for integration tests)
- Docker (for Docker tests)

#### Quick Test Suite
```bash
# 1. Basic functionality check (30 seconds)
python3 -c "import fast_intercom_mcp; print('✅ Import successful')"

# 2. CLI availability check
fast-intercom-mcp --help

# 3. Run unit tests
pytest tests/ -x --tb=short

# 4. Quick integration test (requires API token)
export INTERCOM_ACCESS_TOKEN=your_token_here
./scripts/run_integration_test.sh --quick

# 5. Test MCP tools
python3 scripts/test_mcp_tools.py
```

#### Complete Test Suite
```bash
# 1. Full unit test suite with coverage
pytest tests/ --cov=fast_intercom_mcp --cov-report=html

# 2. Integration test with performance monitoring
./scripts/run_integration_test.sh --performance-report --output integration_results.json

# 3. Docker clean install test
./scripts/test_docker_install.sh --with-api-test

# 4. MCP protocol compliance test
python3 scripts/test_mcp_tools.py --verbose --output mcp_results.json
```

### CI/CD Pipeline

#### GitHub Actions Workflows

**Fast Check** (runs on every PR and push to main):
```bash
# Trigger manually
gh workflow run fast-check.yml

# View results
gh run list --workflow=fast-check.yml --limit=5

# Check latest run status
gh run view $(gh run list --workflow=fast-check.yml --limit=1 --json databaseId --jq '.[0].databaseId')
```

**Integration Test** (manual trigger with workflow_dispatch):
```bash
# Trigger integration test workflow from Issue #37
gh workflow run integration-test.yml

# Trigger with custom parameters
gh workflow run integration-test.yml -f sync_days=30 -f run_full_test=true

# View detailed logs
gh run view --log
```

Note: The integration test workflow was implemented in Issue #37 and runs comprehensive API testing with real data.

#### Expected CI/CD Results
- **Fast Check**: < 2 minutes, validates imports, linting, and CLI smoke test
- **Integration Test**: 5-30 minutes, validates real API functionality with 30+ days of data
- **Docker Test**: Can be triggered via local scripts for deployment validation

### Manual Verification

#### Local MCP Server Test
```bash
# 1. Start MCP server in test mode
fast-intercom-mcp start --test-mode &
SERVER_PID=$!

# 2. Test MCP tools
python3 scripts/test_mcp_tools.py --verbose

# 3. Test specific tools
python3 scripts/test_mcp_tools.py --tool search_conversations
python3 scripts/test_mcp_tools.py --tool get_server_status

# 4. Clean up
kill $SERVER_PID
```

#### Database Integrity Check
```bash
# Check database schema and data
python3 scripts/verify_database_integrity.py

# View database statistics
sqlite3 ~/.fast-intercom-mcp/data.db "
SELECT 
    COUNT(*) as conversations,
    (SELECT COUNT(*) FROM messages) as messages,
    (SELECT COUNT(*) FROM sync_periods) as sync_periods
FROM conversations;
"
```

## Test Configuration

### Environment Variables
```bash
# Required for integration tests
export INTERCOM_ACCESS_TOKEN=your_token_here

# Optional test configuration
export FASTINTERCOM_TEST_LOG_LEVEL=DEBUG
export FASTINTERCOM_TEST_TIMEOUT=300
export FASTINTERCOM_TEST_DATA_RETENTION=7

# Performance test configuration
export FASTINTERCOM_PERF_TARGET_CONV_PER_SEC=10
export FASTINTERCOM_PERF_TARGET_RESPONSE_MS=100
export FASTINTERCOM_PERF_TARGET_MEMORY_MB=100
```

### Test Configuration Files

#### `tests/config.json`
```json
{
  "unit_tests": {
    "timeout": 30,
    "log_level": "WARNING"
  },
  "integration_tests": {
    "timeout": 300,
    "default_days": 7,
    "max_conversations": 1000
  },
  "performance_tests": {
    "target_conv_per_sec": 10,
    "target_response_ms": 100,
    "target_memory_mb": 100
  }
}
```

## Success Criteria

### Unit Tests
- ✅ All tests pass
- ✅ Code coverage > 80%
- ✅ No deprecation warnings
- ✅ All imports resolve correctly

### Integration Tests
- ✅ Successful API connection
- ✅ Data sync completes without errors
- ✅ Synced data matches expected format
- ✅ Performance targets met (see benchmarks below)

### Docker Tests
- ✅ Docker image builds successfully
- ✅ Container starts and serves MCP protocol
- ✅ Basic functionality works in clean environment
- ✅ No missing dependencies or configuration issues

### Performance Benchmarks

#### Expected Performance Targets
- **Sync Speed**: 10-50 conversations/second
- **Response Time**: <100ms for cached queries
- **Memory Usage**: <100MB for server process
- **Storage Efficiency**: ~2KB per conversation average

#### Performance Test Results Format
```
✅ Sync Performance: 23.4 conv/sec (target: >10)
✅ Response Time: 47ms average (target: <100ms)
✅ Memory Usage: 73MB (target: <100MB)
✅ Storage: 1.8KB/conv average (target: ~2KB)
```

## Troubleshooting

### Common Issues

#### Test Import Failures
```bash
# Problem: ImportError when running tests
# Solution: Ensure package is installed in development mode
pip install -e .
python3 -c "import fast_intercom_mcp; print('✅ Import works')"
```

#### API Connection Failures
```bash
# Problem: Integration tests fail with connection errors
# Diagnosis:
curl -H "Authorization: Bearer $INTERCOM_ACCESS_TOKEN" \
     https://api.intercom.io/me

# Solutions:
# 1. Verify token is valid and has correct permissions
# 2. Check network connectivity
# 3. Verify token has 'conversations:read' permission
```

#### Database Locked Errors
```bash
# Problem: Database is locked during tests
# Solution: Ensure no other processes are using the database
ps aux | grep fast-intercom-mcp
pkill -f fast-intercom-mcp

# Clean up test databases
rm -f ~/.fast-intercom-mcp/test_*.db
```

#### Docker Test Failures
```bash
# Problem: Docker tests fail to build or run
# Diagnosis:
docker build -t fast-intercom-mcp-test .
docker run --rm fast-intercom-mcp-test --help

# Common solutions:
# 1. Ensure Docker is running
# 2. Check Dockerfile syntax
# 3. Verify base image availability
```

#### Performance Test Failures
```bash
# Problem: Performance tests fail to meet targets
# Diagnosis tools:
./scripts/profile_sync_performance.sh
./scripts/monitor_memory_usage.sh

# Common causes:
# 1. Large dataset (>10K conversations)
# 2. Slow network connection
# 3. Database not optimized
# 4. Insufficient system resources
```

### Debug Procedures

#### Enable Debug Logging
```bash
# For unit tests
export FASTINTERCOM_LOG_LEVEL=DEBUG
pytest tests/ -s

# For integration tests
export FASTINTERCOM_TEST_LOG_LEVEL=DEBUG
./scripts/run_integration_test.sh --verbose

# For Docker tests
./scripts/test_docker_install.sh --debug
```

#### Analyze Test Failures
```bash
# Generate detailed test report
pytest tests/ --tb=long --capture=no > test_report.txt

# View recent logs
tail -f ~/.fast-intercom-mcp/logs/fast-intercom-mcp.log

# Database debugging
sqlite3 ~/.fast-intercom-mcp/data.db .dump > db_dump.sql
```

#### Performance Debugging
```bash
# Profile sync performance
python3 -m cProfile -o sync_profile.prof scripts/profile_sync.py

# Analyze profile
python3 -c "
import pstats
p = pstats.Stats('sync_profile.prof')
p.sort_stats('cumulative').print_stats(20)
"

# Memory profiling
pip install memory-profiler
python3 -m memory_profiler scripts/profile_memory.py
```

## Continuous Integration

### Adding New Tests

#### Unit Test Example
```python
# tests/test_new_feature.py
import pytest
from fast_intercom_mcp import NewFeature

class TestNewFeature:
    def test_new_functionality(self):
        feature = NewFeature()
        result = feature.do_something()
        assert result is not None
        assert result.status == "success"
```

#### Integration Test Example
```python
# tests/test_integration_new.py
import pytest
from fast_intercom_mcp import SyncService

@pytest.mark.integration
async def test_new_integration_feature():
    service = SyncService()
    result = await service.new_feature_method()
    assert result.success is True
```

### Updating Performance Targets

When modifying performance targets:

1. Update `tests/config.json`
2. Update documentation in this file
3. Update CI/CD workflow files
4. Validate new targets with real data

### Test Data Management

#### Test Data Location
- Unit tests: Use mocked data in `tests/fixtures/`
- Integration tests: Use real API data (controlled scope)
- Performance tests: Use configurable dataset size

#### Test Data Cleanup
```bash
# Clean up test artifacts
./scripts/cleanup_test_data.sh

# Reset test environment
fast-intercom-mcp reset --test-mode
```

## Integration with Development Workflow

### Pre-commit Testing

Pre-commit hooks ensure code quality and catch issues before they reach CI.

#### Standard Pre-commit Script
Create `scripts/pre_commit_validation.sh`:
```bash
#!/bin/bash
set -e

echo "🔧 Running pre-commit validation..."

# Environment setup (detect project type)
if [ -f "pyproject.toml" ] && command -v poetry >/dev/null 2>&1; then
    echo "📦 Using Poetry environment"
    PYTHON_CMD="poetry run python"
    RUFF_CMD="poetry run ruff"
    PYTEST_CMD="poetry run pytest"
elif [ -f "venv/bin/activate" ]; then
    echo "📦 Using venv environment"
    source venv/bin/activate
    PYTHON_CMD="python"
    RUFF_CMD="ruff"
    PYTEST_CMD="pytest"
else
    echo "📦 Using system Python"
    PYTHON_CMD="python3"
    RUFF_CMD="ruff"
    PYTEST_CMD="python3 -m pytest"
fi

# 1. Import check
echo "1️⃣ Testing module import..."
$PYTHON_CMD -c "import fast_intercom_mcp; print('✅ Import successful')" || exit 1

# 2. Linting
echo "2️⃣ Running linting..."
$RUFF_CMD check . --exclude venv --exclude .venv || exit 1

# 3. Quick unit tests
echo "3️⃣ Running quick unit tests..."
$PYTEST_CMD tests/ -x --tb=short -q || exit 1

# 4. CLI smoke test
echo "4️⃣ Testing CLI functionality..."
$PYTHON_CMD -m fast_intercom_mcp --help >/dev/null || exit 1

echo "✅ All pre-commit checks passed!"
```

#### Installation
```bash
# Make script executable
chmod +x scripts/pre_commit_validation.sh

# Install as git pre-commit hook
ln -sf ../../scripts/pre_commit_validation.sh .git/hooks/pre-commit

# Test the hook
./scripts/pre_commit_validation.sh
```

#### Manual Pre-commit Check
```bash
# Run pre-commit validation manually
./scripts/pre_commit_validation.sh

# Run with verbose output
bash -x ./scripts/pre_commit_validation.sh
```

### Pull Request Testing
- All PRs must pass Fast Check workflow
- Integration tests run on manual trigger
- Performance regression tests on major changes

### Release Testing
- Complete test suite must pass
- Docker tests must pass
- Performance benchmarks must be met
- Documentation must be updated

## Future Test Expansion

### Planned Test Additions
- Load testing with multiple concurrent clients
- Network failure simulation and recovery
- Database corruption recovery testing
- Memory leak detection over extended runs

### Test Infrastructure Improvements
- Automated performance regression detection
- Test result trending and analysis
- Integration with monitoring systems
- Automated test data generation

## Quick Reference

### Most Common Test Commands
```bash
# Daily development testing
pytest tests/ -x --tb=short                    # Quick unit tests
./scripts/run_integration_test.sh --quick      # Quick integration
python3 scripts/test_mcp_tools.py              # MCP tools test

# Before PR submission
pytest tests/ --cov=fast_intercom_mcp         # Full unit tests
./scripts/run_integration_test.sh             # Full integration
./scripts/test_docker_install.sh              # Docker test

# Before release
./scripts/run_integration_test.sh --performance-report  # With metrics
./scripts/test_docker_install.sh --with-api-test        # Full Docker test
python3 scripts/test_mcp_tools.py --verbose --output mcp_results.json
```

### Emergency Test Commands
```bash
# Test if server is functional
fast-intercom-mcp status

# Test basic API connectivity
python3 -c "
import asyncio
from fast_intercom_mcp import IntercomClient, Config
async def test(): 
    client = IntercomClient(Config.load().intercom_token)
    print('✅ Connected' if await client.test_connection() else '❌ Failed')
asyncio.run(test())
"

# Test database integrity
sqlite3 ~/.fast-intercom-mcp/data.db "PRAGMA integrity_check;"
```

This testing guide ensures comprehensive validation of the FastIntercom MCP server across all deployment scenarios and use cases.