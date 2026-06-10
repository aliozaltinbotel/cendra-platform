"""Middleware configuration for the AG-UI FastAPI server.

Provides CORS, optional API key authentication, and basic rate limiting.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import Settings


def setup_cors(app: FastAPI, settings: Settings | None = None) -> None:
    """Attach CORS middleware to the FastAPI application.

    Args:
        app: The FastAPI application instance.
        settings: Optional settings; when None, allows all origins (dev mode).
    """
    allowed_origins = ["*"]
    if settings and settings.allowed_origins:
        allowed_origins = settings.allowed_origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Optional middleware that validates an API key on incoming requests.

    Checks the ``Authorization`` header for a Bearer token matching the
    configured ``ui_api_key``. Skips validation when no key is configured
    (development mode) or for health-check endpoints.
    """

    SKIP_PATHS: set[str] = {"/health", "/docs", "/openapi.json", "/redoc"}

    def __init__(self, app: FastAPI, api_key: str | None = None) -> None:
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self.api_key is None:
            return await call_next(request)

        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header."},
            )

        token = auth_header.removeprefix("Bearer ").strip()
        if token != self.api_key:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Invalid API key."},
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory sliding-window rate limiter.

    Limits each client IP to ``max_requests`` within ``window_seconds``.
    Suitable for single-instance deployments; use Redis-backed rate limiting
    for multi-instance production setups.
    """

    def __init__(
        self,
        app: FastAPI,
        max_requests: int = 60,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._request_log: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - self.window_seconds

        # Prune expired entries
        self._request_log[client_ip] = [
            ts for ts in self._request_log[client_ip] if ts > cutoff
        ]

        if len(self._request_log[client_ip]) >= self.max_requests:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={
                    "Retry-After": str(self.window_seconds),
                },
            )

        self._request_log[client_ip].append(now)
        return await call_next(request)


def setup_middleware(app: FastAPI, settings: Settings | None = None) -> None:
    """Apply all middleware layers to the FastAPI application.

    Order matters -- outermost middleware runs first. The stack is:
      1. CORS (outermost)
      2. Rate limiting
      3. API key auth
      4. Tenant resolver (innermost, closest to route handlers)

    Args:
        app: The FastAPI application instance.
        settings: Application settings.
    """
    from brain_engine.tenants import TenantResolverMiddleware

    setup_cors(app, settings)

    rate_limit = 60
    rate_window = 60
    if settings:
        rate_limit = settings.rate_limit_max_requests
        rate_window = settings.rate_limit_window_seconds

    app.add_middleware(
        RateLimitMiddleware,
        max_requests=rate_limit,
        window_seconds=rate_window,
    )

    api_key = settings.ui_api_key if settings else None
    if api_key:
        app.add_middleware(APIKeyAuthMiddleware, api_key=api_key)

    # Tenant resolver runs INNERMOST so it observes only requests
    # that already passed auth + rate limiting.  The resolver
    # itself is published from the FastAPI lifespan hook via
    # ``configure_tenant_resolver`` — until then the middleware
    # is a no-op pass-through (Phase 3 fail-open contract).
    app.add_middleware(TenantResolverMiddleware)
