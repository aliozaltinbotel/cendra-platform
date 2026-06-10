"""Prometheus ``/metrics`` endpoint for Brain Engine.

Reference: ``brain_engine_advisory.md`` §5.

Wiring contract:

* The router is mounted at the application root (no prefix); the
  scrape target is exactly ``/metrics``.
* The exporter is a process-wide singleton from
  ``brain_engine.observability.exporters``.  Every code path that
  records a metric calls into the same singleton.
* The endpoint is not authenticated by design — production scrape
  is restricted at the network layer (NetworkPolicy) so only the
  Prometheus pod can reach the route.

The route always returns 200 — even if the exporter has never been
told about a metric — so that a missing metric does not silently
become a missing scrape target.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from brain_engine.observability.exporters.prometheus_exporter import (
    CONTENT_TYPE_LATEST,
    build_default_exporter,
)


router = APIRouter()


@router.get(
    "/metrics",
    summary="Prometheus metrics scrape endpoint",
    response_class=Response,
    include_in_schema=False,
)
async def metrics() -> Response:
    """Return the current registry snapshot in Prometheus format."""
    exporter = build_default_exporter()
    payload = exporter.render()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
