"""FastAPI middleware that binds a :class:`TenantContext` per request.

The middleware inspects each incoming request for a
``property_channel_id`` in three places, in priority order:

1. **Path parameter** — extracted via a configurable regex; the
   bootstrap endpoints (``/bootstrap/property/{id}``) and any other
   route with ``/property/<id>`` in its URL match here.
2. **Header** — ``X-Property-Channel-Id`` (with two camelCase
   aliases the Sandbox UI ships).  Clean integration path for
   endpoints whose body is opaque to the middleware (AG-UI SSE
   handshake, ``/private-conversation`` etc.).
3. **Query string** — ``?property_id=`` / ``?propertyChannelId=``.

When an id is found, :class:`TenantResolver` resolves it to a
:class:`TenantContext` and :func:`bind_tenant` makes it visible to
downstream services for the duration of the request.  When no id
can be located the middleware is a no-op — request paths that do
not need tenant scope (e.g. health probes) are unaffected.

Why no JSON body peek
---------------------
Earlier versions of this middleware also read ``application/json``
bodies to extract ``property_id`` from the payload.  That path
required replacing ``request._receive`` with a replay callable so
downstream handlers could still parse the body — and the replay
collided with Starlette ``BaseHTTPMiddleware``'s internal
``wrapped_receive`` state machine whenever the inner endpoint
returned a streaming response (AG-UI SSE):

    RuntimeError: Unexpected message received: http.request

The body peek is therefore intentionally absent here.  Callers
that cannot put the id in the URL or path must add the
``X-Property-Channel-Id`` header.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Final

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from brain_engine.tenants.context import bind_tenant
from brain_engine.tenants.resolver import TenantResolver
from brain_engine.tenants.runtime import (
    active_auto_bootstrap_trigger,
    active_tenant_resolver,
)

__all__ = ["TenantResolverMiddleware"]


logger = structlog.get_logger(__name__)


_PROPERTY_QUERY_KEYS: Final[tuple[str, ...]] = (
    "property_id",
    "property_channel_id",
    "propertyChannelId",
    "propertyId",
)

_PROPERTY_HEADER_KEYS: Final[tuple[str, ...]] = (
    "x-property-channel-id",
    "x-property-id",
    "x-propertychannelid",
)

_DEFAULT_PATH_REGEX: Final[re.Pattern[str]] = re.compile(
    r"/property/(?P<property_id>[^/?#]+)",
)


class TenantResolverMiddleware(BaseHTTPMiddleware):
    """Resolve tenant from request and bind it to the ContextVar."""

    def __init__(
        self,
        app: Callable[..., Awaitable[Response]],
        resolver: TenantResolver | None = None,
        path_regex: re.Pattern[str] = _DEFAULT_PATH_REGEX,
    ) -> None:
        super().__init__(app)
        self._resolver = resolver
        self._path_regex = path_regex

    def _current_resolver(self) -> TenantResolver | None:
        if self._resolver is not None:
            return self._resolver
        return active_tenant_resolver()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        resolver = self._current_resolver()
        if resolver is None:
            return await call_next(request)

        property_id = self._extract_from_path(request)
        if property_id is None:
            property_id = self._extract_from_header(request)
        if property_id is None:
            property_id = self._extract_from_query(request)

        if property_id is None:
            return await call_next(request)

        context = await resolver.resolve(property_id)
        # Phase 4: fire-and-forget auto-bootstrap for the first
        # touch of a property the brain has never primed.  The
        # trigger handles its own dedup and never raises, so a
        # missing pipeline or a failed bootstrap can never poison
        # the request path.
        trigger = active_auto_bootstrap_trigger()
        if trigger is not None:
            try:
                await trigger.maybe_fire(property_id, context)
            except Exception as exc:
                logger.warning(
                    "auto_bootstrap_trigger_failed",
                    property_channel_id=property_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        with bind_tenant(context):
            return await call_next(request)

    def _extract_from_path(self, request: Request) -> str | None:
        match = self._path_regex.search(request.url.path)
        if match is None:
            return None
        return match.group("property_id") or None

    @staticmethod
    def _extract_from_header(request: Request) -> str | None:
        # Starlette normalises header names to lowercase, so the
        # comparison key is also lowercase here.
        for key in _PROPERTY_HEADER_KEYS:
            value = request.headers.get(key)
            if value and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _extract_from_query(request: Request) -> str | None:
        for key in _PROPERTY_QUERY_KEYS:
            value = request.query_params.get(key)
            if value:
                return value
        return None
