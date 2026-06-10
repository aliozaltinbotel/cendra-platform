"""Pipeline hook: surface a temporal analysis to PM Chat (Phase 3 PR3c.1).

Wires the PM-chat temporal surface (:mod:`temporal_pm`) into the live
conversation pipeline.  After Brain answers the guest,
:func:`maybe_emit_temporal_analysis` runs a grounded analysis of the
client's past + present and emits it to PM Chat as a ``TEMPORAL_ANALYSIS``
SSE event — the same Brain→PM channel as ``MISSING_INFO_DETECTED``.

All the heavy logic stays here (and in the surface / core it calls), so the
3.8k-line ``service.py`` only gains a single call.  The hook is:

* **flag-gated default-off** (``BRAIN_TEMPORAL_PM_ENABLED``, read live);
* **import-safe** — it holds no analyzer at import time; the bootstrap
  injects a built analyzer + timeline once via
  :func:`configure_temporal_pm_deps`;
* **non-fatal** — any failure is logged and swallowed so an optional PM
  insight never breaks the conversation.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Final

import structlog

from brain_engine.conversation.temporal_pm import respond
from brain_engine.memory.memory_timeline import TimelineScope
from brain_engine.streaming.emit_helpers import emit_temporal_analysis

if TYPE_CHECKING:
    from brain_engine.memory.memory_timeline import MemoryTimeline
    from brain_engine.temporal_analysis import TemporalAnalyzer

__all__ = [
    "configure_temporal_pm_deps",
    "maybe_emit_temporal_analysis",
]


logger = structlog.get_logger(__name__)

_ENABLED_ENV: Final[str] = "BRAIN_TEMPORAL_PM_ENABLED"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

# The standing analytical question, overridable per env (mirrors the
# analyzer's injectable system prompt — a single instruction, not a
# taxonomy).
_QUESTION_ENV: Final[str] = "TEMPORAL_PM_QUESTION"
_DEFAULT_QUESTION: Final[str] = (
    "Summarise this client's recent history and anything notable the "
    "property manager should know right now."
)

# Injected once at startup; empty ⇒ the hook is a silent no-op.
_deps: dict[str, Any] = {}


def configure_temporal_pm_deps(
    *,
    analyzer: TemporalAnalyzer | None,
    timeline: MemoryTimeline | None,
) -> None:
    """Inject the analyzer + timeline the hook reuses (built once)."""
    _deps["analyzer"] = analyzer
    _deps["timeline"] = timeline


async def maybe_emit_temporal_analysis(
    *,
    property_id: str,
    customer_id: str = "",
) -> None:
    """Emit a ``TEMPORAL_ANALYSIS`` PM-chat event when enabled.

    No-op when the flag is off, the hook is unwired, or no client scope is
    given.  Fully non-fatal: every failure is logged and swallowed.
    """
    if not _enabled():
        return
    analyzer = _deps.get("analyzer")
    timeline = _deps.get("timeline")
    if analyzer is None or timeline is None:
        return
    if not (property_id or customer_id):
        return

    try:
        scope = TimelineScope(
            property_id=property_id,
            customer_id=customer_id,
        )
        reply = await respond(
            _question(),
            scope,
            analyzer=analyzer,
            timeline=timeline,
        )
        if reply is None:
            return
        analysis = reply.result.analysis
        if analysis is None:
            return
        emit_temporal_analysis(
            text=reply.text,
            answer=analysis.answer,
            key_findings=analysis.key_findings,
            confidence=analysis.confidence,
            context_entry_count=reply.result.context_entry_count,
            as_of=reply.result.as_of.isoformat(),
            scope=_scope_dict(scope),
        )
        logger.info(
            "temporal_pm_hook.emitted",
            entry_count=reply.result.context_entry_count,
        )
    except Exception:
        logger.warning("temporal_pm_hook.failed", exc_info=True)


def _enabled() -> bool:
    """Whether the PM-chat hook flag is on (read live, every call)."""
    return os.environ.get(_ENABLED_ENV, "").strip().lower() in _TRUTHY


def _question() -> str:
    """The standing analytical question (env override or default)."""
    return os.environ.get(_QUESTION_ENV, "").strip() or _DEFAULT_QUESTION


def _scope_dict(scope: TimelineScope) -> dict[str, str]:
    """Non-empty client identifiers, for the event payload."""
    pairs = (
        ("property_id", scope.property_id),
        ("customer_id", scope.customer_id),
    )
    return {label: value for label, value in pairs if value}
