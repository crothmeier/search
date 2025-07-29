"""
Security module for ChatGPT Search API.

Provides comprehensive security features including:
- JWT authentication with role-based access control
- Password hashing and verification
- Rate limiting with Redis backend
- Security headers middleware
- API key authentication for service accounts
- CORS validation
- Input validation helpers
- Request tracing
- Token blacklisting
"""

import os
import re
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Set, Union
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request, Response, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
import redis.asyncio as redis
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel, Field, EmailStr, validator
import uuid

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Security constants
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
API_KEY_HEADER = "X-API-Key"
REQUEST_ID_HEADER = "X-Request-ID"

# CORS settings
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",")
ALLOWED_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS]

# Rate limiting configuration
RATE_LIMIT_DEFAULT = os.getenv("RATE_LIMIT_DEFAULT", "100/minute")
RATE_LIMIT_AUTH = os.getenv("RATE_LIMIT_AUTH", "10/minute")

# Initialize security components
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# Redis client for rate limiting and token blacklisting
redis_client: Optional[redis.Redis] = None

# Rate limiter
limiter = Limiter(key_func=get_remote_address)


# Pydantic models
class UserRole(BaseModel):
    """User role with associated scopes."""
    name: str
    scopes: List[str] = Field(default_factory=list)


class User(BaseModel):
    """User model with role-based access control."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    username: str
    email: EmailStr
    is_active: bool = True
    is_service_account: bool = False
    role: str = "user"
    scopes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    @field_validator("scopes", pre=True, always=True)
    def set_scopes_based_on_role(cls, v, values):
        """Set default scopes based on role."""
        role = values.get("role", "user")
        if not v:  # If scopes not explicitly provided
            role_scopes = {
                "admin": ["read", "write", "admin"],
                "editor": ["read", "write"],
                "user": ["read"],
                "service": ["read", "write", "service"]
            }
            return role_scopes.get(role, ["read"])
        return v


class TokenData(BaseModel):
    """Token data extracted from JWT."""
    username: Optional[str] = None
    user_id: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)
    is_service_account: bool = False


class TokenBlacklist(BaseModel):
    """Token blacklist entry."""
    jti: str  # JWT ID
    exp: datetime
    blacklisted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Utility functions
async def get_redis_client() -> redis.Redis:
    """Get Redis client instance."""
    global redis_client
    if redis_client is None:
        redis_client = await redis.from_url(REDIS_URL, decode_responses=True)
    return redis_client


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.
    
    Args:
        data: Dictionary containing user data
        expires_delta: Optional custom expiration time
        
    Returns:
        Encoded JWT token
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid.uuid4())  # JWT ID for blacklisting
    })
    
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict) -> str:
    """
    Create a JWT refresh token with longer expiration.
    
    Args:
        data: Dictionary containing user data
        
    Returns:
        Encoded JWT refresh token
    """
    expires_delta = timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    return create_access_token(data, expires_delta)


async def decode_token(token: str) -> TokenData:
    """
    Decode and validate a JWT token.
    
    Args:
        token: JWT token string
        
    Returns:
        TokenData object
        
    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Check if token is blacklisted
        jti = payload.get("jti")
        if jti:
            redis_client = await get_redis_client()
            if await redis_client.exists(f"blacklist:{jti}"):
                logger.warning(f"Attempted use of blacklisted token: {jti}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked"
                )
        
        username: str = payload.get("sub")
        user_id: str = payload.get("user_id")
        scopes: List[str] = payload.get("scopes", [])
        is_service_account: bool = payload.get("is_service_account", False)
        
        if username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials"
            )
            
        return TokenData(
            username=username,
            user_id=user_id,
            scopes=scopes,
            is_service_account=is_service_account
        )
        
    except JWTError as e:
        logger.error(f"JWT decode error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )


async def blacklist_token(token: str) -> None:
    """
    Add a token to the blacklist.
    
    Args:
        token: JWT token to blacklist
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        exp = payload.get("exp")
        
        if jti and exp:
            redis_client = await get_redis_client()
            # Set expiration to match token expiration
            ttl = exp - datetime.now(timezone.utc).timestamp()
            if ttl > 0:
                await redis_client.setex(f"blacklist:{jti}", int(ttl), "1")
                logger.info(f"Token blacklisted: {jti}")
                
    except JWTError:
        pass  # Invalid token, no need to blacklist


# Dependency functions
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> TokenData:
    """
    Get current user from JWT token.
    
    Args:
        credentials: HTTP Bearer credentials
        
    Returns:
        TokenData object
    """
    return await decode_token(credentials.credentials)


def require_scopes(*required_scopes: str):
    """
    Create a dependency that requires specific scopes.
    
    Args:
        required_scopes: Variable number of required scopes
        
    Returns:
        Dependency function
    """
    async def verify_scopes(current_user: TokenData = Depends(get_current_user)):
        if not any(scope in current_user.scopes for scope in required_scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user
    return verify_scopes


async def verify_api_key(request: Request) -> Optional[str]:
    """
    Verify API key for service-to-service authentication.
    
    Args:
        request: FastAPI request object
        
    Returns:
        API key if valid, None otherwise
    """
    api_key = request.headers.get(API_KEY_HEADER)
    if not api_key:
        return None
        
    # Validate API key against stored keys in Redis
    redis_client = await get_redis_client()
    key_data = await redis_client.get(f"api_key:{api_key}")
    
    if not key_data:
        logger.warning(f"Invalid API key attempt from {get_remote_address(request)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
        
    return api_key


# Middleware functions
async def security_headers_middleware(request: Request, call_next):
    """
    Add security headers to all responses.
    
    Args:
        request: FastAPI request
        call_next: Next middleware/handler
        
    Returns:
        Response with security headers
    """
    response = await call_next(request)
    
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    
    return response


async def request_id_middleware(request: Request, call_next):
    """
    Add request ID for tracing.
    
    Args:
        request: FastAPI request
        call_next: Next middleware/handler
        
    Returns:
        Response with request ID header
    """
    request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
    
    # Store request ID in request state for logging
    request.state.request_id = request_id
    
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    
    return response


def validate_cors_origin(origin: str) -> bool:
    """
    Validate CORS origin against allowed origins.
    
    Args:
        origin: Origin header value
        
    Returns:
        True if origin is allowed, False otherwise
    """
    if not origin:
        return False
        
    # Exact match
    if origin in ALLOWED_ORIGINS:
        return True
        
    # Pattern matching for subdomains
    for allowed in ALLOWED_ORIGINS:
        if allowed.startswith("*."):
            domain = allowed[2:]
            if origin.endswith(domain):
                return True
                
    return False


# Input validation helpers
def validate_password_strength(password: str) -> bool:
    """
    Validate password meets security requirements.
    
    Args:
        password: Password to validate
        
    Returns:
        True if password is strong enough
        
    Raises:
        HTTPException: If password is weak
    """
    if len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters long"
        )
        
    if not re.search(r"[A-Z]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one uppercase letter"
        )
        
    if not re.search(r"[a-z]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one lowercase letter"
        )
        
    if not re.search(r"\d", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one digit"
        )
        
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one special character"
        )
        
    return True


def sanitize_input(input_string: str, max_length: int = 1000) -> str:
    """
    Sanitize user input to prevent injection attacks.
    
    Args:
        input_string: String to sanitize
        max_length: Maximum allowed length
        
    Returns:
        Sanitized string
    """
    if not input_string:
        return ""
        
    # Truncate to max length
    input_string = input_string[:max_length]
    
    # Remove null bytes
    input_string = input_string.replace("\x00", "")
    
    # Remove control characters except newlines and tabs
    input_string = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]", "", input_string)
    
    return input_string.strip()


# Setup functions
def setup_security(app):
    """
    Setup security middleware and handlers for FastAPI app.
    
    Args:
        app: FastAPI application instance
    """
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER]
    )
    
    # Security headers
    app.middleware("http")(security_headers_middleware)
    
    # Request ID for tracing
    app.middleware("http")(request_id_middleware)
    
    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    
    logger.info("Security middleware configured")


# API Key management functions
async def create_api_key(name: str, scopes: List[str], expires_in_days: int = 365) -> str:
    """
    Create a new API key for service accounts.
    
    Args:
        name: Name/description for the API key
        scopes: List of allowed scopes
        expires_in_days: Days until expiration
        
    Returns:
        Generated API key
    """
    api_key = secrets.token_urlsafe(32)
    
    redis_client = await get_redis_client()
    key_data = {
        "name": name,
        "scopes": scopes,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
    }
    
    # Store with expiration
    await redis_client.setex(
        f"api_key:{api_key}",
        expires_in_days * 86400,
        str(key_data)
    )
    
    logger.info(f"API key created for: {name}")
    return api_key


async def revoke_api_key(api_key: str) -> bool:
    """
    Revoke an API key.
    
    Args:
        api_key: API key to revoke
        
    Returns:
        True if revoked, False if not found
    """
    redis_client = await get_redis_client()
    result = await redis_client.delete(f"api_key:{api_key}")
    
    if result:
        logger.info(f"API key revoked: {api_key[:8]}...")
        
    return bool(result)


# Context managers
@asynccontextmanager
async def redis_connection():
    """Context manager for Redis connection."""
    client = await get_redis_client()
    try:
        yield client
    finally:
        # Connection pooling handles cleanup
        pass