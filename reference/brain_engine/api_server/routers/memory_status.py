"""Read-only status surface for the memory + patterns subsystem.

Operators need a single place to confirm at a glance that Brain
Engine's memory tiers are *alive and accumulating* — episodic events
landing in Redis, DecisionCases growing in Postgres, PatternRules
gaining confidence.  Without this view, the only signals are pod
restarts and silent drops.

Two endpoints are exposed under ``/api/admin/memory``:

* ``GET /status`` — global snapshot.  Counts across episodic memory,
  DecisionCase store, PatternRule store; readiness booleans for each
  dependency that lifespan wires.
* ``GET /recent/{property_id}`` — focused look at one property:
  recent episodes, last DecisionCases, active PatternRules.

The router is read-only by design.  It never writes, never triggers
mining, never mutates state.  That makes it safe to poll from a
dashboard or CI watcher every few seconds.

All return shapes are JSON-serialisable plain dicts so a CLI client
can pipe them through ``jq`` without touching Python.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Path, Query
from fastapi.responses import JSONResponse

from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.patterns.store import (
    DecisionCaseStore,
    PatternRuleStore,
)


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/admin/memory", tags=["Intelligence"])


_deps: dict[str, Any] = {
    "episodic_memory": None,
    "case_store": None,
    "rule_store": None,
}


def configure_deps(deps: dict[str, Any]) -> None:
    """Publish lifespan-built dependencies into the router scope.

    Re-entrant: a second call replaces the prior values atomically so
    test fixtures can swap stacks between runs.
    """
    _deps.update(deps)


@router.get(
    "/status",
    summary="Memory + patterns subsystem health snapshot",
)
async def memory_status() -> JSONResponse:
    """Return a high-level snapshot of every memory tier.

    Returns:
        ``200`` with a dict carrying per-tier readiness booleans and
        coarse counts.  Tiers that are not wired report
        ``"ready": false`` instead of raising — the response is meant
        to surface partial configuration rather than crash on it.
    """
    episodic = _deps.get("episodic_memory")
    case_store = _deps.get("case_store")
    rule_store = _deps.get("rule_store")

    episodic_block = await _episodic_block(episodic)
    case_block = await _case_block(case_store)
    rule_block = await _rule_block(rule_store)

    overall_ready = all(
        block["ready"]
        for block in (episodic_block, case_block, rule_block)
    )

    return JSONResponse(
        content={
            "ready": overall_ready,
            "episodic": episodic_block,
            "decision_cases": case_block,
            "pattern_rules": rule_block,
        }
    )


@router.get(
    "/recent/{property_id}",
    summary="Recent memory activity for one property",
)
async def memory_recent(
    property_id: str = Path(..., min_length=1),
    episodes: int = Query(
        20,
        ge=1,
        le=200,
        description="How many recent episodes to return.",
    ),
    cases: int = Query(
        20,
        ge=1,
        le=200,
        description="How many recent DecisionCases to return.",
    ),
) -> JSONResponse:
    """Return recent episodes and DecisionCases for ``property_id``.

    Useful for verifying that a freshly bootstrapped property is
    actually feeding the memory layer rather than silently dropping
    data on the floor.
    """
    episodic = _deps.get("episodic_memory")
    case_store = _deps.get("case_store")

    return JSONResponse(
        content={
            "property_id": property_id,
            "episodes": await _recent_episodes(episodic, episodes),
            "cases": await _recent_cases(
                case_store,
                property_id=property_id,
                limit=cases,
            ),
        }
    )


# ---------------------------------------------------------------------------
# Tier helpers — each tolerates a missing dependency by returning an
# explicit "ready: false" block rather than raising.
# ---------------------------------------------------------------------------


async def _episodic_block(memory: Any) -> dict[str, Any]:
    """Summarise the episodic memory tier."""
    if not isinstance(memory, EpisodicMemory):
        return {"ready": False, "reason": "episodic_memory not wired"}
    try:
        recent = await memory.get_recent(5)
    except Exception as exc:  # noqa: BLE001 - status must not crash
        logger.exception("memory_status.episodic_failed")
        return {
            "ready": False,
            "reason": f"{exc.__class__.__name__}: {exc}",
        }
    return {
        "ready": True,
        "session_id": getattr(memory, "session_id", ""),
        "recent_sample_size": len(recent),
        "latest_event": (
            recent[0].event if recent else None
        ),
        "latest_timestamp": (
            recent[0].timestamp.isoformat() if recent else None
        ),
    }


async def _case_block(store: Any) -> dict[str, Any]:
    """Summarise the DecisionCase store."""
    if not isinstance(store, DecisionCaseStore):
        return {"ready": False, "reason": "case_store not wired"}
    try:
        total = await store.count()
    except Exception as exc:  # noqa: BLE001 - status must not crash
        logger.exception("memory_status.case_count_failed")
        return {
            "ready": False,
            "reason": f"{exc.__class__.__name__}: {exc}",
        }
    return {
        "ready": True,
        "total": int(total),
        "store_class": store.__class__.__name__,
    }


async def _rule_block(store: Any) -> dict[str, Any]:
    """Summarise the PatternRule store."""
    if not isinstance(store, PatternRuleStore):
        return {"ready": False, "reason": "rule_store not wired"}
    try:
        active = await store.get_active_rules()
    except Exception as exc:  # noqa: BLE001 - status must not crash
        logger.exception("memory_status.rule_list_failed")
        return {
            "ready": False,
            "reason": f"{exc.__class__.__name__}: {exc}",
        }
    by_mode: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    for rule in active:
        mode_key = getattr(rule.execution_mode, "value", str(
            rule.execution_mode
        ))
        scope_key = getattr(rule.scope, "value", str(rule.scope))
        by_mode[mode_key] = by_mode.get(mode_key, 0) + 1
        by_scope[scope_key] = by_scope.get(scope_key, 0) + 1
    return {
        "ready": True,
        "active": len(active),
        "by_mode": by_mode,
        "by_scope": by_scope,
        "store_class": store.__class__.__name__,
    }


async def _recent_episodes(
    memory: Any,
    limit: int,
) -> list[dict[str, Any]]:
    """Return the last ``limit`` episodes as plain dicts."""
    if not isinstance(memory, EpisodicMemory):
        return []
    try:
        recent = await memory.get_recent(limit)
    except Exception:  # noqa: BLE001 - status must not crash
        logger.exception("memory_status.recent_episodes_failed")
        return []
    return [ep.to_dict() for ep in recent]


async def _recent_cases(
    store: Any,
    *,
    property_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return the most recent DecisionCases for a property."""
    if not isinstance(store, DecisionCaseStore):
        return []
    try:
        cases = await store.search(
            property_id=property_id,
            limit=limit,
        )
    except Exception:  # noqa: BLE001 - status must not crash
        logger.exception("memory_status.recent_cases_failed")
        return []
    cases.sort(
        key=lambda c: getattr(c, "created_at", None) or 0,
        reverse=True,
    )
    return [_case_summary(c) for c in cases[:limit]]


def _case_summary(case: Any) -> dict[str, Any]:
    """Project a DecisionCase down to a small dashboard tile."""
    return {
        "case_id": getattr(case, "case_id", ""),
        "scenario": getattr(case.scenario, "value", str(case.scenario)),
        "stage": getattr(case.stage, "value", str(case.stage)),
        "decision": getattr(
            case.decision_type,
            "value",
            str(case.decision_type),
        ),
        "created_at": (
            case.created_at.isoformat()
            if getattr(case, "created_at", None) is not None
            else None
        ),
    }
