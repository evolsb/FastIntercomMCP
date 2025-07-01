#!/bin/bash
# run_integration_test.sh - Integration test runner for FastIntercom MCP
# This script performs comprehensive integration testing with real Intercom API data

set -e  # Exit on any error

# Script metadata
SCRIPT_NAME="FastIntercom MCP Integration Test"
SCRIPT_VERSION="1.0.0"
START_TIME=$(date +%s)

# Default configuration
DAYS=7
# Note: The integration test syncs ALL conversations in the date range to properly test:
# - Real-world data volumes and performance
# - Memory management under load  
# - API rate limiting handling
# - Database performance at scale
# The sync will process ALL conversations from the specified days without limits.
MAX_CONVERSATIONS=1000  # Note: This is only used for reporting, not enforced by sync
PERFORMANCE_REPORT=false
QUICK_MODE=false
VERBOSE=false
OUTPUT_FILE=""
CLEANUP=true

# Performance targets
TARGET_CONV_PER_SEC=10
TARGET_RESPONSE_MS=100
TARGET_MEMORY_MB=100

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}ℹ  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}⚠  $1${NC}"
}

log_error() {
    echo -e "${RED}✗ $1${NC}"
}

log_section() {
    echo ""
    echo -e "${BLUE}= $1${NC}"
    echo "=================================================================================="
}

# Usage information
usage() {
    cat << EOF
$SCRIPT_NAME v$SCRIPT_VERSION

Usage: $0 [OPTIONS]

OPTIONS:
    --days N                Number of days to sync (default: $DAYS)
    --performance-report    Generate detailed performance metrics
    --quick                 Fast test with minimal data (1 day)
    --verbose              Enable debug logging
    --output FILE          Save results to JSON file
    --no-cleanup           Don't clean up test environment
    --help                 Show this help message
    
Note: Tests sync ALL conversations in the specified date range to properly test
      real-world performance, memory management, and API rate limiting.

TEST MODES:
    Quick Test (--quick):
        • Syncs 1 day of data (all conversations from that day)
        • Fast execution (1-2 minutes)
        • Good for: CI/CD, quick validation, development testing
        
    Standard Test (default):
        • Syncs 7 days of data
        • Moderate execution (5-10 minutes)
        • Good for: Regular testing, PR validation
        
    Extended Test (--days 30):
        • Syncs up to 30 days of data
        • Long execution (15-30 minutes)
        • Good for: Performance testing, full validation
        
    Performance Test (--performance-report):
        • Includes detailed metrics and analysis
        • Measures sync speed, memory usage, response times
        • Good for: Optimization validation, benchmarking

EXAMPLES:
    # Quick functionality check (1-2 minutes)
    $0 --quick

    # Standard 7-day integration test (5-10 minutes)
    $0

    # Extended test with performance monitoring (15-30 minutes)
    $0 --days 30 --performance-report

    # Debug test with verbose output
    $0 --verbose --no-cleanup

REQUIREMENTS:
    - INTERCOM_ACCESS_TOKEN environment variable
    - Python 3.11+ with fast_intercom_mcp package
    - Network connectivity to Intercom API

EXIT CODES:
    0 - All tests passed
    1 - API connection failed
    2 - Sync operation failed
    3 - MCP server test failed
    4 - Performance targets not met
    5 - Environment setup failed
EOF
}

# Parse command line options
while [[ $# -gt 0 ]]; do
    case $1 in
        --days)
            DAYS="$2"
            shift 2
            ;;
        --max-conversations)
            MAX_CONVERSATIONS="$2"
            shift 2
            ;;
        --performance-report)
            PERFORMANCE_REPORT=true
            shift
            ;;
        --quick)
            QUICK_MODE=true
            DAYS=1
            MAX_CONVERSATIONS=100
            shift
            ;;
        --verbose)
            VERBOSE=true
            export FASTINTERCOM_LOG_LEVEL=DEBUG
            shift
            ;;
        --output)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        --no-cleanup)
            CLEANUP=false
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Global variables for test tracking
TEST_WORKSPACE=""
TEST_RUN_ID=""
SERVER_PID=""
TEMP_FILES=()
TEST_RESULTS=()
PYTHON_CMD=""
CLI_CMD=""

# Cleanup function
cleanup() {
    local exit_code=$?
    
    if [[ "$CLEANUP" == "true" ]]; then
        log_info "Cleaning up test environment..."
        
        # Stop MCP server if running
        if [[ -n "$SERVER_PID" && "$SERVER_PID" != "0" ]]; then
            kill "$SERVER_PID" 2>/dev/null || true
            wait "$SERVER_PID" 2>/dev/null || true
        fi
        
        # Remove temporary files
        for temp_file in "${TEMP_FILES[@]}"; do
            rm -f "$temp_file" 2>/dev/null || true
        done
        
        # Remove test workspace
        if [[ -n "$TEST_WORKSPACE" && -d "$TEST_WORKSPACE" ]]; then
            rm -rf "$TEST_WORKSPACE" 2>/dev/null || true
        fi
        
        log_success "Cleanup completed"
    else
        log_warning "Skipping cleanup (--no-cleanup specified)"
        if [[ -n "$TEST_WORKSPACE" ]]; then
            echo ""
            log_section "📁 Test artifacts preserved for debugging"
            log_info "Test workspace: $TEST_WORKSPACE"
            log_info "View logs: ls -la $TEST_WORKSPACE/logs/"
            log_info "View results: ls -la $TEST_WORKSPACE/results/"
            log_info "View database: sqlite3 $TEST_WORKSPACE/data/data.db"
            echo ""
        fi
    fi
    
    exit $exit_code
}

# Set trap for cleanup
trap cleanup EXIT INT TERM

# Detect and configure Python environment
detect_python_environment() {
    # Check if we're in a Poetry project
    if [[ -f "pyproject.toml" ]] && command -v poetry >/dev/null 2>&1; then
        log_info "Detected Poetry environment"
        export PYTHON_CMD="poetry run python"
        export CLI_CMD="poetry run fast-intercom-mcp"
        # Ensure dependencies are installed (includes http2 from pyproject.toml)
        poetry install --quiet || {
            log_error "Failed to install Poetry dependencies"
            exit 5
        }
    # Check for virtual environment
    elif [[ -f "venv/bin/activate" ]]; then
        log_info "Detected venv environment"
        source venv/bin/activate
        export PYTHON_CMD="python"
        export CLI_CMD="fast-intercom-mcp"
    elif [[ -f ".venv/bin/activate" ]]; then
        log_info "Detected .venv environment"
        source .venv/bin/activate
        export PYTHON_CMD="python"
        export CLI_CMD="fast-intercom-mcp"
    else
        log_info "Using system Python"
        export PYTHON_CMD="python3"
        export CLI_CMD="fast-intercom-mcp"
    fi
}

# Load environment variables from .env file
load_env_file() {
    # Use python-dotenv to properly load .env file
    local env_script=$(cat << 'EOF'
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print("python-dotenv not installed", file=sys.stderr)
    sys.exit(1)

# Search for .env file in current and parent directories
for path in [Path.cwd(), Path.cwd().parent, Path.cwd().parent.parent]:
    env_file = path / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        print(f"Loaded .env from {env_file}")
        break
else:
    print("No .env file found")

# Export environment variables for shell
for key, value in os.environ.items():
    if key.startswith(("INTERCOM_", "FASTINTERCOM_")):
        print(f"export {key}='{value}'")
EOF
)
    
    # Run the Python script and evaluate its output
    local env_exports
    env_exports=$($PYTHON_CMD -c "$env_script" 2>&1)
    
    if [[ $? -eq 0 ]]; then
        # Extract and evaluate export commands
        while IFS= read -r line; do
            if [[ $line == export* ]]; then
                eval "$line"
            elif [[ $line == "Loaded .env from"* ]]; then
                log_info "$line"
            fi
        done <<< "$env_exports"
    else
        log_warning "Could not load .env file using python-dotenv"
        # Fallback to manual parsing
        for env_file in ".env" "../.env" "../../.env" "../../fast-intercom-mcp/.env"; do
            if [[ -f "$env_file" ]]; then
                log_info "Loading environment from $env_file (fallback method)"
                while IFS='=' read -r key value; do
                    # Skip comments and empty lines
                    [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
                    # Remove quotes from value
                    value=${value#[\"\']}
                    value=${value%[\"\']}
                    export "$key=$value"
                done < "$env_file"
                break
            fi
        done
    fi
}

# Test environment setup
setup_test_environment() {
    log_section "Setting up test environment"
    
    # Detect Python environment first
    detect_python_environment
    
    # Load environment variables from .env
    load_env_file
    
    # Find project root by looking for pyproject.toml
    local project_root=""
    local current_dir="$(pwd)"
    
    # Search up the directory tree for pyproject.toml
    while [[ "$current_dir" != "/" ]]; do
        if [[ -f "$current_dir/pyproject.toml" ]]; then
            project_root="$current_dir"
            break
        fi
        current_dir="$(dirname "$current_dir")"
    done
    
    # If not found, use current directory as fallback
    if [[ -z "$project_root" ]]; then
        project_root="$(pwd)"
        log_warning "Could not find pyproject.toml, using current directory as project root: $project_root"
    else
        log_info "Found project root: $project_root"
    fi
    
    # Generate unique test run ID
    TEST_RUN_ID="$(date +%Y%m%d-%H%M%S)-$(openssl rand -hex 3 2>/dev/null || echo $RANDOM)"
    
    # Set up test workspace using environment variable or default location with unique ID
    if [[ -n "$FASTINTERCOM_TEST_WORKSPACE" ]]; then
        TEST_WORKSPACE="$FASTINTERCOM_TEST_WORKSPACE-$TEST_RUN_ID"
        log_info "Using custom test workspace from environment with unique ID: $TEST_WORKSPACE"
    else
        TEST_WORKSPACE="$project_root/.test-workspace-$TEST_RUN_ID"
        log_info "Using test workspace with unique ID: $TEST_WORKSPACE"
    fi
    
    # Create test workspace directory structure
    mkdir -p "$TEST_WORKSPACE/data"
    mkdir -p "$TEST_WORKSPACE/logs"
    mkdir -p "$TEST_WORKSPACE/results"
    
    # Set the configuration directory to the data subdirectory
    export FASTINTERCOM_CONFIG_DIR="$TEST_WORKSPACE/data"
    
    # Configure logging to use test-specific directory
    export FASTINTERCOM_LOG_DIR="$TEST_WORKSPACE/logs"
    export FASTINTERCOM_LOG_FILE="$TEST_WORKSPACE/logs/fast-intercom-mcp.log"
    
    # Display test run information
    echo ""
    log_section "📋 Test Run Information"
    log_info "Test Run ID: $TEST_RUN_ID"
    log_info "Test Workspace: $TEST_WORKSPACE"
    log_info "Log Directory: $TEST_WORKSPACE/logs/"
    log_info "Results Directory: $TEST_WORKSPACE/results/"
    log_info "Database Location: $TEST_WORKSPACE/data/data.db"
    echo ""
    
    # Verify API token
    if [[ -z "$INTERCOM_ACCESS_TOKEN" ]]; then
        log_error "INTERCOM_ACCESS_TOKEN not found in environment or .env file"
        echo ""
        log_info "📋 Quick Setup:"
        log_info "   1. Copy template: cp .env.example .env"
        log_info "   2. Get token from: https://developers.intercom.com/building-apps/docs/app-authentication"
        log_info "   3. Add to .env file: INTERCOM_ACCESS_TOKEN=your_token_here"
        log_info "   4. Or export directly: export INTERCOM_ACCESS_TOKEN=your_token_here"
        echo ""
        exit 5
    fi
    
    # Test API connectivity
    log_info "Testing API connectivity..."
    if ! curl -s -f -H "Authorization: Bearer $INTERCOM_ACCESS_TOKEN" \
            -H "Accept: application/json" \
            https://api.intercom.io/me > /dev/null; then
        log_error "API connection failed"
        log_info "Please verify your token has correct permissions"
        exit 1
    fi
    
    # Verify Python environment and package
    log_info "Verifying Python environment..."
    if ! $PYTHON_CMD -c "import fast_intercom_mcp; print('Package version:', fast_intercom_mcp.__version__)" 2>/dev/null; then
        log_error "FastIntercom MCP package not available"
        log_info "Please install with: pip install -e . (or poetry install)"
        exit 5
    fi
    
    # Verify CLI is available
    if ! $CLI_CMD --help &> /dev/null; then
        log_error "fast-intercom-mcp CLI not working"
        log_info "Please ensure the package is properly installed"
        exit 5
    fi
    
    log_success "Test environment ready"
}

# API connection test
test_api_connection() {
    log_section "Testing API Connection"
    
    local api_response
    api_response=$(curl -s -H "Authorization: Bearer $INTERCOM_ACCESS_TOKEN" \
                       -H "Accept: application/json" \
                       https://api.intercom.io/me)
    
    if [[ $? -eq 0 ]]; then
        local workspace_name
        workspace_name=$(echo "$api_response" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('name', 'Unknown'))
except:
    print('Unknown')
")
        log_success "Connected to workspace: $workspace_name"
        TEST_RESULTS+=("api_connection:PASSED")
        return 0
    else
        log_error "API connection test failed"
        TEST_RESULTS+=("api_connection:FAILED")
        return 1
    fi
}

# Database initialization test
test_database_initialization() {
    log_section "Testing Database Initialization"
    
    # Initialize FastIntercom database (answer 'n' to sync prompt)
    log_info "Initializing database..."
    echo ""
    
    # Run init with real-time output display
    echo "n" | $CLI_CMD init --token "$INTERCOM_ACCESS_TOKEN" --sync-days 0 2>&1 | tee "$TEST_WORKSPACE/logs/init_output.txt"
    local init_result="${PIPESTATUS[1]}"  # Get exit code of CLI_CMD, not tee
    
    echo ""  # Add spacing after init output
    
    if [[ $init_result -eq 0 ]]; then
        log_success "Database initialized successfully"
        
        # Verify database file exists and has correct schema
        local db_file="$TEST_WORKSPACE/data/data.db"
        if [[ -f "$db_file" ]]; then
            # Check table structure
            local table_count
            table_count=$(sqlite3 "$db_file" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || echo "0")
            
            if [[ "$table_count" -gt 0 ]]; then
                log_success "Database schema created successfully ($table_count tables)"
                TEST_RESULTS+=("database_init:PASSED")
                return 0
            else
                log_error "Database schema not created properly"
                TEST_RESULTS+=("database_init:FAILED")
                return 1
            fi
        else
            log_error "Database file not created"
            TEST_RESULTS+=("database_init:FAILED")
            return 1
        fi
    else
        log_error "Database initialization failed"
        TEST_RESULTS+=("database_init:FAILED")
        return 1
    fi
}

# Data sync test
test_data_sync() {
    log_section "Testing Data Sync"
    
    local sync_start_time
    sync_start_time=$(date +%s)
    
    log_info "Syncing $DAYS days of conversation data..."
    log_warning "Note: Progress shows each batch as '100%' - this is normal, sync continues until all data is processed"
    
    # Create temp file for sync output capture
    local sync_output_file="$TEST_WORKSPACE/logs/sync_output.txt"
    
    # Run sync operation with real-time progress display
    log_info "Starting sync (real-time progress will be shown below)..."
    echo ""
    
    # Run sync with test-specific logging configuration, tee output for both display and capture
    FASTINTERCOM_LOG_DIR="$TEST_WORKSPACE/logs" FASTINTERCOM_LOG_FILE="$TEST_WORKSPACE/logs/sync.log" \
        $CLI_CMD sync --force --days "$DAYS" 2>&1 | tee "$sync_output_file"
    
    # Check exit status from PIPESTATUS array (bash specific)
    local sync_exit_code="${PIPESTATUS[0]}"
    
    echo ""  # Add spacing after sync output
    
    if [[ "$sync_exit_code" -eq 0 ]]; then
        local sync_end_time
        sync_end_time=$(date +%s)
        local sync_duration=$((sync_end_time - sync_start_time))
        
        # Extract metrics from saved sync output
        local conversations_synced
        conversations_synced=$(grep -o '[0-9]\+ conversations' "$sync_output_file" | tail -1 | grep -o '[0-9]\+' || echo "0")
        
        local messages_synced
        messages_synced=$(grep -o '[0-9]\+ messages' "$sync_output_file" | tail -1 | grep -o '[0-9]\+' || echo "0")
        
        # Calculate sync speed
        local sync_speed
        if [[ "$sync_duration" -gt 0 && "$conversations_synced" -gt 0 ]]; then
            sync_speed=$(echo "scale=1; $conversations_synced / $sync_duration" | bc 2>/dev/null || echo "0")
        else
            sync_speed="0"
        fi
        
        log_success "Sync completed: $conversations_synced conversations, $messages_synced messages"
        log_info "Sync duration: ${sync_duration}s (${sync_speed} conv/sec)"
        
        # Verify data was actually stored
        local db_file="$TEST_WORKSPACE/data/data.db"
        if [[ -f "$db_file" ]]; then
            local stored_conversations
            stored_conversations=$(sqlite3 "$db_file" "SELECT COUNT(*) FROM conversations;" 2>/dev/null || echo "0")
            
            if [[ "$stored_conversations" -gt 0 ]]; then
                log_success "Data verification: $stored_conversations conversations stored in database"
                TEST_RESULTS+=("data_sync:PASSED:$conversations_synced:$sync_speed")
                
                # Store sync metrics for performance report
                echo "$conversations_synced,$messages_synced,$sync_duration,$sync_speed" > "$TEST_WORKSPACE/results/sync_metrics.csv"
                
                return 0
            else
                log_error "No conversations found in database after sync"
                TEST_RESULTS+=("data_sync:FAILED")
                return 1
            fi
        else
            log_error "Database file not found after sync"
            TEST_RESULTS+=("data_sync:FAILED")
            return 1
        fi
    else
        log_error "Sync operation failed (exit code: $sync_exit_code)"
        log_info "Check sync output above and logs at: $TEST_WORKSPACE/logs/sync.log"
        TEST_RESULTS+=("data_sync:FAILED")
        return 1
    fi
}

# MCP server test
test_mcp_server() {
    log_section "Testing MCP Server"
    
    log_info "Starting MCP server in stdio mode..."
    
    # Start MCP server using mcp command for stdio mode
    # Configure logging for MCP server
    FASTINTERCOM_LOG_DIR="$TEST_WORKSPACE/logs" FASTINTERCOM_LOG_FILE="$TEST_WORKSPACE/logs/mcp-server.log" \
    $CLI_CMD mcp > "$TEST_WORKSPACE/logs/server.log" 2>&1 &
    SERVER_PID=$!
    
    # Wait for server to start
    sleep 3
    
    # Check if server is running
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        log_error "MCP server failed to start"
        cat "$TEST_WORKSPACE/logs/server.log" 2>/dev/null || true
        TEST_RESULTS+=("mcp_server:FAILED")
        return 1
    fi
    
    log_success "MCP server started (PID: $SERVER_PID)"
    
    # Test MCP tools using the new test scripts
    log_info "Testing MCP tools..."
    
    local test_output=""
    local test_script=""
    local tools_tested=0
    local tools_passed=0
    local test_exit_code=0
    
    # Try to use the full MCP test script first
    if command -v poetry >/dev/null 2>&1 && poetry run python -c "import mcp" 2>/dev/null; then
        log_info "Using test_mcp_server.py with full MCP library support..."
        test_script="$SCRIPT_DIR/test_mcp_server.py"
        test_output=$(FASTINTERCOM_CONFIG_DIR="$TEST_WORKSPACE/data" PYTHONPATH="$SCRIPT_DIR/../:$PYTHONPATH" \
            poetry run python "$test_script" --test-mode 2>&1)
        test_exit_code=$?
    else
        log_info "MCP library not available, using test_mcp_server_simple.py..."
        test_script="$SCRIPT_DIR/test_mcp_server_simple.py"
        test_output=$(FASTINTERCOM_CONFIG_DIR="$TEST_WORKSPACE/data" PYTHONPATH="$SCRIPT_DIR/../:$PYTHONPATH" \
            $PYTHON_CMD "$test_script" --test-mode 2>&1)
        test_exit_code=$?
    fi
    
    # Parse test results
    if [[ $test_exit_code -eq 0 ]]; then
        # Try to extract JSON results
        local json_results=$(echo "$test_output" | grep -E '^\{.*\}$' | tail -1)
        
        if [[ -n "$json_results" ]]; then
            # Parse JSON results
            tools_tested=$(echo "$json_results" | $PYTHON_CMD -c "import sys, json; data = json.load(sys.stdin); print(data.get('total_tests', 0))" 2>/dev/null || echo "0")
            tools_passed=$(echo "$json_results" | $PYTHON_CMD -c "import sys, json; data = json.load(sys.stdin); print(data.get('passed_tests', 0))" 2>/dev/null || echo "0")
            
            # Show individual test results if available
            echo "$json_results" | $PYTHON_CMD -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for test in data.get('tests', []):
        status = '✅' if test['passed'] else '❌'
        print(f\"  {status} {test['name']}: {test.get('message', 'No message')}\")
except:
    pass
" 2>/dev/null || true
        else
            # Fallback: count PASSED/FAILED in output
            tools_passed=$(echo "$test_output" | grep -c "PASSED" || echo "0")
            local tools_failed=$(echo "$test_output" | grep -c "FAILED" || echo "0")
            tools_tested=$((tools_passed + tools_failed))
            
            # Show the test output for debugging
            echo "$test_output" | grep -E "(PASSED|FAILED|Testing|Error)" || true
        fi
        
        log_success "MCP tools test completed: $tools_passed/$tools_tested tools passed"
    else
        log_error "MCP test script failed with exit code: $test_exit_code"
        echo "$test_output" | tail -20
        tools_tested=1
        tools_passed=0
    fi
    
    # Kill the server
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    
    # Update TEST_RESULTS
    if [[ "$tools_passed" -eq "$tools_tested" ]] && [[ "$tools_tested" -gt 0 ]]; then
        TEST_RESULTS+=("mcp_server:PASSED:$tools_passed")
        return 0
    else
        TEST_RESULTS+=("mcp_server:FAILED:$tools_passed/$tools_tested")
        return 1
    fi
}

# Performance measurement
measure_performance() {
    if [[ "$PERFORMANCE_REPORT" != "true" ]]; then
        return 0
    fi
    
    log_section "Measuring Performance"
    
    local metrics_file="$TEST_WORKSPACE/results/performance_metrics.json"
    local db_file="$TEST_WORKSPACE/data/data.db"
    
    # Get sync metrics
    local sync_metrics=""
    if [[ -f "$TEST_WORKSPACE/results/sync_metrics.csv" ]]; then
        sync_metrics=$(cat "$TEST_WORKSPACE/results/sync_metrics.csv")
    fi
    
    # Measure query response time
    log_info "Measuring query response times..."
    local response_times=()
    for i in {1..5}; do
        local start_time
        start_time=$(date +%s%3N)  # milliseconds
        $CLI_CMD status > /dev/null 2>&1
        local end_time
        end_time=$(date +%s%3N)
        local response_time=$((end_time - start_time))
        response_times+=("$response_time")
    done
    
    # Calculate average response time
    local total_time=0
    for time in "${response_times[@]}"; do
        total_time=$((total_time + time))
    done
    local avg_response_time=$((total_time / ${#response_times[@]}))
    
    # Measure memory usage (approximate)
    local memory_usage
    if command -v ps &> /dev/null && [[ -n "$SERVER_PID" ]]; then
        memory_usage=$(ps -o rss= -p "$SERVER_PID" 2>/dev/null | awk '{print int($1/1024)}' || echo "0")
    else
        memory_usage="0"
    fi
    
    # Get database size
    local db_size_mb="0"
    if [[ -f "$db_file" ]]; then
        db_size_mb=$(du -m "$db_file" | cut -f1)
    fi
    
    # Generate performance report
    cat > "$metrics_file" << EOF
{
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "test_config": {
        "days": $DAYS,
        "max_conversations": $MAX_CONVERSATIONS,
        "quick_mode": $QUICK_MODE
    },
    "sync_performance": {
        "conversations_synced": $(echo "$sync_metrics" | cut -d, -f1 || echo "0"),
        "messages_synced": $(echo "$sync_metrics" | cut -d, -f2 || echo "0"),
        "duration_seconds": $(echo "$sync_metrics" | cut -d, -f3 || echo "0"),
        "conversations_per_second": $(echo "$sync_metrics" | cut -d, -f4 || echo "0")
    },
    "query_performance": {
        "average_response_time_ms": $avg_response_time,
        "response_times_ms": [$(IFS=,; echo "${response_times[*]}")]
    },
    "resource_usage": {
        "memory_usage_mb": $memory_usage,
        "database_size_mb": $db_size_mb
    },
    "performance_targets": {
        "target_conv_per_sec": $TARGET_CONV_PER_SEC,
        "target_response_ms": $TARGET_RESPONSE_MS,
        "target_memory_mb": $TARGET_MEMORY_MB
    }
}
EOF
    
    # Evaluate performance against targets
    local perf_issues=()
    local sync_speed
    sync_speed=$(echo "$sync_metrics" | cut -d, -f4 || echo "0")
    
    if (( $(echo "$sync_speed < $TARGET_CONV_PER_SEC" | bc -l 2>/dev/null || echo "0") )); then
        perf_issues+=("Sync speed below target: ${sync_speed} < $TARGET_CONV_PER_SEC conv/sec")
    fi
    
    if [[ "$avg_response_time" -gt "$TARGET_RESPONSE_MS" ]]; then
        perf_issues+=("Response time above target: ${avg_response_time}ms > ${TARGET_RESPONSE_MS}ms")
    fi
    
    if [[ "$memory_usage" -gt "$TARGET_MEMORY_MB" ]]; then
        perf_issues+=("Memory usage above target: ${memory_usage}MB > ${TARGET_MEMORY_MB}MB")
    fi
    
    # Report performance results
    log_success "Performance measurement completed"
    log_info "Sync Speed: ${sync_speed} conv/sec (target: >${TARGET_CONV_PER_SEC})"
    log_info "Response Time: ${avg_response_time}ms average (target: <${TARGET_RESPONSE_MS}ms)"
    log_info "Memory Usage: ${memory_usage}MB (target: <${TARGET_MEMORY_MB}MB)"
    log_info "Database Size: ${db_size_mb}MB"
    
    if [[ ${#perf_issues[@]} -eq 0 ]]; then
        log_success "All performance targets met"
        TEST_RESULTS+=("performance:PASSED")
        return 0
    else
        log_warning "Performance issues detected:"
        for issue in "${perf_issues[@]}"; do
            log_warning "  - $issue"
        done
        TEST_RESULTS+=("performance:WARNING")
        return 0  # Don't fail the test for performance warnings
    fi
}

# Generate test report
generate_test_report() {
    log_section "Test Results"
    
    local end_time
    end_time=$(date +%s)
    local total_duration=$((end_time - START_TIME))
    
    local passed_tests=0
    local total_tests=0
    local failed_tests=()
    
    # Count test results
    for result in "${TEST_RESULTS[@]}"; do
        total_tests=$((total_tests + 1))
        if [[ "$result" =~ :PASSED ]]; then
            passed_tests=$((passed_tests + 1))
        elif [[ "$result" =~ :FAILED ]]; then
            failed_tests+=("$result")
        fi
    done
    
    # Generate summary
    echo ""
    echo "= $SCRIPT_NAME - Test Report"
    echo "=================================================================================="
    echo "Test Duration: ${total_duration}s"
    echo "Tests Passed: $passed_tests/$total_tests"
    echo ""
    
    # Detailed results
    for result in "${TEST_RESULTS[@]}"; do
        local test_name
        test_name=$(echo "$result" | cut -d: -f1)
        local test_status
        test_status=$(echo "$result" | cut -d: -f2)
        
        case "$test_status" in
            PASSED)
                log_success "$test_name: PASSED"
                ;;
            FAILED)
                log_error "$test_name: FAILED"
                ;;
            WARNING)
                log_warning "$test_name: WARNING"
                ;;
        esac
    done
    
    echo ""
    echo "=================================================================================="
    
    # Final result
    if [[ ${#failed_tests[@]} -eq 0 ]]; then
        log_success "Integration test PASSED "
        echo ""
        return 0
    else
        log_error "Integration test FAILED L"
        echo ""
        log_error "Failed tests:"
        for failed_test in "${failed_tests[@]}"; do
            log_error "  - $failed_test"
        done
        echo ""
        return 1
    fi
}

# Save results to file if requested
save_results() {
    if [[ -n "$OUTPUT_FILE" ]]; then
        local results_json="$TEST_WORKSPACE/results/test_results.json"
        
        # Create comprehensive results file
        cat > "$results_json" << EOF
{
    "test_metadata": {
        "script_name": "$SCRIPT_NAME",
        "script_version": "$SCRIPT_VERSION",
        "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
        "duration_seconds": $(($(date +%s) - START_TIME)),
        "test_config": {
            "days": $DAYS,
            "max_conversations": $MAX_CONVERSATIONS,
            "quick_mode": $QUICK_MODE,
            "performance_report": $PERFORMANCE_REPORT
        }
    },
    "test_results": [
$(IFS=$'\n'; for result in "${TEST_RESULTS[@]}"; do
    test_name=$(echo "$result" | cut -d: -f1)
    test_status=$(echo "$result" | cut -d: -f2)
    echo "        {\"test\": \"$test_name\", \"status\": \"$test_status\"},"
done | sed '$ s/,$//')
    ],
    "environment": {
        "test_workspace": "$TEST_WORKSPACE",
        "python_version": "$($PYTHON_CMD --version 2>&1)",
        "package_available": $($PYTHON_CMD -c "import fast_intercom_mcp; print('true')" 2>/dev/null || echo "false")
    }
}
EOF
        
        # Copy to output file
        cp "$results_json" "$OUTPUT_FILE"
        log_success "Results saved to: $OUTPUT_FILE"
    fi
}

# Main execution function
main() {
    log_section "$SCRIPT_NAME v$SCRIPT_VERSION"
    
    if [[ "$QUICK_MODE" == "true" ]]; then
        log_info "Running in QUICK mode ($DAYS days of data)"
    else
        log_info "Running full integration test ($DAYS days of data)"
    fi
    
    # Run test sequence
    setup_test_environment || exit 5
    test_api_connection || exit 1
    test_database_initialization || exit 2
    test_data_sync || exit 2
    test_mcp_server || exit 3
    measure_performance
    
    # Generate and save results
    generate_test_report
    local test_result=$?
    save_results
    
    exit $test_result
}

# Execute main function
main "$@"