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

def extract_messages_from_mapping(mapping, current_node):
    """Extract messages from the mapping tree structure"""
    messages = []
    
    def traverse_node(node_id):
        if node_id not in mapping:
            return
            
        node = mapping[node_id]
        
        # Extract message if present
        if 'message' in node and node['message'] is not None:
            msg = node['message']
            if msg.get('author', {}).get('role') and msg.get('content'):
                message_data = {
                    'id': msg.get('id', node_id),
                    'author': msg['author']['role'],
                    'content': msg['content'],
                    'create_time': msg.get('create_time', 0)
                }
                messages.append(message_data)
        
        # Traverse children
        if 'children' in node:
            for child_id in node['children']:
                traverse_node(child_id)
    
    # Start from root or first node
    if 'client-created-root' in mapping:
        traverse_node('client-created-root')
    elif current_node:
        traverse_node(current_node)
    else:
        # Fallback: traverse all nodes
        for node_id in mapping:
            if mapping[node_id].get('parent') is None:
                traverse_node(node_id)
    
    return messages

def import_conversations(json_path, db_path):
    print(f"Loading {json_path}...")
    with open(json_path, 'r') as f:
        data = json.load(f)
    
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
    
    total_messages = 0
    total_errors = 0
    batch = []
    
    for conv in tqdm(data, desc="Processing conversations"):
        try:
            conv_id = conv.get('conversation_id', conv.get('id', ''))
            title = conv.get('title', 'Untitled')
            created = convert_timestamp(conv.get('create_time', 0))
            updated = convert_timestamp(conv.get('update_time', 0))
            
            # Extract messages from mapping
            if 'mapping' in conv:
                messages = extract_messages_from_mapping(
                    conv['mapping'], 
                    conv.get('current_node')
                )
                
                # Insert metadata
                conn.execute("""
                    INSERT OR REPLACE INTO metadata VALUES (?, ?, ?, ?, ?)
                """, (conv_id, title, len(messages), created, updated))
                
                # Process messages
                for msg in messages:
                    try:
                        msg_time = convert_timestamp(msg.get('create_time', 0))
                        author = msg.get('author', 'unknown')
                        
                        # Extract content
                        content = ""
                        content_obj = msg.get('content', {})
                        
                        if isinstance(content_obj, dict):
                            # Handle different content types
                            if 'parts' in content_obj:
                                parts = content_obj.get('parts', [])
                                if parts and isinstance(parts, list):
                                    content = ' '.join(str(p) for p in parts if p)
                            elif 'text' in content_obj:
                                content = str(content_obj['text'])
                            else:
                                content = str(content_obj)
                        elif isinstance(content_obj, str):
                            content = content_obj
                        
                        if content.strip():
                            batch.append((conv_id, msg_time, author, content))
                            total_messages += 1
                            
                            if len(batch) >= 1000:
                                conn.executemany(
                                    "INSERT INTO conversations VALUES (?, ?, ?, ?)",
                                    batch
                                )
                                batch = []
                                
                    except Exception as e:
                        total_errors += 1
                        if total_errors < 10:
                            print(f"\nError processing message: {e}")
                        
        except Exception as e:
            total_errors += 1
            if total_errors < 10:
                print(f"\nError processing conversation: {e}")
    
    # Final batch
    if batch:
        conn.executemany(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            batch
        )
    
    conn.commit()
    print(f"\nImported {len(data)} conversations with {total_messages} messages")
    if total_errors > 0:
        print(f"Encountered {total_errors} errors during import")
    
    # Optimize
    print("Optimizing database...")
    conn.execute("INSERT INTO conversations(conversations) VALUES('optimize')")
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_chatgpt_mapping.py <conversations.json>")
        sys.exit(1)
    
    # Clear existing database
    db_path = "data/conversations.db"
    if Path(db_path).exists():
        Path(db_path).unlink()
        print(f"Removed existing database")
    
    import_conversations(sys.argv[1], db_path)
