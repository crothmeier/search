"""SQLite database manager with FTS5 support for conversation search."""
import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from contextlib import contextmanager
from datetime import datetime
import os

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages SQLite database with FTS5 for conversation search."""
    
    def __init__(self, db_path: str):
        """Initialize database manager."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()
    
    def _initialize_database(self):
        """Create database schema if not exists."""
        with self.get_connection() as conn:
            # Create FTS5 virtual table for full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS conversations USING fts5(
                    conversation_id UNINDEXED,
                    timestamp UNINDEXED,
                    sender,
                    content,
                    tokenize='porter unicode61'
                )
            """)
            
            # Create metadata table for conversation info
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT,
                    message_count INTEGER,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # Create index on timestamps for sorting
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metadata_updated 
                ON metadata(updated_at DESC)
            """)
            
            # Create table for tracking imports
            conn.execute("""
                CREATE TABLE IF NOT EXISTS import_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT,
                    file_size INTEGER,
                    conversations_imported INTEGER,
                    messages_imported INTEGER,
                    imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    duration_seconds REAL
                )
            """)
    
    def insert_conversation(self, conversation_id: str, title: str, 
                          messages: List[Tuple[str, str, datetime]]) -> int:
        """
        Insert a conversation with its messages.
        
        Args:
            conversation_id: Unique conversation identifier
            title: Conversation title
            messages: List of (sender, content, timestamp) tuples
            
        Returns:
            Number of messages inserted
        """
        with self.get_connection() as conn:
            # Insert messages into FTS table
            message_data = [
                (conversation_id, timestamp.isoformat(), sender, content)
                for sender, content, timestamp in messages
            ]
            
            conn.executemany("""
                INSERT INTO conversations (conversation_id, timestamp, sender, content)
                VALUES (?, ?, ?, ?)
            """, message_data)
            
            # Insert/update metadata
            created_at = messages[0][2].isoformat() if messages else datetime.now().isoformat()
            updated_at = messages[-1][2].isoformat() if messages else datetime.now().isoformat()
            
            conn.execute("""
                INSERT OR REPLACE INTO metadata 
                (conversation_id, title, message_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (conversation_id, title, len(messages), created_at, updated_at))
            
            return len(messages)
    
    def search_conversations(self, query: str, limit: int = 20, 
                           offset: int = 0) -> List[Dict[str, Any]]:
        """
        Search conversations using FTS5.
        
        Args:
            query: Search query
            limit: Maximum results to return
            offset: Results offset for pagination
            
        Returns:
            List of search results with snippets
        """
        with self.get_connection() as conn:
            # Search with snippet generation
            results = conn.execute("""
                SELECT 
                    c.conversation_id,
                    c.timestamp,
                    c.sender,
                    snippet(conversations, 3, '<mark>', '</mark>', '...', 32) as snippet,
                    m.title,
                    m.message_count,
                    m.updated_at,
                    rank
                FROM conversations c
                JOIN metadata m ON c.conversation_id = m.conversation_id
                WHERE conversations MATCH ?
                ORDER BY rank
                LIMIT ? OFFSET ?
            """, (query, limit, offset)).fetchall()
            
            return [dict(row) for row in results]
    
    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get full conversation by ID."""
        with self.get_connection() as conn:
            # Get metadata
            metadata = conn.execute("""
                SELECT * FROM metadata WHERE conversation_id = ?
            """, (conversation_id,)).fetchone()
            
            if not metadata:
                return None
            
            # Get all messages
            messages = conn.execute("""
                SELECT timestamp, sender, content
                FROM conversations
                WHERE conversation_id = ?
                ORDER BY timestamp
            """, (conversation_id,)).fetchall()
            
            result = dict(metadata)
            result['messages'] = [dict(msg) for msg in messages]
            return result
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        with self.get_connection() as conn:
            stats = {}
            
            # Total conversations
            stats['total_conversations'] = conn.execute(
                "SELECT COUNT(*) FROM metadata"
            ).fetchone()[0]
            
            # Total messages
            stats['total_messages'] = conn.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0]
            
            # Database size
            stats['database_size_mb'] = os.path.getsize(self.db_path) / (1024 * 1024)
            
            # Import history
            last_import = conn.execute("""
                SELECT * FROM import_history 
                ORDER BY imported_at DESC 
                LIMIT 1
            """).fetchone()
            
            if last_import:
                stats['last_import'] = dict(last_import)
            
            # Date range
            date_range = conn.execute("""
                SELECT MIN(created_at) as earliest, MAX(updated_at) as latest
                FROM metadata
            """).fetchone()
            
            if date_range:
                stats['date_range'] = {
                    'earliest': date_range['earliest'],
                    'latest': date_range['latest']
                }
            
            return stats
    
    def record_import(self, file_path: str, file_size: int, 
                     conversations: int, messages: int, duration: float):
        """Record import history."""
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO import_history 
                (file_path, file_size, conversations_imported, messages_imported, duration_seconds)
                VALUES (?, ?, ?, ?, ?)
            """, (file_path, file_size, conversations, messages, duration))
    
    def optimize(self):
        """Optimize database for better performance."""
        with self.get_connection() as conn:
            conn.execute("VACUUM")
            conn.execute("ANALYZE")
            logger.info("Database optimized")