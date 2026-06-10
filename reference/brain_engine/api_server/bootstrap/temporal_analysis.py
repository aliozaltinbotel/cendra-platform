"""Lifespan wiring for the temporal analysis endpoint (Phase 3, PR3b.1).

Activates the read-only ``/api/v1/temporal/analyze`` surface (PR3b) by
injecting the live timeline stores and a chat model into its router.  The
endpoint itself stays gated by the default-off
``BRAIN_TEMPORAL_ANALYSIS_ENABLED`` flag, so mounting + wiring it here is
inert until an operator flips that flag.

Kept out of ``server.lifespan`` (already ~3k lines) as a focused wire,
mirroring :mod:`api_server.bootstrap.memory`.  The wire is **best-effort**:
the timeline is assembled from whichever stores the memory system exposes
(``knowledge_graph`` + ``guest_history``; ``customer_memory`` is not built
in the API server yet, so that source is simply absent and the timeline
degrades gracefully), and a chat model that fails to construct leaves the
endpoint running degraded (HTTP 200 with ``analysis=null``) rather than
breaking startup.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Final

from brain_engine.api.temporal_analysis_endpoints import (
    configure_temporal_analysis_deps,
)
from brain_engine.conversation.temporal_pm_hook import (
    configure_temporal_pm_deps,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from brain_engine.memory.factory import MemorySystem
    from brain_engine.models.base import BaseChatModel
    from config.settings import Settings

logger = logging.getLogger(__name__)

# Provider / model for the analysis LLM.  Defaults to Azure OpenAI (the
# dev backend) and the primary ``settings.llm_model``; override per env
# without code change.
_BACKEND_ENV: Final[str] = "TEMPORAL_ANALYSIS_BACKEND"
_MODEL_ENV: Final[str] = "TEMPORAL_ANALYSIS_MODEL"


def wire(
    application: FastAPI,
    *,
    settings: Settings,
    memory: MemorySystem | None,
) -> None:
    """Inject stores + chat model into the temporal analysis router.

    Args:
        application: The FastAPI app (its ``state`` records the wiring).
        settings: Loaded settings — supplies the default model id.
        memory: The cognitive memory system; its ``knowledge_graph`` and
            ``guest_history`` become timeline sources.  ``None`` leaves
            the endpoint wired but store-less (it then returns 503).
    """
    deps: dict[str, Any] = {}
    if memory is not None:
        if getattr(memory, "knowledge_graph", None) is not None:
            deps["knowledge_graph"] = memory.knowledge_graph
        if getattr(memory, "guest_history", None) is not None:
            deps["guest_history"] = memory.guest_history

    model = _build_chat_model(settings)
    if model is not None:
        deps["chat_model"] = model

    configure_temporal_analysis_deps(deps)
    application.state.temporal_analysis_wired = True
    logger.info(
        "Temporal analysis endpoint wired (stores=%s, llm=%s). "
        "Gated by BRAIN_TEMPORAL_ANALYSIS_ENABLED (default off).",
        sorted(key for key in deps if key != "chat_model"),
        "yes" if model is not None else "no",
    )

    _wire_pm_hook(deps, model)


def _wire_pm_hook(deps: dict[str, Any], model: BaseChatModel | None) -> None:
    """Build the timeline + analyzer once and hand them to the PM hook.

    Reuses the same stores the endpoint got.  ``None`` slots (no sources
    or no model) leave the hook a silent no-op; it is also flag-gated
    default-off (``BRAIN_TEMPORAL_PM_ENABLED``).
    """
    from brain_engine.memory.memory_timeline import MemoryTimeline
    from brain_engine.memory.timeline_sources import (
        CustomerEventSource,
        GuestOperationsSource,
        KnowledgeGraphSource,
    )
    from brain_engine.temporal_analysis import TemporalAnalyzer

    sources: list[Any] = []
    if (kg := deps.get("knowledge_graph")) is not None:
        sources.append(KnowledgeGraphSource(kg))
    if (gh := deps.get("guest_history")) is not None:
        sources.append(GuestOperationsSource(gh))
    if (cm := deps.get("customer_memory")) is not None:
        sources.append(CustomerEventSource(cm))

    timeline = MemoryTimeline(sources) if sources else None
    analyzer = TemporalAnalyzer(model) if model is not None else None
    configure_temporal_pm_deps(analyzer=analyzer, timeline=timeline)
    logger.info(
        "Temporal PM-chat hook wired (sources=%d, llm=%s). "
        "Gated by BRAIN_TEMPORAL_PM_ENABLED (default off).",
        len(sources),
        "yes" if analyzer is not None else "no",
    )


def _build_chat_model(settings: Settings) -> BaseChatModel | None:
    """Build the analysis chat model, or ``None`` on any failure.

    A failure (missing provider config, bad model id) is logged and
    swallowed so the endpoint runs degraded instead of aborting startup.
    """
    backend = os.getenv(_BACKEND_ENV, "azure_openai").strip().lower()
    model_id = os.getenv(_MODEL_ENV, settings.llm_model)
    try:
        from brain_engine.models.factory import init_chat_model

        kwargs: dict[str, Any] = {}
        if backend == "azure_openai":
            kwargs = {
                "azure_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", ""),
                "api_version": os.getenv("AZURE_OPENAI_API_VERSION", ""),
                "api_key": os.getenv("AZURE_OPENAI_API_KEY", "") or None,
            }
        return init_chat_model(f"{backend}:{model_id}", **kwargs)
    except Exception as exc:  # best-effort: degrade, never break startup
        logger.warning(
            "Temporal analysis chat model init failed — endpoint will "
            "run degraded (no LLM): %s (%s)",
            exc,
            type(exc).__name__,
        )
        return None
