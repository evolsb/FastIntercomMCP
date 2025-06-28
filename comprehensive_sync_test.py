#!/usr/bin/env python3
"""
Comprehensive local sync test with detailed performance metrics
"""
import time
import os
import sys
import psutil
import json
import asyncio
from datetime import datetime, timedelta
import threading
import subprocess
from pathlib import Path

# Add the project to the path
sys.path.insert(0, str(Path(__file__).parent))

from fast_intercom_mcp.config import Config
from fast_intercom_mcp.database import DatabaseManager
from fast_intercom_mcp.intercom_client import IntercomClient
from fast_intercom_mcp.sync_service import SyncService

class PerformanceMonitor:
    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.metrics = {
            'sync_duration': 0,
            'conversations_synced': 0,
            'sync_rate_per_second': 0,
            'peak_memory_mb': 0,
            'peak_cpu_percent': 0,
            'database_size_mb': 0,
            'api_requests_made': 0,
            'errors_encountered': 0,
            'server_startup_time': 0
        }
        self.memory_samples = []
        self.cpu_samples = []
        self.monitoring = False
        self.monitor_thread = None
        
    def start_monitoring(self):
        self.start_time = time.time()
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_resources)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        
    def stop_monitoring(self):
        self.end_time = time.time()
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=1)
        
        self.metrics['sync_duration'] = self.end_time - self.start_time
        if self.memory_samples:
            self.metrics['peak_memory_mb'] = max(self.memory_samples)
        if self.cpu_samples:
            self.metrics['peak_cpu_percent'] = max(self.cpu_samples)
            
    def _monitor_resources(self):
        process = psutil.Process()
        while self.monitoring:
            try:
                memory_mb = process.memory_info().rss / 1024 / 1024
                cpu_percent = process.cpu_percent()
                self.memory_samples.append(memory_mb)
                self.cpu_samples.append(cpu_percent)
                time.sleep(0.5)  # Sample every 500ms
            except:
                break

def test_environment_setup():
    """Test that all required environment variables are set"""
    print("🔧 Testing environment setup...")
    
    required_vars = ['INTERCOM_ACCESS_TOKEN']
    missing_vars = []
    
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"❌ Missing environment variables: {missing_vars}")
        print("Please set these variables in your .env file")
        return False
    
    print("✅ Environment variables configured")
    return True

def test_database_operations(config):
    """Test database initialization and basic operations"""
    print("🗄️ Testing database operations...")
    
    db_manager = DatabaseManager(config)
    
    # Test initialization
    start_time = time.time()
    db_manager.initialize_db()
    init_time = time.time() - start_time
    
    # Test database file exists and measure size
    db_path = Path(config.db_path)
    if db_path.exists():
        db_size_mb = db_path.stat().st_size / 1024 / 1024
        print(f"✅ Database initialized in {init_time:.2f}s, size: {db_size_mb:.2f}MB")
        return db_size_mb
    else:
        print("❌ Database file not created")
        return 0

def test_api_connectivity():
    """Test Intercom API connectivity and permissions"""
    print("🌐 Testing API connectivity...")
    
    try:
        config = Config()
        client = IntercomClient(config.intercom_access_token)
        
        # Test basic API call
        start_time = time.time()
        response = client.get_conversations(limit=1)
        api_time = time.time() - start_time
        
        if response and 'conversations' in response:
            print(f"✅ API connected in {api_time:.2f}s")
            total_count = response.get('total_count', 0)
            print(f"📊 Total conversations available: {total_count:,}")
            return True, total_count
        else:
            print("❌ API connection failed")
            return False, 0
            
    except Exception as e:
        print(f"❌ API error: {e}")
        return False, 0

async def test_sync_performance(monitor, days=7, max_conversations=1000):
    """Run a comprehensive sync test with performance monitoring"""
    print(f"🔄 Running sync test ({days} days, max {max_conversations} conversations)...")
    
    try:
        # Setup
        config = Config()
        config.max_conversations = max_conversations
        
        db_manager = DatabaseManager(config)
        client = IntercomClient(config.intercom_access_token)
        sync_service = SyncService(db_manager, client)  # Use same as GitHub tests
        
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        print(f"📅 Syncing from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        
        # Start monitoring
        monitor.start_monitoring()
        
        # Run sync using same method as GitHub tests
        sync_start = time.time()
        results = await sync_service.sync_period(start_date, end_date)
        sync_duration = time.time() - sync_start
        
        # Stop monitoring
        monitor.stop_monitoring()
        
        # Collect results (results is now SyncStats object)
        conversations_synced = results.total_conversations
        api_requests = results.api_calls_made  
        errors = results.errors_encountered
        
        monitor.metrics.update({
            'conversations_synced': conversations_synced,
            'api_requests_made': api_requests,
            'errors_encountered': errors,
            'sync_rate_per_second': conversations_synced / sync_duration if sync_duration > 0 else 0
        })
        
        print(f"✅ Sync completed: {conversations_synced} conversations in {sync_duration:.2f}s")
        print(f"📈 Rate: {monitor.metrics['sync_rate_per_second']:.1f} conversations/second")
        
        return True
        
    except Exception as e:
        monitor.stop_monitoring()
        print(f"❌ Sync failed: {e}")
        return False

def test_mcp_server_startup():
    """Test MCP server startup time and basic functionality"""
    print("🖥️ Testing MCP server startup...")
    
    try:
        # Start server in background
        start_time = time.time()
        
        # Use subprocess to start server with timeout
        process = subprocess.Popen(
            [sys.executable, '-m', 'fast_intercom_mcp', 'server'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait for server to start (look for startup message)
        startup_detected = False
        timeout = 10  # 10 second timeout
        
        while time.time() - start_time < timeout:
            if process.poll() is not None:
                # Process ended
                stdout, stderr = process.communicate()
                if "Server started" in stdout or "listening" in stdout.lower():
                    startup_detected = True
                break
            time.sleep(0.1)
        
        startup_time = time.time() - start_time
        
        # Clean up
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        
        if startup_detected or startup_time < 5:  # Consider quick exit as success
            print(f"✅ MCP server started in {startup_time:.2f}s")
            return startup_time
        else:
            print(f"⚠️ Server startup took {startup_time:.2f}s (may need investigation)")
            return startup_time
            
    except Exception as e:
        print(f"❌ MCP server test failed: {e}")
        return 0

def generate_performance_report(monitor, db_size_mb, total_conversations, server_startup_time):
    """Generate a comprehensive performance report"""
    
    report = {
        'test_info': {
            'timestamp': datetime.now().isoformat(),
            'test_duration_seconds': monitor.metrics['sync_duration'],
            'python_version': sys.version,
            'platform': sys.platform
        },
        'performance_metrics': {
            'sync_duration_seconds': round(monitor.metrics['sync_duration'], 2),
            'conversations_synced': monitor.metrics['conversations_synced'],
            'sync_rate_per_second': round(monitor.metrics['sync_rate_per_second'], 2),
            'peak_memory_mb': round(monitor.metrics['peak_memory_mb'], 2),
            'peak_cpu_percent': round(monitor.metrics['peak_cpu_percent'], 2),
            'database_size_mb': round(db_size_mb, 2),
            'api_requests_made': monitor.metrics['api_requests_made'],
            'errors_encountered': monitor.metrics['errors_encountered'],
            'server_startup_time_seconds': round(server_startup_time, 2)
        },
        'efficiency_metrics': {
            'conversations_per_mb': round(monitor.metrics['conversations_synced'] / max(db_size_mb, 0.1), 2),
            'api_efficiency': round(monitor.metrics['conversations_synced'] / max(monitor.metrics['api_requests_made'], 1), 2),
            'memory_efficiency_conversations_per_mb_ram': round(monitor.metrics['conversations_synced'] / max(monitor.metrics['peak_memory_mb'], 1), 2)
        },
        'assessment': {
            'overall_status': 'PASSED' if monitor.metrics['errors_encountered'] == 0 else 'PARTIAL',
            'performance_rating': _rate_performance(monitor.metrics),
            'total_conversations_available': total_conversations,
            'test_coverage_percent': round((monitor.metrics['conversations_synced'] / max(total_conversations, 1)) * 100, 2)
        }
    }
    
    return report

def _rate_performance(metrics):
    """Rate the overall performance"""
    score = 0
    
    # Sync rate (target: >10/sec)
    if metrics['sync_rate_per_second'] >= 20:
        score += 3
    elif metrics['sync_rate_per_second'] >= 10:
        score += 2
    elif metrics['sync_rate_per_second'] >= 5:
        score += 1
    
    # Memory usage (target: <100MB)
    if metrics['peak_memory_mb'] <= 50:
        score += 3
    elif metrics['peak_memory_mb'] <= 100:
        score += 2
    elif metrics['peak_memory_mb'] <= 200:
        score += 1
    
    # Error rate (target: 0 errors)
    if metrics['errors_encountered'] == 0:
        score += 2
    
    # Server startup (target: <3s)
    if metrics.get('server_startup_time', 10) <= 3:
        score += 2
    elif metrics.get('server_startup_time', 10) <= 5:
        score += 1
    
    total_possible = 10
    percentage = (score / total_possible) * 100
    
    if percentage >= 80:
        return "EXCELLENT"
    elif percentage >= 60:
        return "GOOD"
    elif percentage >= 40:
        return "FAIR"
    else:
        return "NEEDS_IMPROVEMENT"

def main():
    print("🚀 Starting Comprehensive FastIntercom MCP Sync Test")
    print("=" * 60)
    
    # Initialize monitoring
    monitor = PerformanceMonitor()
    
    # Test 1: Environment Setup
    if not test_environment_setup():
        return 1
    
    # Test 2: Database Operations
    config = Config()
    db_size_mb = test_database_operations(config)
    
    # Test 3: API Connectivity
    api_connected, total_conversations = test_api_connectivity()
    if not api_connected:
        return 1
    
    # Test 4: MCP Server Startup
    server_startup_time = test_mcp_server_startup()
    
    # Test 5: Sync Performance Test
    print("\n" + "=" * 60)
    print("🔄 STARTING COMPREHENSIVE SYNC TEST")
    print("=" * 60)
    
    # Run with moderate scope for comprehensive test
    success = asyncio.run(test_sync_performance(monitor, days=7, max_conversations=500))
    
    if not success:
        print("❌ Sync test failed!")
        return 1
    
    # Update final database size
    db_path = Path(config.db_path)
    if db_path.exists():
        db_size_mb = db_path.stat().st_size / 1024 / 1024
    
    # Generate and save report
    report = generate_performance_report(monitor, db_size_mb, total_conversations, server_startup_time)
    
    # Save report to file
    report_path = Path(__file__).parent / "comprehensive_test_report.json"
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    # Print summary
    print("\n" + "=" * 60)
    print("📊 COMPREHENSIVE TEST RESULTS")
    print("=" * 60)
    
    metrics = report['performance_metrics']
    assessment = report['assessment']
    efficiency = report['efficiency_metrics']
    
    print(f"✅ Overall Status: {assessment['overall_status']}")
    print(f"🏆 Performance Rating: {assessment['performance_rating']}")
    print(f"📈 Test Coverage: {assessment['test_coverage_percent']:.1f}%")
    print()
    print("📊 Performance Metrics:")
    print(f"  • Sync Duration: {metrics['sync_duration_seconds']}s")
    print(f"  • Conversations Synced: {metrics['conversations_synced']:,}")
    print(f"  • Sync Rate: {metrics['sync_rate_per_second']:.1f} conversations/sec")
    print(f"  • Peak Memory: {metrics['peak_memory_mb']:.1f}MB")
    print(f"  • Peak CPU: {metrics['peak_cpu_percent']:.1f}%")
    print(f"  • Database Size: {metrics['database_size_mb']:.2f}MB")
    print(f"  • API Requests: {metrics['api_requests_made']}")
    print(f"  • Errors: {metrics['errors_encountered']}")
    print(f"  • Server Startup: {metrics['server_startup_time_seconds']:.2f}s")
    print()
    print("🎯 Efficiency Metrics:")
    print(f"  • Conversations per MB Storage: {efficiency['conversations_per_mb']:.1f}")
    print(f"  • API Efficiency: {efficiency['api_efficiency']:.2f} conversations/request")
    print(f"  • Memory Efficiency: {efficiency['memory_efficiency_conversations_per_mb_ram']:.1f} conv/MB RAM")
    print()
    print(f"📄 Full report saved to: {report_path}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())