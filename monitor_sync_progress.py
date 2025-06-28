#!/usr/bin/env python3
"""
Monitor sync progress in real time
"""
import time
import sqlite3
from pathlib import Path

def get_db_stats(db_path):
    if not Path(db_path).exists():
        return None
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM conversations")
        conversations = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM messages")  
        messages = cursor.fetchone()[0]
        
        conn.close()
        
        size_mb = Path(db_path).stat().st_size / 1024 / 1024
        
        return {
            'conversations': conversations,
            'messages': messages,
            'size_mb': size_mb
        }
    except:
        return None

def main():
    db_path = Path.home() / ".fast-intercom-mcp-full-test" / "data.db"
    
    print("📊 Monitoring sync progress...")
    print("Press Ctrl+C to stop monitoring")
    print("-" * 60)
    
    last_conversations = 0
    start_time = time.time()
    
    try:
        while True:
            stats = get_db_stats(db_path)
            elapsed = time.time() - start_time
            
            if stats:
                conversations = stats['conversations']
                messages = stats['messages']
                size_mb = stats['size_mb']
                
                # Calculate rate
                conversations_per_sec = conversations / max(elapsed, 1)
                
                # Show progress
                print(f"⏱️ {elapsed:5.0f}s | 💬 {conversations:6,} conv | ✉️ {messages:7,} msg | 💾 {size_mb:6.1f}MB | 🚀 {conversations_per_sec:5.1f}/sec")
                
                last_conversations = conversations
            else:
                print(f"⏱️ {elapsed:5.0f}s | ⌛ Waiting for database...")
            
            time.sleep(10)  # Update every 10 seconds
            
    except KeyboardInterrupt:
        print("\n📊 Monitoring stopped")
        if stats:
            print(f"Final stats: {stats['conversations']:,} conversations, {stats['messages']:,} messages, {stats['size_mb']:.1f}MB")

if __name__ == "__main__":
    main()