"""Secure search implementation with SQL injection prevention and enhanced features."""
import re
import logging
import hashlib
import json
import html
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from functools import lru_cache
import redis
from redis.exceptions import RedisError
from .database_enhanced import DatabaseManager

logger = logging.getLogger(__name__)


class SearchConstants:
    """Configuration constants for search functionality."""
    MAX_QUERY_LENGTH = 500
    MIN_QUERY_LENGTH = 1
    CACHE_TTL_SECONDS = 300  # 5 minutes
    MAX_RESULTS_PER_PAGE = 100
    DEFAULT_RESULTS_PER_PAGE = 20
    CONNECTION_TIMEOUT = 5.0
    AUDIT_LOG_RETENTION_DAYS = 90
    
    # FTS5 special characters that need escaping
    FTS5_SPECIAL_CHARS = ['"', "'", '(', ')', '*', ':', '^']
    
    # Boolean operators to preserve
    BOOLEAN_OPERATORS = ['AND', 'OR', 'NOT', 'NEAR']


class SecureSearchEngine:
    """Secure search engine with injection prevention and caching."""
    
    def __init__(self, db_manager: DatabaseManager, redis_client: Optional[redis.Redis] = None):
        """Initialize secure search engine."""
        self.db = db_manager
        self.redis_client = redis_client
        self._init_cache()
    
    def _init_cache(self):
        """Initialize Redis cache connection."""
        if not self.redis_client:
            try:
                self.redis_client = redis.Redis(
                    host='localhost', 
                    port=6379, 
                    db=0,
                    decode_responses=True,
                    socket_connect_timeout=SearchConstants.CONNECTION_TIMEOUT,
                    socket_timeout=SearchConstants.CONNECTION_TIMEOUT
                )
                self.redis_client.ping()
                logger.info("Redis cache initialized successfully")
            except (RedisError, Exception) as e:
                logger.warning(f"Redis not available, caching disabled: {e}")
                self.redis_client = None
    
    def search(self, query: str, limit: int = 20, offset: int = 0, 
               user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute secure search with validation and caching.
        
        Args:
            query: Search query string
            limit: Maximum results to return
            offset: Results offset for pagination
            user_id: Optional user ID for audit logging
            
        Returns:
            Dictionary with search results and metadata
        """
        start_time = datetime.now()
        
        # Validate and sanitize inputs
        query = self._validate_query(query)
        limit = min(max(1, limit), SearchConstants.MAX_RESULTS_PER_PAGE)
        offset = max(0, offset)
        
        # Check cache first
        cache_key = self._get_cache_key(query, limit, offset)
        cached_result = self._get_cached_result(cache_key)
        if cached_result:
            logger.debug(f"Cache hit for query: {self._hash_query(query)}")
            self._log_search_audit(query, user_id, True, start_time)
            return cached_result
        
        # Process query for FTS5
        processed_query = self._process_secure_query(query)
        
        try:
            # Execute search with timeout
            results = self.db.search_conversations(
                processed_query, 
                limit, 
                offset,
                timeout=SearchConstants.CONNECTION_TIMEOUT
            )
            
            # Sanitize snippets to prevent XSS
            for result in results:
                if 'snippet' in result:
                    result['snippet'] = self._sanitize_snippet(result['snippet'])
            
            # Count total results
            total_count = self._count_results(processed_query)
            
            response = {
                'query': query,
                'processed_query': processed_query,
                'results': results,
                'count': len(results),
                'total': total_count,
                'limit': limit,
                'offset': offset,
                'has_more': (offset + len(results)) < total_count,
                'cached': False
            }
            
            # Cache the result
            self._cache_result(cache_key, response)
            
            # Log audit
            self._log_search_audit(query, user_id, False, start_time)
            
            return response
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            self._log_search_audit(query, user_id, False, start_time, str(e))
            return {
                'query': query,
                'error': 'Search failed. Please try again.',
                'results': [],
                'count': 0,
                'total': 0,
                'cached': False
            }
    
    def _validate_query(self, query: str) -> str:
        """
        Validate and sanitize search query.
        
        - Remove null bytes
        - Enforce length limits
        - Strip whitespace
        """
        if not query:
            raise ValueError("Query cannot be empty")
        
        # Remove null bytes
        query = query.replace('\x00', '')
        
        # Strip whitespace
        query = query.strip()
        
        # Check length
        if len(query) < SearchConstants.MIN_QUERY_LENGTH:
            raise ValueError(f"Query too short (min {SearchConstants.MIN_QUERY_LENGTH} chars)")
        
        if len(query) > SearchConstants.MAX_QUERY_LENGTH:
            raise ValueError(f"Query too long (max {SearchConstants.MAX_QUERY_LENGTH} chars)")
        
        return query
    
    def _process_secure_query(self, query: str) -> str:
        """
        Process query for secure FTS5 search.
        
        - Escape special characters
        - Preserve boolean operators
        - Handle phrase searches
        """
        # Check if query has boolean operators
        has_operators = any(
            f' {op} ' in query.upper() 
            for op in SearchConstants.BOOLEAN_OPERATORS
        )
        
        # Check if query is a phrase search (has quotes)
        is_phrase = '"' in query
        
        if is_phrase:
            # Escape special chars within phrases
            return self._escape_phrase_query(query)
        elif has_operators:
            # Process boolean query
            return self._process_boolean_query(query)
        else:
            # Simple query - escape and quote
            escaped = self._escape_fts5_chars(query)
            words = escaped.split()
            if len(words) > 1:
                return f'"{escaped}"'
            return escaped
    
    def _escape_fts5_chars(self, text: str) -> str:
        """Escape FTS5 special characters."""
        for char in SearchConstants.FTS5_SPECIAL_CHARS:
            if char != '"':  # Don't escape quotes in phrase searches
                text = text.replace(char, f'\\{char}')
        return text
    
    def _escape_phrase_query(self, query: str) -> str:
        """Handle phrase searches with proper escaping."""
        # Split by quotes to find phrases
        parts = query.split('"')
        result = []
        
        for i, part in enumerate(parts):
            if i % 2 == 1:  # Inside quotes
                # Escape special chars except quotes
                escaped = self._escape_fts5_chars(part)
                result.append(f'"{escaped}"')
            else:  # Outside quotes
                if part.strip():
                    result.append(self._escape_fts5_chars(part))
        
        return ' '.join(result)
    
    def _process_boolean_query(self, query: str) -> str:
        """Process query with boolean operators."""
        # Preserve operators while escaping other parts
        pattern = r'\b(' + '|'.join(SearchConstants.BOOLEAN_OPERATORS) + r')\b'
        parts = re.split(pattern, query, flags=re.IGNORECASE)
        
        result = []
        for part in parts:
            if part.upper() in SearchConstants.BOOLEAN_OPERATORS:
                result.append(part.upper())
            else:
                escaped = self._escape_fts5_chars(part.strip())
                if escaped:
                    result.append(escaped)
        
        return ' '.join(result)
    
    def _sanitize_snippet(self, snippet: str) -> str:
        """Sanitize snippet to prevent XSS attacks."""
        # First HTML escape everything
        safe = html.escape(snippet)
        
        # Then restore our highlight markers
        safe = safe.replace('&lt;mark&gt;', '<mark>')
        safe = safe.replace('&lt;/mark&gt;', '</mark>')
        
        return safe
    
    def _get_cache_key(self, query: str, limit: int, offset: int) -> str:
        """Generate cache key for query."""
        key_data = f"{query}:{limit}:{offset}"
        key_hash = hashlib.sha256(key_data.encode()).hexdigest()
        return f"search:v1:{key_hash}"
    
    def _get_cached_result(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Get cached search result."""
        if not self.redis_client:
            return None
        
        try:
            cached = self.redis_client.get(cache_key)
            if cached:
                result = json.loads(cached)
                result['cached'] = True
                return result
        except (RedisError, json.JSONDecodeError) as e:
            logger.warning(f"Cache retrieval error: {e}")
        
        return None
    
    def _cache_result(self, cache_key: str, result: Dict[str, Any]):
        """Cache search result."""
        if not self.redis_client:
            return
        
        try:
            # Don't cache error results
            if 'error' not in result:
                self.redis_client.setex(
                    cache_key,
                    SearchConstants.CACHE_TTL_SECONDS,
                    json.dumps(result)
                )
        except (RedisError, json.JSONEncodeError) as e:
            logger.warning(f"Cache storage error: {e}")
    
    def _count_results(self, processed_query: str) -> int:
        """Count total results for a query."""
        try:
            return self.db.count_search_results(
                processed_query,
                timeout=SearchConstants.CONNECTION_TIMEOUT
            )
        except Exception as e:
            logger.error(f"Error counting results: {e}")
            return 0
    
    def _hash_query(self, query: str) -> str:
        """Hash query for privacy in logs."""
        return hashlib.sha256(query.encode()).hexdigest()[:16]
    
    def _log_search_audit(self, query: str, user_id: Optional[str], 
                         from_cache: bool, start_time: datetime, 
                         error: Optional[str] = None):
        """Log search audit trail."""
        try:
            duration_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            self.db.log_search_audit(
                query_hash=self._hash_query(query),
                user_id=user_id,
                query_length=len(query),
                from_cache=from_cache,
                duration_ms=duration_ms,
                error=error
            )
        except Exception as e:
            logger.error(f"Failed to log search audit: {e}")
    
    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get full conversation by ID with sanitization."""
        result = self.db.get_conversation(conversation_id)
        
        if result and 'messages' in result:
            # Sanitize message content
            for msg in result['messages']:
                if 'content' in msg:
                    msg['content'] = html.escape(msg['content'])
        
        return result
    
    def suggest_queries(self, partial_query: str, user_id: Optional[str] = None) -> List[str]:
        """
        Suggest queries based on partial input with validation.
        """
        try:
            # Validate partial query
            partial_query = self._validate_query(partial_query)
        except ValueError:
            return []
        
        # Escape special chars for LIKE query
        escaped = partial_query.replace('%', '\\%').replace('_', '\\_')
        
        suggestions = self.db.get_query_suggestions(escaped, limit=5)
        
        # Log suggestion request
        if user_id:
            logger.debug(f"Query suggestions requested by {user_id}")
        
        return suggestions
    
    def clear_cache(self, pattern: Optional[str] = None):
        """Clear search cache."""
        if not self.redis_client:
            return
        
        try:
            if pattern:
                # Clear specific pattern
                keys = self.redis_client.keys(f"search:v1:*{pattern}*")
                if keys:
                    self.redis_client.delete(*keys)
                    logger.info(f"Cleared {len(keys)} cache entries")
            else:
                # Clear all search cache
                keys = self.redis_client.keys("search:v1:*")
                if keys:
                    self.redis_client.delete(*keys)
                    logger.info(f"Cleared all {len(keys)} cache entries")
        except RedisError as e:
            logger.error(f"Failed to clear cache: {e}")
    
    def cleanup_old_audits(self):
        """Clean up old audit logs."""
        cutoff_date = datetime.now() - timedelta(days=SearchConstants.AUDIT_LOG_RETENTION_DAYS)
        deleted = self.db.cleanup_search_audits(cutoff_date)
        logger.info(f"Cleaned up {deleted} old audit entries")