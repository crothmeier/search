"""Enhanced SQLite database manager with connection pooling and security features."""
import sqlite3
import logging
import hashlib
import fcntl
import time
import threading
import atexit
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union
from contextlib import contextmanager
from datetime import datetime
from queue import Queue, Empty
import os

logger = logging.getLogger(__name__)


class ConnectionPool:
    """Thread-safe SQLite connection pool."""
    
    def __init__(self, db_path: str, pool_size: int = 10):
        """Initialize connection pool."""
        self.db_path = db_path
        self.pool_size = pool_size
        self._pool = Queue(maxsize=pool_size)
        self._all_connections = []
        self._lock = threading.Lock()
        self._closed = False
        
        # Pre-create connections
        for _ in range(pool_size):
            conn = self._create_connection()
            self._pool.put(conn)
            self._all_connections.append(conn)
        
        # Register cleanup on exit
        atexit.register(self.close_all)
    
    def _create_connection(self) -> sqlite3.Connection:
        """Create a new database connection with optimizations."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL")
        
        # Set busy timeout to 5 seconds
        conn.execute("PRAGMA busy_timeout=5000")
        
        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys=ON")
        
        # Optimize for performance
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB memory map
        
        return conn
    
    @contextmanager
    def get_connection(self, timeout: float = 5.0):
        """Get a connection from the pool."""
        if self._closed:
            raise RuntimeError("Connection pool is closed")
        
        conn = None
        try:
            conn = self._pool.get(timeout=timeout)
            yield conn
            conn.commit()
        except Empty:
            raise TimeoutError("Failed to get connection from pool")
        except Exception as e:
            if conn:
                conn.rollback()
            raise
        finally:
            if conn and not self._closed:
                self._pool.put(conn)
    
    def close_all(self):
        """Close all connections in the pool."""
        with self._lock:
            if self._closed:
                return
            
            self._closed = True
            
            # Close all connections
            for conn in self._all_connections:
                try:
                    conn.close()
                except Exception as e:
                    logger.error(f"Error closing connection: {e}")
            
            logger.info("Connection pool closed")


class DatabaseManager:
    """Enhanced database manager with security and performance features."""
    
    def __init__(self, db_path: str, pool_size: int = 10):
        """Initialize enhanced database manager."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize with file locking
        self._initialize_with_lock()
        
        # Create connection pool
        self.pool = ConnectionPool(str(self.db_path), pool_size)
        
        # Prepared statement cache
        self._stmt_cache = {}
        self._stmt_lock = threading.Lock()
    
    def _initialize_with_lock(self):
        """Initialize database with file locking to prevent race conditions."""
        lock_file = self.db_path.with_suffix('.lock')
        
        with open(lock_file, 'w') as f:
            # Acquire exclusive lock
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                self._initialize_database()
            finally:
                # Release lock
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    
    def _initialize_database(self):
        """Create enhanced database schema."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            # Create FTS5 virtual table with better tokenization
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS conversations USING fts5(
                    conversation_id UNINDEXED,
                    timestamp UNINDEXED,
                    sender,
                    content,
                    tokenize='porter unicode61 remove_diacritics 2'
                )
            """)
            
            # Enhanced metadata table with checksum
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    checksum TEXT,
                    indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Search audit table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_hash TEXT NOT NULL,
                    user_id TEXT,
                    query_length INTEGER NOT NULL,
                    from_cache BOOLEAN DEFAULT 0,
                    duration_ms REAL,
                    error TEXT,
                    searched_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Enhanced import history with checksum
            conn.execute("""
                CREATE TABLE IF NOT EXISTS import_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    file_checksum TEXT NOT NULL,
                    conversations_imported INTEGER NOT NULL DEFAULT 0,
                    messages_imported INTEGER NOT NULL DEFAULT 0,
                    imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    duration_seconds REAL
                )
            """)
            
            # Create optimized indexes
            self._create_indexes(conn)
            
            conn.commit()
            logger.info("Database initialized with enhanced schema")
            
        finally:
            conn.close()
    
    def _create_indexes(self, conn: sqlite3.Connection):
        """Create optimized indexes for performance."""
        indexes = [
            # Metadata indexes
            "CREATE INDEX IF NOT EXISTS idx_metadata_updated ON metadata(updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_metadata_created ON metadata(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_metadata_checksum ON metadata(checksum)",
            
            # Search audit indexes
            "CREATE INDEX IF NOT EXISTS idx_audit_searched_at ON search_audit(searched_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_audit_query_hash ON search_audit(query_hash)",
            "CREATE INDEX IF NOT EXISTS idx_audit_user_id ON search_audit(user_id)",
            
            # Import history indexes
            "CREATE INDEX IF NOT EXISTS idx_import_checksum ON import_history(file_checksum)",
            "CREATE INDEX IF NOT EXISTS idx_import_date ON import_history(imported_at DESC)"
        ]
        
        for idx in indexes:
            conn.execute(idx)
    
    @contextmanager
    def get_connection(self, timeout: float = 5.0):
        """Get a database connection from the pool."""
        with self.pool.get_connection(timeout=timeout) as conn:
            yield conn
    
    def _calculate_checksum(self, data: Union[str, List[Tuple]]) -> str:
        """Calculate SHA256 checksum for data."""
        hasher = hashlib.sha256()
        
        if isinstance(data, str):
            hasher.update(data.encode('utf-8'))
        else:
            # For list of tuples (messages)
            for item in data:
                hasher.update(str(item).encode('utf-8'))
        
        return hasher.hexdigest()
    
    def insert_conversation(self, conversation_id: str, title: str,
                          messages: List[Tuple[str, str, datetime]],
                          checksum: Optional[str] = None) -> int:
        """Insert conversation with checksum verification."""
        if not checksum:
            checksum = self._calculate_checksum(messages)
        
        with self.get_connection() as conn:
            # Check if already imported
            existing = conn.execute(
                "SELECT checksum FROM metadata WHERE conversation_id = ?",
                (conversation_id,)
            ).fetchone()
            
            if existing and existing['checksum'] == checksum:
                logger.info(f"Conversation {conversation_id} already imported with same checksum")
                return 0
            
            # Insert messages using prepared statement
            message_data = [
                (conversation_id, timestamp.isoformat(), sender, content)
                for sender, content, timestamp in messages
            ]
            
            conn.executemany("""
                INSERT INTO conversations (conversation_id, timestamp, sender, content)
                VALUES (?, ?, ?, ?)
            """, message_data)
            
            # Insert/update metadata with checksum
            created_at = messages[0][2].isoformat() if messages else datetime.now().isoformat()
            updated_at = messages[-1][2].isoformat() if messages else datetime.now().isoformat()
            
            conn.execute("""
                INSERT OR REPLACE INTO metadata 
                (conversation_id, title, message_count, created_at, updated_at, checksum)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (conversation_id, title, len(messages), created_at, updated_at, checksum))
            
            return len(messages)
    
    def search_conversations(self, query: str, limit: int = 20,
                           offset: int = 0, timeout: float = 5.0) -> List[Dict[str, Any]]:
        """Search with timeout and prepared statements."""
        with self.get_connection(timeout=timeout) as conn:
            # Set query timeout
            conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
            
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
    
    def count_search_results(self, query: str, timeout: float = 5.0) -> int:
        """Count search results with timeout."""
        with self.get_connection(timeout=timeout) as conn:
            result = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE conversations MATCH ?",
                (query,)
            ).fetchone()
            return result[0] if result else 0
    
    def log_search_audit(self, query_hash: str, user_id: Optional[str],
                        query_length: int, from_cache: bool,
                        duration_ms: float, error: Optional[str] = None):
        """Log search audit trail."""
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO search_audit 
                (query_hash, user_id, query_length, from_cache, duration_ms, error)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (query_hash, user_id, query_length, from_cache, duration_ms, error))
    
    def cleanup_search_audits(self, before_date: datetime) -> int:
        """Clean up old search audit entries."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM search_audit WHERE searched_at < ?",
                (before_date.isoformat(),)
            )
            return cursor.rowcount
    
    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get conversation with prepared statement."""
        with self.get_connection() as conn:
            metadata = conn.execute(
                "SELECT * FROM metadata WHERE conversation_id = ?",
                (conversation_id,)
            ).fetchone()
            
            if not metadata:
                return None
            
            messages = conn.execute("""
                SELECT timestamp, sender, content
                FROM conversations
                WHERE conversation_id = ?
                ORDER BY timestamp
            """, (conversation_id,)).fetchall()
            
            result = dict(metadata)
            result['messages'] = [dict(msg) for msg in messages]
            return result
    
    def get_query_suggestions(self, partial: str, limit: int = 5) -> List[str]:
        """Get query suggestions with escaping."""
        with self.get_connection() as conn:
            # Escape for LIKE query
            escaped = partial.replace('\\', '\\\\')
            
            titles = conn.execute("""
                SELECT DISTINCT title 
                FROM metadata 
                WHERE title LIKE ? ESCAPE '\\'
                ORDER BY updated_at DESC
                LIMIT ?
            """, (f'%{escaped}%', limit)).fetchall()
            
            return [t['title'] for t in titles]
    
    def record_import(self, file_path: str, file_size: int,
                     conversations: int, messages: int,
                     duration: float, checksum: str):
        """Record import with checksum."""
        with self.get_connection() as conn:
            # Check if file already imported
            existing = conn.execute(
                "SELECT id FROM import_history WHERE file_checksum = ?",
                (checksum,)
            ).fetchone()
            
            if existing:
                logger.warning(f"File {file_path} already imported")
                return
            
            conn.execute("""
                INSERT INTO import_history 
                (file_path, file_size, file_checksum, conversations_imported, 
                 messages_imported, duration_seconds)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (file_path, file_size, checksum, conversations, messages, duration))
    
    def get_stats(self) -> Dict[str, Any]:
        """Get enhanced database statistics."""
        with self.get_connection() as conn:
            stats = {}
            
            # Basic stats
            stats['total_conversations'] = conn.execute(
                "SELECT COUNT(*) FROM metadata"
            ).fetchone()[0]
            
            stats['total_messages'] = conn.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0]
            
            # Database size
            stats['database_size_mb'] = os.path.getsize(self.db_path) / (1024 * 1024)
            
            # WAL size if exists
            wal_path = self.db_path.with_suffix('.db-wal')
            if wal_path.exists():
                stats['wal_size_mb'] = os.path.getsize(wal_path) / (1024 * 1024)
            
            # Search audit stats
            audit_stats = conn.execute("""
                SELECT 
                    COUNT(*) as total_searches,
                    AVG(duration_ms) as avg_duration_ms,
                    COUNT(DISTINCT user_id) as unique_users,
                    SUM(CASE WHEN from_cache THEN 1 ELSE 0 END) as cache_hits
                FROM search_audit
                WHERE searched_at > datetime('now', '-7 days')
            """).fetchone()
            
            stats['search_stats_7d'] = dict(audit_stats) if audit_stats else {}
            
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
    
    def optimize(self):
        """Optimize database with WAL checkpoint."""
        with self.get_connection() as conn:
            # Checkpoint WAL file
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            
            # Analyze for query optimization
            conn.execute("ANALYZE")
            
            # Vacuum to reclaim space
            conn.execute("VACUUM")
            
            logger.info("Database optimized")
    
    def close(self):
        """Close database connections."""
        self.pool.close_all()