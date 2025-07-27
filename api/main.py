"""FastAPI application for ChatGPT conversation search."""
import os
import sys
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, generate_latest
from dotenv import load_dotenv

from src.database import DatabaseManager
from src.search import SearchEngine

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Metrics
search_counter = Counter('search_requests_total', 'Total number of search requests')
search_duration = Histogram('search_duration_seconds', 'Search request duration')
api_errors = Counter('api_errors_total', 'Total number of API errors')

# Global instances
db_manager: Optional[DatabaseManager] = None
search_engine: Optional[SearchEngine] = None


# Pydantic models
class SearchResponse(BaseModel):
    """Search response model."""
    query: str
    processed_query: Optional[str] = None
    results: list[Dict[str, Any]]
    count: int
    total: int
    limit: int
    offset: int
    has_more: bool
    error: Optional[str] = None


class ConversationResponse(BaseModel):
    """Conversation response model."""
    conversation_id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str
    messages: list[Dict[str, Any]]


class StatsResponse(BaseModel):
    """Database statistics response."""
    total_conversations: int
    total_messages: int
    database_size_mb: float
    last_import: Optional[Dict[str, Any]] = None
    date_range: Optional[Dict[str, str]] = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    timestamp: str
    database: str
    version: str = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global db_manager, search_engine
    
    # Startup
    db_path = os.getenv('DATABASE_PATH', 'data/conversations.db')
    logger.info(f"Initializing database at {db_path}")
    
    db_manager = DatabaseManager(db_path)
    search_engine = SearchEngine(db_manager)
    
    logger.info("API startup complete")
    
    yield
    
    # Shutdown
    logger.info("API shutdown")


# Create FastAPI app
app = FastAPI(
    title="ChatGPT Search API",
    description="Search through ChatGPT conversation history",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    try:
        # Check database connection
        stats = db_manager.get_stats()
        db_status = "healthy" if stats.get('total_conversations', 0) >= 0 else "unhealthy"
        
        return HealthResponse(
            status="healthy" if db_status == "healthy" else "degraded",
            timestamp=datetime.now().isoformat(),
            database=db_status
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        api_errors.inc()
        raise HTTPException(status_code=503, detail="Service unavailable")


@app.get("/search", response_model=SearchResponse)
async def search_conversations(
    q: str = Query(..., description="Search query", min_length=1),
    limit: int = Query(20, ge=1, le=100, description="Maximum results to return"),
    offset: int = Query(0, ge=0, description="Results offset for pagination")
):
    """
    Search conversations using full-text search.
    
    Supports:
    - Simple text queries
    - Phrase searches with quotes
    - Boolean operators (AND, OR, NOT)
    """
    search_counter.inc()
    
    try:
        with search_duration.time():
            results = search_engine.search(q, limit, offset)
        
        return SearchResponse(**results)
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        api_errors.inc()
        return SearchResponse(
            query=q,
            error=str(e),
            results=[],
            count=0,
            total=0,
            limit=limit,
            offset=offset,
            has_more=False
        )


@app.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: str):
    """Get full conversation by ID."""
    try:
        conversation = search_engine.get_conversation(conversation_id)
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return ConversationResponse(**conversation)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching conversation: {e}")
        api_errors.inc()
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/stats", response_model=StatsResponse)
async def get_statistics():
    """Get database statistics."""
    try:
        stats = db_manager.get_stats()
        return StatsResponse(**stats)
        
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        api_errors.inc()
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint."""
    return JSONResponse(
        content=generate_latest().decode('utf-8'),
        media_type="text/plain"
    )


@app.get("/suggest")
async def suggest_queries(
    q: str = Query(..., description="Partial query for suggestions", min_length=1)
):
    """Get query suggestions based on partial input."""
    try:
        suggestions = search_engine.suggest_queries(q)
        return {"query": q, "suggestions": suggestions}
        
    except Exception as e:
        logger.error(f"Error getting suggestions: {e}")
        api_errors.inc()
        return {"query": q, "suggestions": []}


if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv('API_HOST', '0.0.0.0')
    port = int(os.getenv('API_PORT', '8000'))
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=True,
        log_level=os.getenv('LOG_LEVEL', 'info').lower()
    )