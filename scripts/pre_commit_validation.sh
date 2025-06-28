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
elif [ -f ".venv/bin/activate" ]; then
    echo "📦 Using .venv environment"
    source .venv/bin/activate
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

# 2. Linting (focus on key files, ignore legacy issues)
echo "2️⃣ Running linting on key test files..."
if [ -f "performance_test.py" ]; then
    $RUFF_CMD check performance_test.py || exit 1
fi
if [ -f "quick_performance_test.py" ]; then
    $RUFF_CMD check quick_performance_test.py || exit 1
fi
if [ -f "local_ci_mirror_test.py" ]; then
    $RUFF_CMD check local_ci_mirror_test.py || exit 1
fi
echo "✅ Key test files pass linting"

# 3. Quick unit tests
echo "3️⃣ Running quick unit tests..."
if [ -d "tests" ]; then
    $PYTEST_CMD tests/ -x --tb=short -q || exit 1
else
    echo "⚠️ No tests directory found, skipping unit tests"
fi

# 4. CLI smoke test
echo "4️⃣ Testing CLI functionality..."
$PYTHON_CMD -m fast_intercom_mcp --help >/dev/null || exit 1

# 5. Test consistency validation (ensure sync paths are consistent)
echo "5️⃣ Validating test consistency..."
if [ -f "performance_test.py" ]; then
    # Check that performance_test.py uses SyncService.sync_period
    if grep -q "SyncService.sync_period" performance_test.py; then
        echo "✅ performance_test.py uses correct sync method"
    else
        echo "❌ performance_test.py does not use SyncService.sync_period"
        exit 1
    fi
fi

if [ -f "quick_performance_test.py" ]; then
    # Check that quick_performance_test.py uses SyncService.sync_period
    if grep -q "SyncService.sync_period" quick_performance_test.py; then
        echo "✅ quick_performance_test.py uses correct sync method"
    else
        echo "❌ quick_performance_test.py does not use SyncService.sync_period"
        exit 1
    fi
fi

if [ -f "comprehensive_sync_test.py" ]; then
    # Check that comprehensive_sync_test.py uses SyncService.sync_period
    if grep -q "SyncService.sync_period" comprehensive_sync_test.py; then
        echo "✅ comprehensive_sync_test.py uses correct sync method"
    else
        echo "❌ comprehensive_sync_test.py does not use SyncService.sync_period"
        exit 1
    fi
fi

echo "✅ All pre-commit checks passed!"