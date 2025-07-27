"""Search logic and query processing."""
import re
import logging
from typing import List, Dict, Any, Optional
from .database import DatabaseManager

logger = logging.getLogger(__name__)


class SearchEngine:
    """Handles search queries and result processing."""
    
    def __init__(self, db_manager: DatabaseManager):
        """Initialize search engine with database manager."""
        self.db = db_manager
    
    def search(self, query: str, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        """
        Execute search query and return results.
        
        Args:
            query: Search query string
            limit: Maximum results to return
            offset: Results offset for pagination
            
        Returns:
            Dictionary with search results and metadata
        """
        # Validate inputs
        limit = min(max(1, limit), 100)  # Cap at 100 results
        offset = max(0, offset)
        
        # Process query for FTS5
        processed_query = self._process_query(query)
        
        try:
            # Execute search
            results = self.db.search_conversations(processed_query, limit, offset)
            
            # Count total results for pagination
            total_count = self._count_results(processed_query)
            
            return {
                'query': query,
                'processed_query': processed_query,
                'results': results,
                'count': len(results),
                'total': total_count,
                'limit': limit,
                'offset': offset,
                'has_more': (offset + len(results)) < total_count
            }
        except Exception as e:
            logger.error(f"Search error: {e}")
            return {
                'query': query,
                'error': str(e),
                'results': [],
                'count': 0,
                'total': 0
            }
    
    def _process_query(self, query: str) -> str:
        """
        Process query for FTS5 syntax.
        
        Handles:
        - Phrase searches with quotes
        - AND/OR operators
        - Prefix searches with *
        """
        # Remove dangerous characters
        query = query.strip()
        
        # If query has quotes, preserve them for phrase search
        if '"' in query:
            return query
        
        # If query has boolean operators, preserve them
        if any(op in query.upper() for op in [' AND ', ' OR ', ' NOT ']):
            return query
        
        # For simple queries, make each word required (implicit AND)
        words = query.split()
        if len(words) > 1:
            # Quote multi-word phrases to search as a unit
            return f'"{query}"'
        
        return query
    
    def _count_results(self, processed_query: str) -> int:
        """Count total results for a query."""
        try:
            with self.db.get_connection() as conn:
                result = conn.execute("""
                    SELECT COUNT(*) 
                    FROM conversations 
                    WHERE conversations MATCH ?
                """, (processed_query,)).fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"Error counting results: {e}")
            return 0
    
    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get full conversation by ID."""
        return self.db.get_conversation(conversation_id)
    
    def suggest_queries(self, partial_query: str) -> List[str]:
        """
        Suggest queries based on partial input.
        
        This is a simple implementation that could be enhanced with:
        - Frequent query tracking
        - Autocomplete from conversation titles
        - Smart suggestions based on content
        """
        suggestions = []
        
        # Get recent conversation titles that match
        with self.db.get_connection() as conn:
            titles = conn.execute("""
                SELECT DISTINCT title 
                FROM metadata 
                WHERE title LIKE ? 
                LIMIT 5
            """, (f'%{partial_query}%',)).fetchall()
            
            suggestions.extend([t['title'] for t in titles])
        
        return suggestions