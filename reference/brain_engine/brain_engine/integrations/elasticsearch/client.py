"""Async Elasticsearch client for the direct property-data read path.

The brain builds property profiles from the onboarding-api GraphQL
resolver, which projects only a subset of the underlying Elasticsearch
``properties`` document.  This thin client lets the harvester read the
full document directly to enrich the profile (full pricing, WiFi,
amenities, descriptions, fees, cancellation policies).

It is intentionally schema-unaware: callers pass a raw query body and
receive the parsed JSON.  The default endpoint points at the in-cluster
service DNS so it works unchanged from any pod; local development uses
``kubectl port-forward svc/elasticsearch-cluster-es-http 9200:9200`` and
``endpoint="https://localhost:9200"``.

Authentication uses an Elasticsearch API key in the ``ApiKey`` scheme
(the base64 ``id:api_key`` "encoded" form).  TLS verification is
disabled by default because the in-cluster certificate is self-signed;
this is a read-only, cluster-internal call.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import httpx
import structlog

from brain_engine.integrations.elasticsearch.errors import (
    ElasticsearchReaderError,
)

__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_TIMEOUT_SECONDS",
    "ElasticsearchClient",
]


logger = structlog.get_logger(__name__)

DEFAULT_ENDPOINT: Final[str] = (
    "https://elasticsearch-cluster-es-http.elasticsearch.svc:9200"
)
DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0


class ElasticsearchClient:
    """Thin async wrapper over the Elasticsearch ``_search`` endpoint.

    Owns its underlying :class:`httpx.AsyncClient` only when the caller
    did not inject one, so a shared connection pool can be passed in and
    closed by its owner.

    Usage::

        async with ElasticsearchClient(api_key="<encoded>") as es:
            body = await es.search("properties", {"query": {...}})
    """

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        verify_tls: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                base_url=endpoint.rstrip("/"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"ApiKey {api_key}",
                },
                timeout=httpx.Timeout(timeout_seconds),
                verify=verify_tls,
            )
        self._client = client

    async def __aenter__(self) -> ElasticsearchClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client when this object owns it."""
        if self._owns_client:
            await self._client.aclose()

    async def search(
        self,
        index: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Run ``POST /{index}/_search`` and return the parsed response.

        Raises:
            ElasticsearchReaderError: transport failed, the endpoint
                returned a non-2xx status, or the body was not JSON.
        """
        try:
            response = await self._client.post(
                f"/{index}/_search", json=dict(body),
            )
        except httpx.HTTPError as exc:
            raise ElasticsearchReaderError(
                index, f"request failed: {exc}",
            ) from exc

        if response.status_code >= 400:
            raise ElasticsearchReaderError(
                index, f"returned HTTP {response.status_code}",
            )

        try:
            return response.json()
        except ValueError as exc:
            raise ElasticsearchReaderError(
                index, "returned non-JSON body",
            ) from exc
