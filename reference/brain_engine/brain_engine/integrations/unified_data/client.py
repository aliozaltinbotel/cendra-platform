"""Async GraphQL client for the Cendra onboarding-api unified read layer.

The client is intentionally schema-unaware: callers pass raw query
strings (typically from :mod:`brain_engine.integrations.unified_data.\
queries`) and receive the parsed ``data`` payload.  Mapping unified
shapes into Brain Engine domain objects lives in dedicated adapters
(see :mod:`brain_engine.narrative.unified_sources`).

The default base URL points at the in-cluster service DNS so the
client works unchanged from any pod inside the dev namespace.  Local
development should pass ``base_url="http://localhost:18080"`` after
``kubectl port-forward svc/onboarding-api 18080:80``.
"""

from __future__ import annotations

from typing import Any, Final, Mapping

import httpx
import structlog

from brain_engine.integrations.unified_data.errors import (
    UnifiedDataGraphQLError,
    UnifiedDataTransportError,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "UnifiedDataGraphQLClient",
]


logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL: Final[str] = "http://onboarding-api.dev.svc.cluster.local"
DEFAULT_TIMEOUT_SECONDS: Final[float] = 30.0
_GRAPHQL_PATH: Final[str] = "/graphql"


class UnifiedDataGraphQLClient:
    """Thin async wrapper over the onboarding-api GraphQL endpoint.

    The client owns its underlying :class:`httpx.AsyncClient` only when
    the caller did not inject one.  Callers that want to share a
    connection pool with other integrations can pass their own client
    and remain responsible for closing it.

    Usage::

        async with UnifiedDataGraphQLClient(base_url="...") as gql:
            data = await gql.execute(RESERVATIONS_LIST_QUERY, variables)
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
        auth_token: str | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._owns_client = client is None
        if client is None:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            client = httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                headers=headers,
                timeout=httpx.Timeout(timeout_seconds),
            )
        self._client = client

    async def __aenter__(self) -> UnifiedDataGraphQLClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client when this object owns it."""
        if self._owns_client:
            await self._client.aclose()

    async def execute(
        self,
        query: str,
        variables: Mapping[str, Any] | None = None,
        *,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        """Execute a GraphQL operation and return the ``data`` payload.

        Raises:
            UnifiedDataTransportError: HTTP transport failed or the
                endpoint returned a non-2xx status.
            UnifiedDataGraphQLError: GraphQL response carried an
                ``errors`` array or omitted ``data``.
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = dict(variables)
        if operation_name:
            payload["operationName"] = operation_name

        try:
            response = await self._client.post(_GRAPHQL_PATH, json=payload)
        except httpx.HTTPError as exc:
            raise UnifiedDataTransportError(
                f"GraphQL request failed: {exc}",
                operation_name=operation_name,
            ) from exc

        if response.status_code >= 400:
            raise UnifiedDataTransportError(
                f"GraphQL endpoint returned HTTP {response.status_code}",
                status_code=response.status_code,
                operation_name=operation_name,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise UnifiedDataTransportError(
                "GraphQL endpoint returned non-JSON body",
                status_code=response.status_code,
            ) from exc

        errors = body.get("errors")
        if errors:
            message = _summarise_errors(errors)
            raise UnifiedDataGraphQLError(
                message,
                errors=list(errors),
                operation_name=operation_name,
            )

        data = body.get("data")
        if not isinstance(data, dict):
            raise UnifiedDataGraphQLError(
                "GraphQL response missing 'data' object",
                errors=[],
                operation_name=operation_name,
            )
        return data


def _summarise_errors(errors: list[Any]) -> str:
    """Compose a human-readable summary from a GraphQL ``errors`` array."""
    messages: list[str] = []
    for entry in errors:
        if isinstance(entry, dict):
            text = entry.get("message")
            if text:
                messages.append(str(text))
    if not messages:
        return "GraphQL errors returned"
    return "; ".join(messages)
