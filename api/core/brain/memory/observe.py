"""Memory-retrieval observability seam.

The reference streamed per-tier retrieval events over its AG-UI SSE
channel (``brain_engine.streaming.emit_helpers.emit_memory_retrieved``).
The streaming stack is retired (PORTING_MAP); this shim keeps every
call site verbatim and logs at debug until the runtime wiring (Batch
4/5) routes retrieval telemetry onto Dify's observability surface.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["emit_memory_retrieved"]

logger = logging.getLogger(__name__)


def emit_memory_retrieved(
    *,
    tier: str,
    query: str,
    hits: list[dict[str, Any]],
    latency_ms: float,
) -> None:
    """Record one memory-retrieval event (debug log until Batch 4/5)."""
    logger.debug(
        "memory_retrieved tier=%s query=%s hits=%s latency_ms=%s",
        tier,
        query,
        len(hits),
        round(latency_ms, 2),
    )
