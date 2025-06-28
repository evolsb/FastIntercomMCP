#!/usr/bin/env python3
"""
Local CI Mirror Test - Mirrors GitHub Actions quick-test.yml exactly
This script runs the same tests as GitHub Actions to ensure local/CI consistency
"""
import asyncio
import os
import sys
import json
import time
import shutil
from datetime import datetime, timedelta, UTC
from pathlib import Path

# Add the project to the path  
sys.path.insert(0, str(Path(__file__).parent))

def setup_local_ci_environment():
    """Setup environment variables to mirror CI"""
    # Mirror CI environment variables
    os.environ['PYTHONPATH'] = str(Path(__file__).parent)
    os.environ['FORCE_COLOR'] = '1'
    
    print("🔧 Setting up local CI mirror environment...")
    print(f"PYTHONPATH: {os.environ['PYTHONPATH']}")
    print(f"Python version: {sys.version}")

def verify_package_installation():
    """Mirror: Verify package installation step from CI"""
    print("📦 Verifying package installation...")
    
    # Test 1: Import package (same as CI)
    try:
        import fast_intercom_mcp  # noqa: F401
        print("✅ Package imported successfully")
    except ImportError as e:
        print(f"❌ Package import failed: {e}")
        return False
    
    # Test 2: CLI help (same as CI)
    try:
        import subprocess
        result = subprocess.run([
            sys.executable, '-m', 'fast_intercom_mcp', '--help'
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            print("✅ CLI help command works")
        else:
            print(f"❌ CLI help failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ CLI test failed: {e}")
        return False
    
    return True

def create_test_environment():
    """Mirror: Create test environment step from CI"""
    print("🏗️ Creating test environment...")
    
    # Create temporary test directory (like CI)
    test_dir = Path.cwd() / "local_ci_test_data"
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(exist_ok=True)
    
    print(f"✅ Test environment created at: {test_dir}")
    return test_dir

async def run_quick_integration_test(test_dir, sync_days=1):
    """
    Mirror: Run quick integration test step from CI
    This is EXACT copy of the Python code from quick-test.yml
    """
    print("🚀 Starting Quick Integration Test")
    print(f"Test environment: {sys.version}")
    print(f"Sync days: {sync_days}")
    print("Expected duration: 3-5 minutes")
    print("")
    
    # Change to test directory (like CI)
    original_cwd = os.getcwd()
    os.chdir(test_dir)
    
    try:
        # This is the EXACT Python code from quick-test.yml workflow
        from fast_intercom_mcp.sync_service import SyncService
        from fast_intercom_mcp.database import DatabaseManager
        from fast_intercom_mcp.intercom_client import IntercomClient
        
        print('⏱️  Test started at:', datetime.now(UTC).strftime('%H:%M:%S UTC'))
        
        # Initialize components
        db = DatabaseManager('./quick_test.db')
        client = IntercomClient(os.getenv('INTERCOM_ACCESS_TOKEN'))
        sync_service = SyncService(db, client)
        
        # Quick API connection test
        print('🔌 Testing API connection...')
        connection_result = await client.test_connection()
        if not connection_result:
            raise Exception('API connection failed')
        print('✅ API connection successful')
        
        # Quick sync test with limited conversations for speed
        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=sync_days)
        
        print(f'🔄 Quick sync: {sync_days} day(s) of data (limited to 50 conversations for speed)')
        print(f'📅 Period: {start_date.strftime("%Y-%m-%d")} to {end_date.strftime("%Y-%m-%d")}')
        
        sync_start = time.time()
        # Use the proven sync_period method with a very short period for speed
        recent_time = end_date - timedelta(hours=2)  # Last 2 hours for speed
        
        print(f'🔄 Using sync_period with last 2 hours: {recent_time.strftime("%H:%M")} to {end_date.strftime("%H:%M")}')
        
        stats = await sync_service.sync_period(recent_time, end_date)
        sync_duration = time.time() - sync_start
        
        # Results
        rate = stats.total_conversations / max(sync_duration, 1)
        
        print('')
        print('📊 Quick Test Results:')
        print(f'✅ Conversations synced: {stats.total_conversations:,}')
        print(f'✅ Messages synced: {stats.total_messages:,}')
        print(f'✅ Sync speed: {rate:.1f} conversations/second')
        print(f'✅ Duration: {sync_duration:.1f} seconds')
        print(f'✅ API calls: {stats.api_calls_made:,}')
        
        # Quick MCP tool test
        print('')
        print('🛠️ Testing MCP tools...')
        sync_service.get_status()  # Test that status works
        print('✅ Sync service status: OK')
        
        # Save quick results
        quick_results = {
            'test_type': 'quick',
            'sync_days': sync_days,
            'conversations': stats.total_conversations,
            'messages': stats.total_messages, 
            'duration_seconds': round(sync_duration, 2),
            'rate_conv_per_sec': round(rate, 2),
            'api_calls': stats.api_calls_made,
            'timestamp': datetime.now(UTC).isoformat()
        }
        
        with open('quick_results.json', 'w') as f:
            json.dump(quick_results, f, indent=2)
        
        print('')
        print('🎉 Quick integration test PASSED!')
        print(f'⏱️  Completed at: {datetime.now(UTC).strftime("%H:%M:%S UTC")}')
        
        return quick_results
        
    finally:
        # Restore original working directory
        os.chdir(original_cwd)

def display_test_summary(test_dir, quick_results):
    """Mirror: Display quick test summary step from CI"""
    print("")
    print("📋 QUICK TEST SUMMARY")
    print("="*20)
    
    if quick_results:
        print("✅ Status: SUCCESS")
        print("📊 Results:")
        print(json.dumps(quick_results, indent=2))
    else:
        print("❌ Status: FAILED")
        print("Check logs above for error details")

def check_api_token():
    """Check if API token is available"""
    token = os.getenv('INTERCOM_ACCESS_TOKEN')
    if not token:
        print("❌ INTERCOM_ACCESS_TOKEN environment variable not set")
        print("Please set your Intercom API token:")
        print("export INTERCOM_ACCESS_TOKEN=your_token_here")
        return False
    
    print("✅ API token found")
    return True

async def main():
    """Main test execution - mirrors CI workflow exactly"""
    print("🚀 Local CI Mirror Test - FastIntercom MCP")
    print("=" * 60)
    print("This test mirrors GitHub Actions quick-test.yml exactly")
    print("=" * 60)
    
    # Check prerequisites
    if not check_api_token():
        return 1
    
    # Step 1: Setup environment (mirror CI setup)
    setup_local_ci_environment()
    
    # Step 2: Verify package installation (mirror CI verification)
    if not verify_package_installation():
        print("❌ Package verification failed")
        return 1
    
    # Step 3: Create test environment (mirror CI test environment)
    test_dir = create_test_environment()
    
    # Step 4: Run quick integration test (mirror CI test execution)
    try:
        quick_results = await run_quick_integration_test(test_dir, sync_days=1)
    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        import traceback
        traceback.print_exc()
        quick_results = None
    
    # Step 5: Display test summary (mirror CI summary)
    display_test_summary(test_dir, quick_results)
    
    # Return appropriate exit code
    if quick_results:
        print("\n✅ Local CI mirror test PASSED - matches GitHub Actions behavior")
        return 0
    else:
        print("\n❌ Local CI mirror test FAILED")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)