"""
Test suite for security module.

Tests JWT authentication, password hashing, rate limiting,
CORS validation, and other security features.
"""

import os
import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, AsyncMock

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from jose import jwt
import redis.asyncio as redis

# Import security module
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.security import (
    hash_password, verify_password, create_access_token, create_refresh_token,
    decode_token, blacklist_token, get_current_user, require_scopes,
    verify_api_key, validate_cors_origin, validate_password_strength,
    sanitize_input, setup_security, create_api_key, revoke_api_key,
    User, TokenData, get_redis_client, security_headers_middleware,
    request_id_middleware, SECRET_KEY, ALGORITHM
)


# Test fixtures
@pytest.fixture
def test_app():
    """Create test FastAPI app."""
    app = FastAPI()
    setup_security(app)
    return app


@pytest.fixture
def test_client(test_app):
    """Create test client."""
    return TestClient(test_app)


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.setex = AsyncMock(return_value=True)
    mock.exists = AsyncMock(return_value=False)
    mock.delete = AsyncMock(return_value=1)
    return mock


@pytest.fixture
def test_user():
    """Create test user."""
    return User(
        username="testuser",
        email="test@example.com",
        role="user",
        scopes=["read"]
    )


@pytest.fixture
def admin_user():
    """Create admin user."""
    return User(
        username="admin",
        email="admin@example.com",
        role="admin",
        scopes=["read", "write", "admin"]
    )


# Password hashing tests
class TestPasswordHashing:
    """Test password hashing functionality."""
    
    def test_hash_password(self):
        """Test password hashing."""
        password = "SecurePass123!"
        hashed = hash_password(password)
        
        assert hashed != password
        assert len(hashed) > 20
        assert hashed.startswith("$2b$")
    
    def test_verify_password_correct(self):
        """Test password verification with correct password."""
        password = "SecurePass123!"
        hashed = hash_password(password)
        
        assert verify_password(password, hashed) is True
    
    def test_verify_password_incorrect(self):
        """Test password verification with incorrect password."""
        password = "SecurePass123!"
        hashed = hash_password(password)
        
        assert verify_password("WrongPassword", hashed) is False
    
    def test_password_validation_strong(self):
        """Test strong password validation."""
        assert validate_password_strength("SecurePass123!") is True
    
    def test_password_validation_too_short(self):
        """Test password validation with too short password."""
        with pytest.raises(HTTPException) as exc_info:
            validate_password_strength("Pass1!")
        assert exc_info.value.status_code == 400
        assert "at least 8 characters" in exc_info.value.detail
    
    def test_password_validation_no_uppercase(self):
        """Test password validation without uppercase."""
        with pytest.raises(HTTPException) as exc_info:
            validate_password_strength("securepass123!")
        assert exc_info.value.status_code == 400
        assert "uppercase letter" in exc_info.value.detail
    
    def test_password_validation_no_lowercase(self):
        """Test password validation without lowercase."""
        with pytest.raises(HTTPException) as exc_info:
            validate_password_strength("SECUREPASS123!")
        assert exc_info.value.status_code == 400
        assert "lowercase letter" in exc_info.value.detail
    
    def test_password_validation_no_digit(self):
        """Test password validation without digit."""
        with pytest.raises(HTTPException) as exc_info:
            validate_password_strength("SecurePass!")
        assert exc_info.value.status_code == 400
        assert "one digit" in exc_info.value.detail
    
    def test_password_validation_no_special(self):
        """Test password validation without special character."""
        with pytest.raises(HTTPException) as exc_info:
            validate_password_strength("SecurePass123")
        assert exc_info.value.status_code == 400
        assert "special character" in exc_info.value.detail


# JWT token tests
class TestJWTTokens:
    """Test JWT token functionality."""
    
    def test_create_access_token(self, test_user):
        """Test access token creation."""
        token_data = {
            "sub": test_user.username,
            "user_id": test_user.id,
            "scopes": test_user.scopes
        }
        
        token = create_access_token(token_data)
        
        # Decode to verify
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        assert payload["sub"] == test_user.username
        assert payload["user_id"] == test_user.id
        assert payload["scopes"] == test_user.scopes
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload
    
    def test_create_refresh_token(self, test_user):
        """Test refresh token creation."""
        token_data = {
            "sub": test_user.username,
            "user_id": test_user.id,
            "scopes": test_user.scopes
        }
        
        token = create_refresh_token(token_data)
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Check longer expiration
        exp_time = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        iat_time = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        delta = exp_time - iat_time
        
        assert delta.days >= 6  # Should be 7 days by default
    
    @pytest.mark.asyncio
    async def test_decode_token_valid(self, test_user, mock_redis):
        """Test decoding valid token."""
        with patch("api.security.get_redis_client", return_value=mock_redis):
            token_data = {
                "sub": test_user.username,
                "user_id": test_user.id,
                "scopes": test_user.scopes,
                "is_service_account": False
            }
            
            token = create_access_token(token_data)
            decoded = await decode_token(token)
            
            assert decoded.username == test_user.username
            assert decoded.user_id == test_user.id
            assert decoded.scopes == test_user.scopes
            assert decoded.is_service_account is False
    
    @pytest.mark.asyncio
    async def test_decode_token_expired(self):
        """Test decoding expired token."""
        token_data = {"sub": "testuser"}
        token = create_access_token(token_data, timedelta(seconds=-1))
        
        with pytest.raises(HTTPException) as exc_info:
            await decode_token(token)
        
        assert exc_info.value.status_code == 401
    
    @pytest.mark.asyncio
    async def test_decode_token_blacklisted(self, mock_redis):
        """Test decoding blacklisted token."""
        mock_redis.exists = AsyncMock(return_value=True)
        
        with patch("api.security.get_redis_client", return_value=mock_redis):
            token_data = {"sub": "testuser"}
            token = create_access_token(token_data)
            
            with pytest.raises(HTTPException) as exc_info:
                await decode_token(token)
            
            assert exc_info.value.status_code == 401
            assert "revoked" in exc_info.value.detail
    
    @pytest.mark.asyncio
    async def test_blacklist_token(self, mock_redis):
        """Test token blacklisting."""
        with patch("api.security.get_redis_client", return_value=mock_redis):
            token_data = {"sub": "testuser"}
            token = create_access_token(token_data)
            
            await blacklist_token(token)
            
            mock_redis.setex.assert_called_once()
            args = mock_redis.setex.call_args[0]
            assert args[0].startswith("blacklist:")
            assert args[2] == "1"


# Authentication dependency tests
class TestAuthDependencies:
    """Test authentication dependencies."""
    
    @pytest.mark.asyncio
    async def test_require_scopes_allowed(self, test_user):
        """Test scope requirement with allowed scope."""
        mock_token_data = TokenData(
            username=test_user.username,
            user_id=test_user.id,
            scopes=["read", "write"]
        )
        
        verify_func = require_scopes("read")
        result = await verify_func(mock_token_data)
        
        assert result == mock_token_data
    
    @pytest.mark.asyncio
    async def test_require_scopes_forbidden(self, test_user):
        """Test scope requirement with forbidden scope."""
        mock_token_data = TokenData(
            username=test_user.username,
            user_id=test_user.id,
            scopes=["read"]
        )
        
        verify_func = require_scopes("admin")
        
        with pytest.raises(HTTPException) as exc_info:
            await verify_func(mock_token_data)
        
        assert exc_info.value.status_code == 403
        assert "Insufficient permissions" in exc_info.value.detail
    
    @pytest.mark.asyncio
    async def test_verify_api_key_valid(self, mock_redis):
        """Test API key verification with valid key."""
        mock_redis.get = AsyncMock(return_value='{"name": "test", "scopes": ["read"]}')
        
        with patch("api.security.get_redis_client", return_value=mock_redis):
            request = Mock()
            request.headers = {"X-API-Key": "test_api_key"}
            
            result = await verify_api_key(request)
            assert result == "test_api_key"
    
    @pytest.mark.asyncio
    async def test_verify_api_key_invalid(self, mock_redis):
        """Test API key verification with invalid key."""
        mock_redis.get = AsyncMock(return_value=None)
        
        with patch("api.security.get_redis_client", return_value=mock_redis):
            request = Mock()
            request.headers = {"X-API-Key": "invalid_key"}
            
            with pytest.raises(HTTPException) as exc_info:
                await verify_api_key(request)
            
            assert exc_info.value.status_code == 401
            assert "Invalid API key" in exc_info.value.detail


# Middleware tests
class TestMiddleware:
    """Test security middleware."""
    
    @pytest.mark.asyncio
    async def test_security_headers_middleware(self):
        """Test security headers middleware."""
        request = Mock()
        response = Mock()
        response.headers = {}
        
        async def call_next(req):
            return response
        
        result = await security_headers_middleware(request, call_next)
        
        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert result.headers["X-Frame-Options"] == "DENY"
        assert result.headers["X-XSS-Protection"] == "1; mode=block"
        assert "Strict-Transport-Security" in result.headers
        assert "Referrer-Policy" in result.headers
        assert "Permissions-Policy" in result.headers
    
    @pytest.mark.asyncio
    async def test_request_id_middleware(self):
        """Test request ID middleware."""
        request = Mock()
        request.headers = {}
        request.state = Mock()
        
        response = Mock()
        response.headers = {}
        
        async def call_next(req):
            return response
        
        result = await request_id_middleware(request, call_next)
        
        assert "X-Request-ID" in result.headers
        assert hasattr(request.state, "request_id")
    
    @pytest.mark.asyncio
    async def test_request_id_middleware_existing_id(self):
        """Test request ID middleware with existing ID."""
        existing_id = "existing-request-id"
        request = Mock()
        request.headers = {"X-Request-ID": existing_id}
        request.state = Mock()
        
        response = Mock()
        response.headers = {}
        
        async def call_next(req):
            return response
        
        result = await request_id_middleware(request, call_next)
        
        assert result.headers["X-Request-ID"] == existing_id
        assert request.state.request_id == existing_id


# CORS validation tests
class TestCORS:
    """Test CORS validation."""
    
    def test_validate_cors_origin_allowed(self):
        """Test CORS validation with allowed origin."""
        assert validate_cors_origin("http://localhost:3000") is True
        assert validate_cors_origin("http://localhost:8000") is True
    
    def test_validate_cors_origin_disallowed(self):
        """Test CORS validation with disallowed origin."""
        assert validate_cors_origin("http://evil.com") is False
        assert validate_cors_origin("https://attacker.com") is False
    
    def test_validate_cors_origin_empty(self):
        """Test CORS validation with empty origin."""
        assert validate_cors_origin("") is False
        assert validate_cors_origin(None) is False


# Input validation tests
class TestInputValidation:
    """Test input validation helpers."""
    
    def test_sanitize_input_normal(self):
        """Test input sanitization with normal input."""
        input_str = "This is a normal input string"
        assert sanitize_input(input_str) == input_str
    
    def test_sanitize_input_with_null_bytes(self):
        """Test input sanitization with null bytes."""
        input_str = "Test\x00String"
        assert sanitize_input(input_str) == "TestString"
    
    def test_sanitize_input_with_control_chars(self):
        """Test input sanitization with control characters."""
        input_str = "Test\x01\x02String\x7F"
        assert sanitize_input(input_str) == "TestString"
    
    def test_sanitize_input_preserve_newlines(self):
        """Test input sanitization preserves newlines."""
        input_str = "Line1\nLine2\tTabbed"
        assert sanitize_input(input_str) == input_str
    
    def test_sanitize_input_max_length(self):
        """Test input sanitization with max length."""
        input_str = "A" * 2000
        result = sanitize_input(input_str, max_length=100)
        assert len(result) == 100
    
    def test_sanitize_input_empty(self):
        """Test input sanitization with empty input."""
        assert sanitize_input("") == ""
        assert sanitize_input(None) == ""


# API key management tests
class TestAPIKeyManagement:
    """Test API key management."""
    
    @pytest.mark.asyncio
    async def test_create_api_key(self, mock_redis):
        """Test API key creation."""
        with patch("api.security.get_redis_client", return_value=mock_redis):
            api_key = await create_api_key("test_service", ["read", "write"])
            
            assert len(api_key) > 20
            mock_redis.setex.assert_called_once()
            
            args = mock_redis.setex.call_args[0]
            assert args[0].startswith("api_key:")
            assert args[1] == 365 * 86400  # Default expiration
    
    @pytest.mark.asyncio
    async def test_revoke_api_key(self, mock_redis):
        """Test API key revocation."""
        with patch("api.security.get_redis_client", return_value=mock_redis):
            result = await revoke_api_key("test_api_key")
            
            assert result is True
            mock_redis.delete.assert_called_once_with("api_key:test_api_key")
    
    @pytest.mark.asyncio
    async def test_revoke_api_key_not_found(self, mock_redis):
        """Test revoking non-existent API key."""
        mock_redis.delete = AsyncMock(return_value=0)
        
        with patch("api.security.get_redis_client", return_value=mock_redis):
            result = await revoke_api_key("non_existent_key")
            
            assert result is False


# User model tests
class TestUserModel:
    """Test User model."""
    
    def test_user_default_scopes(self):
        """Test user gets default scopes based on role."""
        user = User(username="test", email="test@example.com", role="user")
        assert user.scopes == ["read"]
        
        admin = User(username="admin", email="admin@example.com", role="admin")
        assert admin.scopes == ["read", "write", "admin"]
        
        editor = User(username="editor", email="editor@example.com", role="editor")
        assert editor.scopes == ["read", "write"]
        
        service = User(username="service", email="service@example.com", role="service")
        assert service.scopes == ["read", "write", "service"]
    
    def test_user_custom_scopes(self):
        """Test user with custom scopes."""
        user = User(
            username="custom",
            email="custom@example.com",
            role="user",
            scopes=["read", "custom_scope"]
        )
        assert user.scopes == ["read", "custom_scope"]
    
    def test_user_id_generation(self):
        """Test automatic user ID generation."""
        user1 = User(username="test1", email="test1@example.com")
        user2 = User(username="test2", email="test2@example.com")
        
        assert user1.id != user2.id
        assert len(user1.id) == 36  # UUID format


# Integration tests
class TestIntegration:
    """Integration tests for security features."""
    
    @pytest.mark.asyncio
    async def test_full_auth_flow(self, mock_redis):
        """Test full authentication flow."""
        with patch("api.security.get_redis_client", return_value=mock_redis):
            # Create user
            user = User(
                username="testuser",
                email="test@example.com",
                role="user"
            )
            
            # Hash password
            password = "SecurePass123!"
            hashed = hash_password(password)
            
            # Verify password
            assert verify_password(password, hashed)
            
            # Create tokens
            token_data = {
                "sub": user.username,
                "user_id": user.id,
                "scopes": user.scopes
            }
            
            access_token = create_access_token(token_data)
            refresh_token = create_refresh_token(token_data)
            
            # Decode and verify access token
            decoded = await decode_token(access_token)
            assert decoded.username == user.username
            assert decoded.user_id == user.id
            assert decoded.scopes == user.scopes
            
            # Blacklist token
            await blacklist_token(access_token)
            mock_redis.setex.assert_called()
            
            # Verify blacklisted token fails
            mock_redis.exists = AsyncMock(return_value=True)
            with pytest.raises(HTTPException) as exc_info:
                await decode_token(access_token)
            assert exc_info.value.status_code == 401


if __name__ == "__main__":
    pytest.main([__file__, "-v"])