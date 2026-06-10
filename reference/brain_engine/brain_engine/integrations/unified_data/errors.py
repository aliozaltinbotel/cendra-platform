"""Exception hierarchy for the onboarding-api unified-data integration.

All errors derive from :class:`UnifiedDataError` so callers can catch
the whole subsystem with a single ``except`` clause, while still
discriminating between transport-level and GraphQL-level failures
when needed.
"""

from __future__ import annotations

from typing import Any

from brain_engine.exceptions import BrainEngineError

__all__ = [
    "UnifiedDataError",
    "UnifiedDataGraphQLError",
    "UnifiedDataTransportError",
]


class UnifiedDataError(BrainEngineError):
    """Base exception for the unified-data GraphQL integration."""


class UnifiedDataTransportError(UnifiedDataError):
    """HTTP/network failure when reaching the GraphQL endpoint.

    Wraps :class:`httpx.HTTPError` so call sites never have to import
    ``httpx`` to handle a network blip.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        **context: Any,
    ) -> None:
        super().__init__(
            message,
            code=status_code if status_code is not None else 503,
            status_code=status_code,
            **context,
        )
        self.status_code = status_code


class UnifiedDataGraphQLError(UnifiedDataError):
    """GraphQL response carried an ``errors`` array or had no ``data``.

    Attributes:
        errors: The raw GraphQL ``errors`` payload, kept verbatim so
            observability layers can surface the upstream messages
            without re-parsing.
    """

    def __init__(
        self,
        message: str,
        *,
        errors: list[Any],
        **context: Any,
    ) -> None:
        super().__init__(message, code=502, errors=errors, **context)
        self.errors = errors
