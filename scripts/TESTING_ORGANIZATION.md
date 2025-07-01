# Test Script Organization

## Directory Structure

```
scripts/
├── integration/              # Integration test scripts (core testing)
│   ├── run_integration_test.sh
│   ├── run_integration_test_detailed.sh  
│   ├── test_docker_install.sh
│   └── test_mcp_tools.py
├── unit/                     # Unit test helpers
│   ├── pre_commit_validation.sh
│   └── docker_test_runner.sh
├── analysis/                 # Content analysis and investigation
│   ├── analyze_customer_messages.py
│   ├── analyze_conversation_updates_multidate.py
│   ├── content_verification.py  (NEW)
│   └── investigate_conversation_updates.py
├── performance/              # Performance testing
│   ├── performance_test.py
│   ├── quick_performance_test.py
│   └── generate_performance_report.py
├── utilities/                # One-time investigation tools
│   ├── debug_sync_issue.py
│   ├── generate_test_data.py
│   ├── import_test_data.py
│   ├── monitor_sync_progress.py
│   ├── quick_test.py
│   ├── simple_analysis.py
│   ├── simple_analysis_multidate.py
│   └── test_sync_specific_date.py
└── baseline/                 # Baseline and comparison tools
    ├── record_baseline.py
    └── compare_to_baseline.py
```

## Script Categories

### 🧪 **Integration Tests** (Core Testing Pipeline)
**Purpose**: Verify the system works end-to-end  
**When to run**: Before PRs, after major changes, scheduled CI  
**Expected runtime**: 2-15 minutes

- `run_integration_test.sh` - Main integration test (1-7 days data)
- `run_integration_test_detailed.sh` - With persistent logging  
- `test_docker_install.sh` - Docker deployment testing
- `test_mcp_tools.py` - MCP protocol validation

### 🔬 **Unit Tests** (Code Quality)
**Purpose**: Validate code quality and unit functionality  
**When to run**: Pre-commit, fast feedback  
**Expected runtime**: 30 seconds - 2 minutes

- `pre_commit_validation.sh` - Linting, type checking, unit tests
- `docker_test_runner.sh` - CI environment parity

### 📊 **Analysis Scripts** (Content Verification)
**Purpose**: Verify data quality and service behavior  
**When to run**: After integration tests, investigate issues  
**Expected runtime**: 1-5 minutes

- `analyze_customer_messages.py` - Customer vs admin message analysis
- `content_verification.py` - NEW: Comprehensive content analysis
- `analyze_conversation_updates_multidate.py` - Multi-date investigation

### ⚡ **Performance Tests** (Performance Validation)
**Purpose**: Ensure performance targets are met  
**When to run**: After optimization changes, periodic monitoring  
**Expected runtime**: 2-10 minutes

- `performance_test.py` - Comprehensive performance testing
- `quick_performance_test.py` - Quick performance check
- `generate_performance_report.py` - Detailed performance analysis

### 🔧 **Investigation Tools** (One-time Use)
**Purpose**: Debug specific issues, explore data  
**When to run**: As needed for troubleshooting  
**Expected runtime**: Variable

- `debug_sync_issue.py` - Debug specific sync problems
- `investigate_conversation_updates.py` - Deep dive into update patterns
- `quick_test.py` - Quick ad-hoc testing
- Various simple_analysis scripts

### 📈 **Baseline Tools** (Comparison)
**Purpose**: Track changes over time, regression detection  
**When to run**: Before major changes, periodic snapshots  
**Expected runtime**: 1-3 minutes

- `record_baseline.py` - Capture current performance baseline
- `compare_to_baseline.py` - Compare against recorded baseline

## Testing Workflow

### **Daily Development**
```bash
# Quick feedback loop
./scripts/unit/pre_commit_validation.sh --fast
./scripts/integration/run_integration_test.sh --days 1
```

### **Before PR Submission**
```bash
# Full validation
./scripts/unit/pre_commit_validation.sh
./scripts/integration/run_integration_test.sh --days 7
./scripts/analysis/content_verification.py
```

### **Investigation/Debugging**
```bash
# Content analysis
./scripts/analysis/analyze_customer_messages.py --date 2025-06-30
./scripts/analysis/content_verification.py --detailed

# Performance investigation  
./scripts/performance/quick_performance_test.py
```

### **Release Preparation**
```bash
# Comprehensive testing
./scripts/integration/run_integration_test_detailed.sh --days 7
./scripts/performance/performance_test.py
./scripts/integration/test_docker_install.sh --with-api-test
```

## Usage Guidelines

1. **Integration tests** should ALWAYS pass before merging
2. **Analysis scripts** help verify data quality but don't block deployments
3. **Investigation tools** are for troubleshooting only
4. **Performance tests** verify we meet targets but allow reasonable variance
5. **Baseline tools** help track trends over time

## Next Steps

- [ ] Reorganize existing scripts into this structure
- [ ] Create `content_verification.py` for comprehensive content analysis
- [ ] Update integration test to include content verification step
- [ ] Add automation to run appropriate tests based on changes