"""Lifespan wiring for the direct Elasticsearch property reader.

Builds an :class:`ElasticsearchPropertyReader` that the profile
harvester uses to overlay the rich ``properties`` document (full
pricing, WiFi, amenities, descriptions, fees, cancellation policies)
onto the GraphQL-built ``static_payload``.

Activation contract (opt-in, fail-open):

* ``ES_ENRICHMENT_ENABLED`` must be truthy AND ``ELASTICSEARCH_API_KEY``
  must be non-empty; otherwise the reader stays ``None`` and the
  harvester builds profiles from GraphQL alone — byte-for-byte the
  pre-Elasticsearch behaviour.
* Any construction error is logged and swallowed so a misconfigured ES
  endpoint never aborts startup or the harvest.

The wire entry point is synchronous because the client only allocates
an :class:`httpx.AsyncClient` at construction (no network I/O until the
first request).  ``await client.aclose()`` on shutdown lives in
``server.lifespan`` alongside the other integration teardowns.
"""

from __future__ import annotations

import logging
import os
from typing import Final

from fastapi import FastAPI

from brain_engine.integrations.elasticsearch import (
    ElasticsearchClient,
    ElasticsearchPropertyReader,
)

logger = logging.getLogger(__name__)

_ENABLED_ENV: Final[str] = "ES_ENRICHMENT_ENABLED"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def _enabled() -> bool:
    return os.getenv(_ENABLED_ENV, "").strip().lower() in _TRUTHY


def wire(application: FastAPI) -> ElasticsearchPropertyReader | None:
    """Build the ES property reader, or ``None`` when disabled / failing.

    On success ``application.state.elasticsearch_client`` and
    ``application.state.es_property_reader`` are populated so the
    onboarding section can inject the reader into the harvester and the
    lifespan can close the client on shutdown.
    """
    if not _enabled():
        return None
    api_key = os.getenv("ELASTICSEARCH_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "elasticsearch enrichment enabled but ELASTICSEARCH_API_KEY "
            "is empty — staying disabled",
        )
        return None
    try:
        endpoint = os.getenv("ELASTICSEARCH_ENDPOINT", "").strip()
        client_kwargs: dict[str, str] = {"api_key": api_key}
        if endpoint:
            client_kwargs["endpoint"] = endpoint
        client = ElasticsearchClient(**client_kwargs)
        reader = ElasticsearchPropertyReader(client)
    except Exception as exc:
        logger.warning("elasticsearch client init failed: %s", exc)
        return None

    application.state.elasticsearch_client = client
    application.state.es_property_reader = reader
    logger.info("Elasticsearch property enrichment wired (endpoint set).")
    return reader
