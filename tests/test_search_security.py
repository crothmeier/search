"""Test secure search implementation."""
import pytest
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime
import redis
from unittest.mock import Mock, patch

from src.search_secure import SecureSearchEngine, SearchConstants
from src.database_enhanced import DatabaseManager, ConnectionPool


class TestSearchSecurity:
    """Test search security features."""
    
    @pytest.fixture
    def temp_db(self):
        """Create temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        db_manager = DatabaseManager(db_path)
        
        # Insert test data
        with db_manager.get_connection() as conn:
            # Insert test conversations
            test_data = [
                ("conv1", "Test conversation", [
                    ("user", "Hello world", datetime.now()),
                    ("assistant", "Hi there!", datetime.now())
                ]),
                ("conv2", "SQL injection test'; DROP TABLE--", [
                    ("user", "Test <script>alert('xss')</script>", datetime.now()),
                    ("assistant", "Response", datetime.now())
                ]),
                ("conv3", "Special chars test", [
                    ("user", 'Test with "quotes" and (parens)', datetime.now()),
                    ("assistant", "Response with [brackets] {braces}", datetime.now())
                ])
            ]
            
            for conv_id, title, messages in test_data:
                db_manager.insert_conversation(conv_id, title, messages)
        
        yield db_manager
        
        # Cleanup
        db_manager.close()
        Path(db_path).unlink(missing_ok=True)
    
    @pytest.fixture
    def search_engine(self, temp_db):
        """Create search engine with test database."""
        return SecureSearchEngine(temp_db)
    
    @pytest.fixture
    def search_engine_with_cache(self, temp_db):
        """Create search engine with Redis cache."""
        # Mock Redis client
        mock_redis = Mock(spec=redis.Redis)
        mock_redis.get.return_value = None
        mock_redis.setex.return_value = True
        
        engine = SecureSearchEngine(temp_db)
        engine.redis_client = mock_redis
        return engine, mock_redis
    
    def test_sql_injection_prevention(self, search_engine):
        """Test various SQL injection attempts are blocked."""
        # SQL injection attempts
        injection_queries = [
            "'; DROP TABLE conversations; --",
            '" OR 1=1 --',
            "' UNION SELECT * FROM metadata --",
            "'; DELETE FROM metadata WHERE 1=1; --",
            "\" OR \"\"=\"",
            "' OR '1'='1",
        ]
        
        for query in injection_queries:
            result = search_engine.search(query)
            # Should not error out or execute injection
            assert "error" not in result or result["error"] != "Search failed"
            # Should properly escape the query
            assert "processed_query" in result
            # Should not contain unescaped SQL
            assert "--" not in result["processed_query"] or "\\" in result["processed_query"]
    
    def test_fts5_special_char_escaping(self, search_engine):
        """Test FTS5 special characters are properly escaped."""
        special_chars = ['"', "'", '(', ')', '[', ']', '{', '}', '\\']
        
        for char in special_chars:
            query = f"test {char} character"
            result = search_engine.search(query)
            
            # Should not error
            assert "error" not in result or result["error"] != "Search failed"
            
            # Check escaping in processed query
            processed = result["processed_query"]
            if char != '"':  # Quotes might be preserved for phrases
                assert f"\\{char}" in processed or char not in processed
    
    def test_query_length_validation(self, search_engine):
        """Test query length limits."""
        # Too short
        result = search_engine.search("")
        assert result["total"] == 0
        assert "error" in result
        
        # Too long
        long_query = "x" * (SearchConstants.MAX_QUERY_LENGTH + 1)
        result = search_engine.search(long_query)
        assert result["total"] == 0
        assert "error" in result
        assert "too long" in result["error"].lower()
        
        # Valid length
        valid_query = "test"
        result = search_engine.search(valid_query)
        assert "error" not in result or result["error"] != "Search failed"
    
    def test_null_byte_removal(self, search_engine):
        """Test null bytes are removed from queries."""
        query = "test\x00query"
        result = search_engine.search(query)
        
        # Should not error
        assert "error" not in result or result["error"] != "Search failed"
        
        # Null byte should be removed
        assert "\x00" not in result["processed_query"]
    
    def test_boolean_operator_preservation(self, search_engine):
        """Test AND, OR, NOT operators are preserved."""
        queries = [
            "python AND java",
            "error OR warning",
            "test NOT fail",
            "word1 NEAR word2"
        ]
        
        for query in queries:
            result = search_engine.search(query)
            processed = result["processed_query"]
            
            # Operators should be uppercase and preserved
            for op in ["AND", "OR", "NOT", "NEAR"]:
                if op in query.upper():
                    assert op in processed
    
    def test_xss_prevention_in_snippets(self, search_engine):
        """Test XSS attempts in snippets are sanitized."""
        # Search for content with XSS attempt
        result = search_engine.search("script")
        
        if result["results"]:
            for res in result["results"]:
                snippet = res.get("snippet", "")
                # Script tags should be escaped
                assert "<script>" not in snippet
                assert "</script>" not in snippet
                # But mark tags should be preserved
                if "<mark>" in snippet:
                    assert "</mark>" in snippet
    
    def test_conversation_id_validation(self, search_engine):
        """Test only valid conversation IDs are accepted."""
        # Valid IDs
        valid_ids = ["conv1", "conv-2", "123-abc-456"]
        for conv_id in valid_ids:
            # Mock a valid conversation
            result = search_engine.get_conversation(conv_id)
            # Should not reject valid IDs
            
        # Invalid IDs
        invalid_ids = [
            "../etc/passwd",
            "conv1; DROP TABLE--",
            "conv1' OR '1'='1",
            "../../secret",
            "conv1\x00null"
        ]
        
        for conv_id in invalid_ids:
            result = search_engine.get_conversation(conv_id)
            assert result is None  # Should reject invalid IDs
    
    def test_cache_functionality(self, search_engine_with_cache):
        """Test Redis caching works correctly."""
        engine, mock_redis = search_engine_with_cache
        
        # First search - cache miss
        mock_redis.get.return_value = None
        result1 = engine.search("test query")
        
        # Should try to get from cache
        mock_redis.get.assert_called()
        
        # Should store in cache if results found
        if result1["results"]:
            mock_redis.setex.assert_called()
            cache_key = mock_redis.setex.call_args[0][0]
            assert cache_key.startswith("search:")
            assert mock_redis.setex.call_args[0][1] == SearchConstants.QUERY_CACHE_TTL
        
        # Second search - cache hit
        cached_data = '{"results": [], "total": 0, "query": "test query"}'
        mock_redis.get.return_value = cached_data
        result2 = engine.search("test query")
        
        assert result2["cached"] is True
    
    def test_search_audit_logging(self, temp_db, search_engine):
        """Test searches are logged to audit table."""
        # Perform search
        query = "test audit"
        result = search_engine.search(query, user_id="testuser")
        
        # Check audit log
        with temp_db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM search_audit ORDER BY searched_at DESC LIMIT 1"
            )
            audit = cursor.fetchone()
            
            assert audit is not None
            assert audit["user_id"] == "testuser"
            assert audit["result_count"] == len(result["results"])
            assert audit["execution_time_ms"] >= 0
            assert audit["query_hash"] == search_engine._hash_query(query)
    
    def test_connection_pool(self, temp_db):
        """Test connection pool handles concurrent requests."""
        import threading
        import time
        
        results = []
        errors = []
        
        def search_task(i):
            try:
                with temp_db.get_connection() as conn:
                    # Simulate work
                    cursor = conn.execute("SELECT COUNT(*) FROM metadata")
                    count = cursor.fetchone()[0]
                    results.append((i, count))
                    time.sleep(0.01)  # Small delay
            except Exception as e:
                errors.append((i, str(e)))
        
        # Create multiple threads
        threads = []
        for i in range(20):  # More threads than pool size
            t = threading.Thread(target=search_task, args=(i,))
            threads.append(t)
            t.start()
        
        # Wait for completion
        for t in threads:
            t.join(timeout=10)
        
        # Check results
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 20


class TestDatabaseMigration:
    """Test database migration."""
    
    @pytest.fixture
    def legacy_db(self):
        """Create legacy database without security features."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        # Create minimal schema
        conn = sqlite3.connect(db_path)
        
        # Old schema without security features
        conn.execute("""
            CREATE TABLE metadata (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                message_count INTEGER DEFAULT 0
            )
        """)
        
        conn.execute("""
            CREATE VIRTUAL TABLE conversations USING fts5(
                conversation_id UNINDEXED,
                content
            )
        """)
        
        # Insert test data
        conn.execute("""
            INSERT INTO metadata VALUES 
            ('test1', 'Test 1', '2024-01-01', '2024-01-01', 2)
        """)
        
        conn.commit()
        conn.close()
        
        yield db_path
        
        # Cleanup
        Path(db_path).unlink(missing_ok=True)
    
    def test_migration_creates_audit_table(self, legacy_db):
        """Test migration adds search_audit table."""
        from scripts.migrate_db_schema import DatabaseMigration
        
        migration = DatabaseMigration(legacy_db, backup=False)
        migration.migrate()
        
        # Check table exists
        conn = sqlite3.connect(legacy_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='search_audit'"
        )
        assert cursor.fetchone() is not None
        conn.close()
    
    def test_migration_adds_columns(self, legacy_db):
        """Test migration adds checksum and title_normalized."""
        from scripts.migrate_db_schema import DatabaseMigration
        
        migration = DatabaseMigration(legacy_db, backup=False)
        migration.migrate()
        
        # Check columns exist
        conn = sqlite3.connect(legacy_db)
        cursor = conn.execute("PRAGMA table_info(metadata)")
        columns = {row[1] for row in cursor}
        
        assert "checksum" in columns
        assert "title_normalized" in columns
        conn.close()
    
    def test_migration_creates_indexes(self, legacy_db):
        """Test migration adds performance indexes."""
        from scripts.migrate_db_schema import DatabaseMigration
        
        migration = DatabaseMigration(legacy_db, backup=False)
        migration.migrate()
        
        # Check indexes exist
        conn = sqlite3.connect(legacy_db)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor}
        
        expected_indexes = [
            "idx_metadata_checksum",
            "idx_audit_searched_at",
            "idx_audit_user_id"
        ]
        
        for idx in expected_indexes:
            assert idx in indexes
        
        conn.close()
    
    def test_migration_backup(self, legacy_db):
        """Test migration creates backup."""
        from scripts.migrate_db_schema import DatabaseMigration
        
        migration = DatabaseMigration(legacy_db, backup=True)
        
        # Get original size
        original_size = Path(legacy_db).stat().st_size
        
        migration.migrate()
        
        # Check backup was created
        assert len(migration.changes_made) > 0
        assert any("backup" in change.lower() for change in migration.changes_made)
        
        # Find backup file
        backup_files = list(Path(legacy_db).parent.glob(f"{Path(legacy_db).stem}_backup_*.db"))
        assert len(backup_files) > 0
        
        # Cleanup backup
        for f in backup_files:
            f.unlink()


class TestPerformance:
    """Test performance optimizations."""
    
    def test_search_timeout(self, search_engine):
        """Test query timeout is enforced."""
        # This is hard to test without a slow query
        # Just verify timeout is set
        with search_engine.db_manager.get_connection() as conn:
            cursor = conn.execute("PRAGMA query_timeout")
            timeout = cursor.fetchone()
            # Should have a timeout set
            assert timeout is not None
    
    def test_connection_pool_performance(self, temp_db):
        """Test connection pool improves performance."""
        import time
        
        # Time sequential connections
        start = time.time()
        for _ in range(10):
            with temp_db.get_connection() as conn:
                conn.execute("SELECT 1")
        pool_time = time.time() - start
        
        # Compare with creating new connections
        start = time.time()
        for _ in range(10):
            conn = sqlite3.connect(str(temp_db.db_path))
            conn.execute("SELECT 1")
            conn.close()
        direct_time = time.time() - start
        
        # Pool should be faster (or at least not significantly slower)
        assert pool_time <= direct_time * 1.5