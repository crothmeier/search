#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from decimal import Decimal
import sqlite3
from datetime import datetime
from tqdm import tqdm

def convert_timestamp(ts):
    """Convert ChatGPT timestamp to string, handling Decimal types"""
    if isinstance(ts, Decimal):
        ts = int(ts)
    if ts > 1e10:  # Milliseconds
        ts = ts / 1000
    return datetime.fromtimestamp(ts).isoformat()

def import_conversations(json_path, db_path):
    # Create database
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS conversations USING fts5(
            conversation_id,
            timestamp,
            sender,
            content,
            tokenize='porter unicode61'
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            message_count INTEGER,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    
    # Load JSON (for 530MB, this should be ok)
    print(f"Loading {json_path}...")
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    # Process conversations
    total_messages = 0
    batch = []
    
    for conv in tqdm(data, desc="Processing conversations"):
        conv_id = conv.get('id', '')
        title = conv.get('title', 'Untitled')
        created = convert_timestamp(conv.get('create_time', 0))
        updated = convert_timestamp(conv.get('update_time', 0))
        
        # Insert metadata
        conn.execute("""
            INSERT OR REPLACE INTO metadata VALUES (?, ?, ?, ?, ?)
        """, (conv_id, title, len(conv.get('messages', [])), created, updated))
        
        # Process messages
        for msg in conv.get('messages', []):
            try:
                msg_time = convert_timestamp(msg.get('create_time', 0))
                author = msg.get('author', {}).get('role', 'unknown')
                
                # Extract content
                content = ""
                content_obj = msg.get('content', {})
                if isinstance(content_obj, dict):
                    parts = content_obj.get('parts', [])
                    if parts and isinstance(parts, list):
                        content = str(parts[0])
                
                if content:
                    batch.append((conv_id, msg_time, author, content))
                    total_messages += 1
                    
                    if len(batch) >= 1000:
                        conn.executemany(
                            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
                            batch
                        )
                        batch = []
                        
            except Exception as e:
                print(f"Error processing message: {e}")
    
    # Final batch
    if batch:
        conn.executemany(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            batch
        )
    
    conn.commit()
    print(f"\nImported {len(data)} conversations with {total_messages} messages")
    
    # Optimize
    print("Optimizing database...")
    conn.execute("INSERT INTO conversations(conversations) VALUES('optimize')")
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_chatgpt_fixed.py <conversations.json>")
        sys.exit(1)
    
    import_conversations(sys.argv[1], "data/conversations.db")
