"""Read-only past-conversation viewer.

Reference: ``brain_engine_advisory.md`` §3 + Monday-2026-04-27
PM-test feedback ("we need an endpoint that shows us how a past
conversation has been analyzed — by stage, by guardrail, by
decision").

Three endpoints under ``/api/admin/past-conversations``:

* ``GET /{reservation_id}/analysis`` — single past conversation,
  every :class:`~brain_engine.patterns.models.DecisionCase`
  associated with it, the refusal/guardrail signals
  :class:`~brain_engine.patterns.refusal_extractor.RefusalExtractor`
  surfaces from the PM responses, and a stage histogram.
* ``GET /{reservation_id}/by-stage`` — same data, but bucketed
  into the nine booking lifecycle stages so the dashboard can
  render the timeline directly.
* ``GET /`` — list reservations that have at least one DecisionCase,
  optionally filtered by ``property_id`` / ``owner_id``, ordered by
  the most recent case time.

The router is purely read-only — never writes, never mutates state.
Missing dependencies degrade gracefully (``ready: false`` rather
than 5xx) so a half-configured pod still answers dashboard probes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import JSONResponse

from brain_engine.patterns.models import BookingStage, DecisionCase
from brain_engine.patterns.refusal_extractor import (
    RefusalExtractor,
    RefusalSignal,
)
from brain_engine.patterns.store import DecisionCaseStore


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/admin/past-conversations",
    tags=["Intelligence"],
)


_deps: dict[str, Any] = {"case_store": None}
_EXTRACTOR = RefusalExtractor()


def configure_deps(deps: dict[str, Any]) -> None:
    """Publish lifespan-built dependencies into the router scope."""
    _deps.update(deps)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{reservation_id}/analysis",
    summary="Full analysis of a single past conversation",
)
async def conversation_analysis(
    reservation_id: str = Path(..., min_length=1),
) -> JSONResponse:
    """Return all DecisionCases + refusal signals for a reservation.

    The response shape is:

    ::

        {
          "reservation_id": "...",
          "case_count": 4,
          "stages": {"pre_arrival": 1, "checkin": 2, ...},
          "languages": {"en": 3, "tr": 1},
          "cases": [ <case summary>, ... ],
          "refusal_signals": [ <signal summary>, ... ],
          "guardrail_summary": {
              "requires_document": 2,
              "requires_payment": 1,
          }
        }

    Returns ``404`` only when the case store has zero cases for the
    reservation.  Missing case-store dependency surfaces a ``503``
    so the caller can distinguish "pod misconfigured" from "no
    history yet".
    """
    store = _deps.get("case_store")
    if not isinstance(store, DecisionCaseStore):
        raise HTTPException(
            status_code=503,
            detail="case_store dependency not wired",
        )

    cases = await _safe_get_by_reservation(store, reservation_id)
    if not cases:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no decision cases for reservation {reservation_id}"
            ),
        )

    cases.sort(key=lambda c: c.created_at)
    signals = _collect_signals(cases)
    return JSONResponse(
        content={
            "reservation_id": reservation_id,
            "case_count": len(cases),
            "stages": _histogram(c.stage.value for c in cases),
            "languages": _histogram(
                (c.message_language or "en") for c in cases
            ),
            "cases": [_case_summary(c) for c in cases],
            "refusal_signals": [
                _signal_summary(case_id, sig)
                for case_id, sig in signals
            ],
            "guardrail_summary": _guardrail_summary(signals),
        },
    )


@router.get(
    "/{reservation_id}/by-stage",
    summary="DecisionCases bucketed into the 9-stage timeline",
)
async def conversation_by_stage(
    reservation_id: str = Path(..., min_length=1),
) -> JSONResponse:
    """Group DecisionCases for a reservation by ``BookingStage``.

    Returns one entry per stage even when empty, so the front-end
    can render a stable timeline scaffold.
    """
    store = _deps.get("case_store")
    if not isinstance(store, DecisionCaseStore):
        raise HTTPException(
            status_code=503,
            detail="case_store dependency not wired",
        )

    cases = await _safe_get_by_reservation(store, reservation_id)
    cases.sort(key=lambda c: c.created_at)

    buckets: dict[str, list[dict[str, Any]]] = {
        stage.value: [] for stage in BookingStage
    }
    for case in cases:
        buckets[case.stage.value].append(_case_summary(case))

    return JSONResponse(
        content={
            "reservation_id": reservation_id,
            "case_count": len(cases),
            "by_stage": buckets,
        },
    )


@router.get(
    "",
    summary="List reservations that have at least one DecisionCase",
)
async def list_past_conversations(
    property_id: str | None = Query(
        default=None,
        min_length=1,
        description="Filter by property identifier.",
    ),
    owner_id: str | None = Query(
        default=None,
        min_length=1,
        description="Filter by property-owner identifier.",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of reservations to return.",
    ),
) -> JSONResponse:
    """List reservations carrying DecisionCases, newest first."""
    store = _deps.get("case_store")
    if not isinstance(store, DecisionCaseStore):
        raise HTTPException(
            status_code=503,
            detail="case_store dependency not wired",
        )

    try:
        cases = await store.search(
            property_id=property_id,
            owner_id=owner_id,
            limit=max(limit * 4, limit),
        )
    except Exception as exc:  # noqa: BLE001 - read endpoint
        logger.exception("past_conversations.search_failed")
        raise HTTPException(
            status_code=500,
            detail=(
                f"case_store.search failed: "
                f"{exc.__class__.__name__}"
            ),
        ) from exc

    grouped: dict[str, list[DecisionCase]] = defaultdict(list)
    for case in cases:
        if case.reservation_id is None:
            continue
        grouped[case.reservation_id].append(case)

    summaries: list[dict[str, Any]] = []
    for reservation_id, group in grouped.items():
        group.sort(key=lambda c: c.created_at)
        latest = group[-1]
        summaries.append(
            {
                "reservation_id": reservation_id,
                "property_id": latest.property_id,
                "owner_id": latest.owner_id,
                "case_count": len(group),
                "first_case_at": group[0].created_at.isoformat(),
                "last_case_at": latest.created_at.isoformat(),
                "stages": _histogram(c.stage.value for c in group),
            },
        )

    summaries.sort(key=lambda s: s["last_case_at"], reverse=True)
    summaries = summaries[:limit]

    return JSONResponse(
        content={
            "filter": {
                "property_id": property_id,
                "owner_id": owner_id,
            },
            "count": len(summaries),
            "reservations": summaries,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _safe_get_by_reservation(
    store: DecisionCaseStore,
    reservation_id: str,
) -> list[DecisionCase]:
    """Wrap ``DecisionCaseStore.get_by_reservation`` with logging."""
    try:
        cases = await store.get_by_reservation(reservation_id)
    except Exception as exc:  # noqa: BLE001 - read endpoint
        logger.exception(
            "past_conversations.get_by_reservation_failed",
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"case_store.get_by_reservation failed: "
                f"{exc.__class__.__name__}"
            ),
        ) from exc
    return list(cases)


def _collect_signals(
    cases: list[DecisionCase],
) -> list[tuple[str, RefusalSignal]]:
    """Run the refusal extractor over every PM ``response_text``.

    Returns a flat list of ``(case_id, signal)`` pairs in case-order,
    which the response shape projects into the ``refusal_signals``
    array.  We intentionally walk the *response* text — that is the
    PM-authored side and the surface the Monday feedback flagged
    ("the PM refusing the door code without a passport").  Guest
    inbound text is not classified here because a refusal classifier
    fired against a guest message would mis-interpret intent.
    """
    out: list[tuple[str, RefusalSignal]] = []
    for case in cases:
        if not case.response_text:
            continue
        for sig in _EXTRACTOR.extract(case.response_text):
            out.append((case.case_id, sig))
    return out


def _signal_summary(
    case_id: str, signal: RefusalSignal,
) -> dict[str, Any]:
    """Project a :class:`RefusalSignal` into a JSON-safe dict."""
    return {
        "case_id": case_id,
        "refusal_type": signal.refusal_type.value,
        "language": signal.language.value,
        "trigger_phrase": signal.trigger_phrase,
        "conditional_clause": signal.conditional_clause,
        "confidence": signal.confidence,
    }


def _guardrail_summary(
    signals: list[tuple[str, RefusalSignal]],
) -> dict[str, int]:
    """Count refusal signals per :class:`RefusalType`."""
    counts: dict[str, int] = defaultdict(int)
    for _case_id, signal in signals:
        counts[signal.refusal_type.value] += 1
    return dict(counts)


def _case_summary(case: DecisionCase) -> dict[str, Any]:
    """Project a :class:`DecisionCase` into a dashboard tile dict."""
    return {
        "case_id": case.case_id,
        "stage": case.stage.value,
        "scenario": case.scenario.value,
        "decision": case.decision.action_type.value,
        "language": case.message_language or "en",
        "message_excerpt": _excerpt(case.message_text),
        "response_excerpt": _excerpt(case.response_text),
        "created_at": case.created_at.isoformat(),
        "executed_actions": list(case.executed_actions),
        "human_overrode": case.outcome.human_overrode,
        "successful": case.outcome.successful,
        "source": case.source.value,
    }


def _excerpt(text: str, *, limit: int = 280) -> str:
    """Trim a long body to the first ``limit`` characters."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def _histogram(values: Any) -> dict[str, int]:
    """Count occurrences of hashable ``values`` into a sorted dict."""
    counts: dict[str, int] = defaultdict(int)
    for v in values:
        counts[str(v)] += 1
    return dict(sorted(counts.items()))


__all__ = ("configure_deps", "router")
