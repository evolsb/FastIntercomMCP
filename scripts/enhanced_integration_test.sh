#!/bin/bash
# enhanced_integration_test.sh - Enhanced integration test with real-time monitoring
# This version integrates with the new progress broadcasting and status tracking systems

set -e  # Exit on any error

# Script metadata
SCRIPT_NAME="FastIntercom MCP Enhanced Integration Test"
SCRIPT_VERSION="2.0.0"
START_TIME=$(date +%s)

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default configuration
DAYS=7
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

# Generate unique test run ID for tracking
TEST_RUN_ID="test-$(date +%Y%m%d-%H%M%S)-$(openssl rand -hex 3 2>/dev/null || echo $RANDOM)"

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
    echo -e "${BLUE}= $1${NC}"
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
    
ENHANCED FEATURES:
    • Real-time progress broadcasting
    • Centralized status tracking
    • Enhanced CLI monitoring integration
    • Better background process visibility

MONITORING COMMANDS (run in another terminal):
    fast-intercom-mcp monitor status      # Live status dashboard
    fast-intercom-mcp logs show --follow  # Follow logs in real-time
    fast-intercom-mcp debug health        # Check system health

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
        --performance-report)
            PERFORMANCE_REPORT=true
            shift
            ;;
        --quick)
            QUICK_MODE=true
            DAYS=1
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
        
        # Complete process tracking
        if command -v $PYTHON_CMD >/dev/null 2>&1; then
            $PYTHON_CMD -c "
try:
    from fast_intercom_mcp.core.status_tracker import complete_process_tracking
    complete_process_tracking('$TEST_RUN_ID', 'completed' if $exit_code == 0 else 'failed', exit_code=$exit_code)
except Exception as e:
    print(f'Warning: Could not update process tracking: {e}')
" 2>/dev/null || true
        fi
        
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
            log_info "Monitor with: fast-intercom-mcp monitor status"
            log_info "View logs: fast-intercom-mcp logs show --follow"
            log_info "Check health: fast-intercom-mcp debug health"
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
        # Ensure dependencies are installed
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
    fi
}

# Enhanced test environment setup with progress tracking
setup_test_environment() {
    log_section "Setting up enhanced test environment"
    
    # Detect Python environment first
    detect_python_environment
    
    # Load environment variables from .env
    load_env_file
    
    # Generate test workspace with unique ID
    TEST_WORKSPACE="$(pwd)/.test-workspace-$TEST_RUN_ID"
    
    # Create test workspace directory structure
    mkdir -p "$TEST_WORKSPACE/data"
    mkdir -p "$TEST_WORKSPACE/logs"
    mkdir -p "$TEST_WORKSPACE/results"
    
    # Set the configuration directory to the data subdirectory
    export FASTINTERCOM_CONFIG_DIR="$TEST_WORKSPACE/data"
    
    # Configure enhanced logging and progress tracking
    export FASTINTERCOM_LOG_DIR="$TEST_WORKSPACE/logs"
    export FASTINTERCOM_PROGRESS_ENABLED=true
    export FASTINTERCOM_SESSION_ID="$TEST_RUN_ID"
    
    # Start process tracking
    $PYTHON_CMD -c "
try:
    from fast_intercom_mcp.core.status_tracker import start_process_tracking
    start_process_tracking(
        '$TEST_RUN_ID', 
        'integration_test', 
        'Integration test with $DAYS days of data',
        workspace='$TEST_WORKSPACE',
        days=$DAYS,
        quick_mode=$QUICK_MODE,
        performance_report=$PERFORMANCE_REPORT
    )
    print('✓ Process tracking started')
except Exception as e:
    print(f'Warning: Could not start process tracking: {e}')
" || log_warning "Process tracking unavailable"
    
    # Display enhanced test run information
    echo ""
    log_section "📋 Enhanced Test Run Information"
    log_info "Test Run ID: $TEST_RUN_ID"
    log_info "Test Workspace: $TEST_WORKSPACE"
    log_info "Enhanced Monitoring: ✓ Progress broadcasting enabled"
    log_info "Status Tracking: ✓ Centralized status tracking enabled"
    log_info ""
    log_info "💡 Monitor this test in real-time:"
    log_info "   fast-intercom-mcp monitor status"
    log_info "   fast-intercom-mcp logs show --follow"
    echo ""
    
    # Verify API token
    if [[ -z "$INTERCOM_ACCESS_TOKEN" ]]; then
        log_error "INTERCOM_ACCESS_TOKEN not found in environment or .env file"
        exit 5
    fi
    
    # Test API connectivity
    log_info "Testing API connectivity..."
    if ! curl -s -f -H "Authorization: Bearer $INTERCOM_ACCESS_TOKEN" \
            -H "Accept: application/json" \
            https://api.intercom.io/me > /dev/null; then
        log_error "API connection failed"
        exit 1
    fi
    
    # Verify Python environment and package
    log_info "Verifying Python environment..."
    if ! $PYTHON_CMD -c "import fast_intercom_mcp; print('✓ Package available')" 2>/dev/null; then
        log_error "FastIntercom MCP package not available"
        exit 5
    fi
    
    log_success "Enhanced test environment ready"
}

# Enhanced data sync test with progress broadcasting
test_data_sync() {
    log_section "Testing Data Sync with Enhanced Monitoring"
    
    # Update process tracking
    $PYTHON_CMD -c "
try:
    from fast_intercom_mcp.core.status_tracker import update_process_tracking
    update_process_tracking(
        '$TEST_RUN_ID',
        progress={'phase': 'data_sync', 'status': 'starting'},
        log_files=['logs/sync_output.txt', 'logs/main.log']
    )
except: pass
" 2>/dev/null || true
    
    local sync_start_time
    sync_start_time=$(date +%s)
    
    log_info "Starting enhanced sync with real-time progress..."
    log_info "💡 Monitor progress: fast-intercom-mcp monitor status"
    
    # Run sync with enhanced logging and progress broadcasting
    FASTINTERCOM_LOG_DIR="$TEST_WORKSPACE/logs" \
    FASTINTERCOM_PROGRESS_ENABLED=true \
    FASTINTERCOM_SESSION_ID="$TEST_RUN_ID" \
        $CLI_CMD sync --force --days "$DAYS" 2>&1 | tee "$TEST_WORKSPACE/logs/sync_output.txt"
    
    local sync_exit_code="${PIPESTATUS[0]}"
    
    if [[ "$sync_exit_code" -eq 0 ]]; then
        local sync_end_time
        sync_end_time=$(date +%s)
        local sync_duration=$((sync_end_time - sync_start_time))
        
        # Extract metrics from output
        local conversations_synced
        conversations_synced=$(grep -o '[0-9]\+ conversations' "$TEST_WORKSPACE/logs/sync_output.txt" | tail -1 | grep -o '[0-9]\+' || echo "0")
        
        local messages_synced
        messages_synced=$(grep -o '[0-9]\+ messages' "$TEST_WORKSPACE/logs/sync_output.txt" | tail -1 | grep -o '[0-9]\+' || echo "0")
        
        # Calculate sync speed
        local sync_speed
        if [[ "$sync_duration" -gt 0 && "$conversations_synced" -gt 0 ]]; then
            sync_speed=$(echo "scale=1; $conversations_synced / $sync_duration" | bc 2>/dev/null || echo "0")
        else
            sync_speed="0"
        fi
        
        log_success "Enhanced sync completed: $conversations_synced conversations, $messages_synced messages"
        log_info "Sync duration: ${sync_duration}s (${sync_speed} conv/sec)"
        
        # Update process tracking with results
        $PYTHON_CMD -c "
try:
    from fast_intercom_mcp.core.status_tracker import update_process_tracking
    update_process_tracking(
        '$TEST_RUN_ID',
        progress={
            'phase': 'data_sync',
            'status': 'completed',
            'conversations': $conversations_synced,
            'messages': $messages_synced,
            'duration': $sync_duration,
            'speed': $sync_speed
        }
    )
except: pass
" 2>/dev/null || true
        
        # Verify data was stored
        local db_file="$TEST_WORKSPACE/data/data.db"
        if [[ -f "$db_file" ]]; then
            local stored_conversations
            stored_conversations=$(sqlite3 "$db_file" "SELECT COUNT(*) FROM conversations;" 2>/dev/null || echo "0")
            
            if [[ "$stored_conversations" -gt 0 ]]; then
                log_success "Data verification: $stored_conversations conversations stored"
                TEST_RESULTS+=("data_sync:PASSED:$conversations_synced:$sync_speed")
                
                # Store metrics for performance report
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
        log_error "Enhanced sync operation failed (exit code: $sync_exit_code)"
        TEST_RESULTS+=("data_sync:FAILED")
        return 1
    fi
}

# Generate enhanced test report
generate_enhanced_test_report() {
    log_section "Enhanced Test Results"
    
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
    
    # Update final process tracking
    local final_status="completed"
    if [[ ${#failed_tests[@]} -gt 0 ]]; then
        final_status="failed"
    fi
    
    $PYTHON_CMD -c "
try:
    from fast_intercom_mcp.core.status_tracker import update_process_tracking
    update_process_tracking(
        '$TEST_RUN_ID',
        progress={
            'phase': 'completed',
            'status': '$final_status',
            'total_tests': $total_tests,
            'passed_tests': $passed_tests,
            'duration': $total_duration
        }
    )
except: pass
" 2>/dev/null || true
    
    # Generate summary
    echo ""
    echo "= $SCRIPT_NAME - Enhanced Test Report"
    echo "=================================================================================="
    echo "Test Run ID: $TEST_RUN_ID"
    echo "Test Duration: ${total_duration}s"
    echo "Tests Passed: $passed_tests/$total_tests"
    echo "Enhanced Features: ✓ Progress broadcasting, ✓ Status tracking"
    echo ""
    
    # Show monitoring commands
    log_info "💡 Post-test analysis commands:"
    log_info "   fast-intercom-mcp debug health --verbose"
    log_info "   fast-intercom-mcp logs errors --summary"
    echo ""
    
    # Final result
    if [[ ${#failed_tests[@]} -eq 0 ]]; then
        log_success "🎉 Enhanced Integration Test PASSED"
        echo ""
        return 0
    else
        log_error "❌ Enhanced Integration Test FAILED"
        echo ""
        log_error "Failed tests:"
        for failed_test in "${failed_tests[@]}"; do
            log_error "  - $failed_test"
        done
        echo ""
        return 1
    fi
}

# Main execution function
main() {
    log_section "$SCRIPT_NAME v$SCRIPT_VERSION"
    
    if [[ "$QUICK_MODE" == "true" ]]; then
        log_info "Running in QUICK mode with enhanced monitoring ($DAYS days)"
    else
        log_info "Running full enhanced integration test ($DAYS days)"
    fi
    
    log_info "✨ Enhanced features: Real-time progress, status tracking, CLI integration"
    echo ""
    
    # Run enhanced test sequence
    setup_test_environment || exit 5
    test_data_sync || exit 2
    
    # Generate enhanced results
    generate_enhanced_test_report
    local test_result=$?
    
    exit $test_result
}

# Execute main function
main "$@"