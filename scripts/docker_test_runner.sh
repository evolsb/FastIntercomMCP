#!/bin/bash
set -e

# Docker Test Runner - Run tests in clean Docker environment for environment parity
SCRIPT_NAME="Docker Test Runner"
SCRIPT_VERSION="1.0.0"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Logging functions
log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
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

Usage: $0 [OPTIONS] [TEST_TYPE]

TEST_TYPES:
    quick       Quick integration test (default)
    full        Full integration test with performance metrics
    unit        Unit tests only
    consistency Test sync method consistency
    all         Run all test types

OPTIONS:
    --api-token TOKEN   Intercom API token (or use INTERCOM_ACCESS_TOKEN env var)
    --keep-container    Don't remove container after test (for debugging)
    --debug            Enable debug logging
    --help             Show this help message

EXAMPLES:
    # Quick test with environment consistency
    $0 quick
    
    # Full test with API token
    $0 --api-token your_token_here full
    
    # Debug failing test (keeps container for inspection)
    $0 --debug --keep-container unit

REQUIREMENTS:
    - Docker installed and running
    - Intercom API token (for integration tests)
    - Docker build context should be project root

EXIT CODES:
    0 - All tests passed
    1 - Docker build failed
    2 - Test execution failed
    3 - Environment setup failed
EOF
}

# Global variables
API_TOKEN=""
KEEP_CONTAINER="false"
DEBUG="false"
TEST_TYPE="quick"
CONTAINER_NAME="fast-intercom-mcp-test-$(date +%s)"
IMAGE_NAME="fast-intercom-mcp-test"

# Parse command line options
while [[ $# -gt 0 ]]; do
    case $1 in
        --api-token)
            API_TOKEN="$2"
            shift 2
            ;;
        --keep-container)
            KEEP_CONTAINER="true"
            shift
            ;;
        --debug)
            DEBUG="true"
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        quick|full|unit|consistency|all)
            TEST_TYPE="$1"
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Get API token from environment if not provided
if [[ -z "$API_TOKEN" ]]; then
    API_TOKEN="$INTERCOM_ACCESS_TOKEN"
fi

cleanup() {
    local exit_code=$?
    
    if [[ "$KEEP_CONTAINER" == "false" ]]; then
        log_info "Cleaning up Docker resources..."
        docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
        if [[ "$DEBUG" != "true" ]]; then
            docker rmi "$IMAGE_NAME" 2>/dev/null || true
        fi
        log_success "Cleanup completed"
    else
        log_warning "Container preserved for debugging: $CONTAINER_NAME"
        log_info "To inspect: docker exec -it $CONTAINER_NAME /bin/bash"
        log_info "To clean up later: docker rm -f $CONTAINER_NAME && docker rmi $IMAGE_NAME"
    fi
    
    exit $exit_code
}

trap cleanup EXIT INT TERM

build_test_image() {
    log_section "Building Docker Test Image"
    
    # Create Dockerfile.test for testing environment
    cat > Dockerfile.test << 'EOF'
FROM python:3.11-slim

# Install system dependencies including testing tools
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy application code
COPY . ./

# Install package in development mode with test dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install -e . && \
    pip install pytest pytest-asyncio pytest-cov httpx[http2]

# Verify installation
RUN python -c "import fast_intercom_mcp; print('✅ Package installed')" && \
    python -m fast_intercom_mcp --help

# Set environment for testing
ENV PYTHONPATH=/app
ENV FASTINTERCOM_TEST_MODE=1

# Default test command
CMD ["python", "-m", "pytest", "tests/", "-v"]
EOF

    log_info "Building test image: $IMAGE_NAME"
    
    if [[ "$DEBUG" == "true" ]]; then
        docker build -f Dockerfile.test -t "$IMAGE_NAME" . || {
            log_error "Docker build failed"
            exit 1
        }
    else
        docker build -f Dockerfile.test -t "$IMAGE_NAME" . >/dev/null 2>&1 || {
            log_error "Docker build failed"
            exit 1
        }
    fi
    
    # Clean up Dockerfile.test
    rm -f Dockerfile.test
    
    log_success "Docker test image built successfully"
}

run_quick_test() {
    log_section "Running Quick Integration Test in Docker"
    
    local docker_cmd="docker run --name $CONTAINER_NAME"
    
    # Add API token if available
    if [[ -n "$API_TOKEN" ]]; then
        docker_cmd="$docker_cmd -e INTERCOM_ACCESS_TOKEN=$API_TOKEN"
    fi
    
    # Add debug logging if enabled
    if [[ "$DEBUG" == "true" ]]; then
        docker_cmd="$docker_cmd -e FASTINTERCOM_LOG_LEVEL=DEBUG"
    fi
    
    # Run local CI mirror test
    docker_cmd="$docker_cmd $IMAGE_NAME python local_ci_mirror_test.py"
    
    log_info "Running command: $docker_cmd"
    eval "$docker_cmd" || {
        log_error "Quick integration test failed in Docker"
        return 1
    }
    
    log_success "Quick integration test passed in Docker environment"
}

run_unit_tests() {
    log_section "Running Unit Tests in Docker"
    
    local docker_cmd="docker run --name $CONTAINER_NAME-unit"
    
    if [[ "$DEBUG" == "true" ]]; then
        docker_cmd="$docker_cmd -e FASTINTERCOM_LOG_LEVEL=DEBUG"
    fi
    
    docker_cmd="$docker_cmd $IMAGE_NAME python -m pytest tests/ -v --tb=short"
    
    log_info "Running unit tests in clean Docker environment"
    eval "$docker_cmd" || {
        log_error "Unit tests failed in Docker"
        return 1
    }
    
    log_success "Unit tests passed in Docker environment"
}

run_consistency_test() {
    log_section "Running Test Consistency Validation in Docker"
    
    local docker_cmd="docker run --name $CONTAINER_NAME-consistency $IMAGE_NAME"
    
    # Run pre-commit validation to check consistency
    docker_cmd="$docker_cmd bash scripts/pre_commit_validation.sh"
    
    log_info "Validating test consistency in Docker environment"
    eval "$docker_cmd" || {
        log_error "Test consistency validation failed in Docker"
        return 1
    }
    
    log_success "Test consistency validation passed in Docker environment"
}

run_full_test() {
    log_section "Running Full Test Suite in Docker"
    
    # Run multiple test containers in sequence
    run_unit_tests || return 1
    run_consistency_test || return 1
    
    if [[ -n "$API_TOKEN" ]]; then
        run_quick_test || return 1
        
        # Run performance tests
        log_info "Running performance tests..."
        docker run --name "$CONTAINER_NAME-perf" \
            -e "INTERCOM_ACCESS_TOKEN=$API_TOKEN" \
            "$IMAGE_NAME" python performance_test.py || {
            log_error "Performance tests failed in Docker"
            return 1
        }
    else
        log_warning "Skipping API tests (no token provided)"
    fi
    
    log_success "Full test suite passed in Docker environment"
}

main() {
    log_section "$SCRIPT_NAME v$SCRIPT_VERSION"
    
    # Verify Docker is available
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed or not in PATH"
        exit 3
    fi
    
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker daemon is not running"
        exit 3
    fi
    
    log_info "Running $TEST_TYPE tests in clean Docker environment"
    
    # Build test image
    build_test_image
    
    # Run requested test type
    case "$TEST_TYPE" in
        quick)
            if [[ -z "$API_TOKEN" ]]; then
                log_error "API token required for quick test"
                log_info "Set INTERCOM_ACCESS_TOKEN or use --api-token option"
                exit 3
            fi
            run_quick_test || exit 2
            ;;
        unit)
            run_unit_tests || exit 2
            ;;
        consistency)
            run_consistency_test || exit 2
            ;;
        full)
            run_full_test || exit 2
            ;;
        all)
            run_unit_tests || exit 2
            run_consistency_test || exit 2
            if [[ -n "$API_TOKEN" ]]; then
                run_quick_test || exit 2
            else
                log_warning "Skipping API tests in 'all' mode (no token)"
            fi
            ;;
        *)
            log_error "Unknown test type: $TEST_TYPE"
            usage
            exit 1
            ;;
    esac
    
    log_success "Docker test run completed successfully"
    log_info "Environment parity validated ✅"
}

main "$@"