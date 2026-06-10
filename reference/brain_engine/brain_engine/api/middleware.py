"""API Middleware — Auth, rate limiting, request logging.

Provides FastAPI middleware components for the Brain Engine API.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)


# ── API Key Authentication ──────────────────────────────────────────── #


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validates API key from request headers.

    Skips validation for health endpoint. If no API key is configured,
    all requests are allowed.

    Args:
        app: FastAPI application.
        api_key: Expected API key (empty string disables auth).
    """

    def __init__(self, app: Any, api_key: str = "") -> None:
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> Response:
        """Validate API key and pass request through.

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware/handler in chain.

        Returns:
            HTTP response.
        """
        if self._should_skip(request):
            return await call_next(request)

        if self._api_key and not self._is_authenticated(request):
            raise HTTPException(status_code=401, detail="Invalid API key")

        return await call_next(request)

    @staticmethod
    def _should_skip(request: Request) -> bool:
        """Check if the request should skip auth.

        Args:
            request: The HTTP request.

        Returns:
            True if auth should be skipped.
        """
        return request.url.path in ("/api/v1/health", "/docs", "/openapi.json")

    def _is_authenticated(self, request: Request) -> bool:
        """Validate the API key from headers.

        Args:
            request: The HTTP request.

        Returns:
            True if the key is valid.
        """
        key = request.headers.get("X-API-Key", "")
        return key == self._api_key


# ── Rate Limiting ───────────────────────────────────────────────────── #


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory sliding window rate limiter.

    Tracks requests per client IP within a time window.

    Args:
        app: FastAPI application.
        max_requests: Maximum requests per window.
        window_seconds: Sliding window duration in seconds.
    """

    def __init__(
        self,
        app: Any,
        max_requests: int = 60,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self._max_requests = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> Response:
        """Check rate limit and pass request through.

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware/handler.

        Returns:
            HTTP response.
        """
        client_ip = self._get_client_ip(request)

        if not self._is_within_limit(client_ip):
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
            )

        return await call_next(request)

    def _is_within_limit(self, client_ip: str) -> bool:
        """Check if client is within rate limit.

        Args:
            client_ip: Client IP address.

        Returns:
            True if within limit.
        """
        now = time.monotonic()
        cutoff = now - self._window

        # Clean old entries
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if t > cutoff
        ]

        if len(self._requests[client_ip]) >= self._max_requests:
            return False

        self._requests[client_ip].append(now)
        return True

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Extract client IP from request.

        Args:
            request: The HTTP request.

        Returns:
            Client IP string.
        """
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        client = request.client
        return client.host if client else "unknown"


# ── Request Logging ─────────────────────────────────────────────────── #


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request with timing information.

    Args:
        app: FastAPI application.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> Response:
        """Log request and response timing.

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware/handler.

        Returns:
            HTTP response.
        """
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        logger.info(
            "%s %s -> %d (%dms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response


# ── CORS Setup ──────────────────────────────────────────────────────── #


def setup_cors(app: Any, allowed_origins: list[str] | None = None) -> None:
    """Configure CORS middleware on the FastAPI app.

    Args:
        app: FastAPI application.
        allowed_origins: List of allowed origins.
    """
    origins = allowed_origins or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
