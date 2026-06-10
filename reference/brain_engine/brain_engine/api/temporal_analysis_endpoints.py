"""HTTP surface for the temporal analysis core (Phase 3, PR3b).

A thin, read-only FastAPI router that exposes
:class:`~brain_engine.temporal_analysis.analyzer.TemporalAnalyzer`.  It
assembles a client's fused :class:`TemporalContext` from the injected
memory stores (knowledge graph + guest operations + customer events),
runs the analyzer, and returns the structured result.

All the work lives in the deterministic Phase 1/2 substrate and the
Phase 3 core; this module only wires stores → timeline → context →
analyzer and shapes the HTTP request / response.

The router is **import-safe**: it holds no store at import time.  A host
calls :func:`configure_temporal_analysis_deps` once at startup to inject
the live stores and chat model (mirrors ``memory_endpoints`` /
``foundation_audit``).  Mounting + that wiring is a separate activation
step, so this module is inert until then.

The endpoint is gated by the default-off ``BRAIN_TEMPORAL_ANALYSIS_ENABLED``
env flag, read on every call so it can be flipped without a pod bounce.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from brain_engine.memory.memory_timeline import MemoryTimeline, TimelineScope
from brain_engine.memory.temporal_fusion import build_temporal_context
from brain_engine.memory.timeline_sources import (
    CustomerEventSource,
    GuestOperationsSource,
    KnowledgeGraphSource,
)
from brain_engine.temporal_analysis import TemporalAnalysis, TemporalAnalyzer

if TYPE_CHECKING:
    from brain_engine.memory.memory_timeline import TimelineSource
    from brain_engine.temporal_analysis import TemporalAnalysisResult

__all__ = [
    "configure_temporal_analysis_deps",
    "router",
]


logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/temporal", tags=["Temporal Analysis"])

_ENABLED_ENV: Final[str] = "BRAIN_TEMPORAL_ANALYSIS_ENABLED"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_STORE_KEYS: Final[tuple[str, ...]] = (
    "knowledge_graph",
    "guest_history",
    "customer_memory",
)

# Injected at startup; empty (and the endpoint 503s) until a host wires it.
_deps: dict[str, Any] = {}


def configure_temporal_analysis_deps(deps: dict[str, Any]) -> None:
    """Inject the live stores / chat model the endpoint reads.

    Recognised keys: ``knowledge_graph``, ``guest_history``,
    ``customer_memory`` (timeline sources, each optional) and
    ``chat_model`` (a :class:`BaseChatModel`; absent ⇒ the analyzer
    degrades to a no-LLM result).
    """
    _deps.update(deps)


class TemporalAnalyzeRequest(BaseModel):
    """Body for ``POST /api/v1/temporal/analyze``."""

    question: str = Field(
        ...,
        min_length=1,
        description="The question to answer about this client.",
    )
    property_id: str = Field(default="", description="Property identifier.")
    guest_id: str = Field(default="", description="Guest identifier.")
    customer_id: str = Field(default="", description="Customer identifier.")
    workspace_id: str = Field(
        default="",
        description="Workspace identifier.",
    )
    as_of: datetime | None = Field(
        default=None,
        description=(
            "Anchor instant; defaults to now. Reconstructs the context "
            "as it stood then (past analysis)."
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description="Cap on the most-recent history entries fed in.",
    )


class TemporalAnalyzeResponse(BaseModel):
    """Result of one temporal analysis."""

    question: str
    as_of: datetime
    scope: dict[str, str]
    llm_used: bool
    context_entry_count: int
    note: str
    analysis: TemporalAnalysis | None


@router.post(
    "/analyze",
    response_model=TemporalAnalyzeResponse,
    summary="Analyse one client's fused past+present temporal context",
)
async def temporal_analyze(
    body: TemporalAnalyzeRequest,
) -> TemporalAnalyzeResponse | JSONResponse:
    """Assemble the client's timeline, fuse it, and analyse it."""
    if not _enabled():
        return JSONResponse(
            status_code=404,
            content={
                "error": "temporal_analysis_disabled",
                "detail": (f"Set {_ENABLED_ENV} to enable this endpoint."),
            },
        )
    if not _has_any_store():
        return JSONResponse(
            status_code=503,
            content={
                "error": "temporal_analysis_not_wired",
                "detail": "No memory stores configured for the timeline.",
            },
        )

    scope = TimelineScope(
        property_id=body.property_id,
        guest_id=body.guest_id,
        customer_id=body.customer_id,
        workspace_id=body.workspace_id,
    )
    context = await build_temporal_context(
        _build_timeline(),
        scope,
        as_of=body.as_of,
        limit=body.limit,
    )
    analyzer = TemporalAnalyzer(_deps.get("chat_model"))
    result = await analyzer.analyze(context, body.question)
    logger.info(
        "temporal_analysis.endpoint",
        entry_count=result.context_entry_count,
        llm_used=result.llm_used,
    )
    return _to_response(result)


def _enabled() -> bool:
    """Whether the endpoint flag is on (read live, every call)."""
    return os.environ.get(_ENABLED_ENV, "").strip().lower() in _TRUTHY


def _has_any_store() -> bool:
    """Whether at least one timeline source is wired."""
    return any(_deps.get(key) is not None for key in _STORE_KEYS)


def _build_timeline() -> MemoryTimeline:
    """A timeline over whichever stores are configured."""
    sources: list[TimelineSource] = []
    if (kg := _deps.get("knowledge_graph")) is not None:
        sources.append(KnowledgeGraphSource(kg))
    if (gh := _deps.get("guest_history")) is not None:
        sources.append(GuestOperationsSource(gh))
    if (cm := _deps.get("customer_memory")) is not None:
        sources.append(CustomerEventSource(cm))
    return MemoryTimeline(sources)


def _to_response(result: TemporalAnalysisResult) -> TemporalAnalyzeResponse:
    """Shape the core result into the HTTP response."""
    scope = result.scope
    scope_out = {
        label: value
        for label, value in (
            ("property_id", scope.property_id),
            ("guest_id", scope.guest_id),
            ("customer_id", scope.customer_id),
            ("workspace_id", scope.workspace_id),
        )
        if value
    }
    return TemporalAnalyzeResponse(
        question=result.question,
        as_of=result.as_of,
        scope=scope_out,
        llm_used=result.llm_used,
        context_entry_count=result.context_entry_count,
        note=result.note,
        analysis=result.analysis,
    )
