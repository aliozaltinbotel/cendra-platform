"""Read + diagnostic surface for the Foundation Layer (FL-16).

Operators and PMs need a single Postman-friendly place to confirm
that the foundation catalogue is loaded, the orchestrator wiring is
live, and any sample text routes to the expected scenarios — without
shell access to the pod.

Three endpoints are exposed under ``/api/admin/foundation``:

* ``GET /status`` — global snapshot.  Reports the loaded scenario
  count, the markdown source path, the byte size of the file on
  disk, and the current state of every Phase-2 feature flag.
* ``POST /analyze`` — ad-hoc diagnostic.  Body carries an arbitrary
  guest message text plus optional ``property_id`` /
  ``reservation_id`` / ``guest_id`` context.  The router runs the
  Foundation Analysis Orchestrator standalone (independent of the
  guest conversation pipeline) and returns the full
  :class:`AnalysisResult`: top-K candidates with similarity scores,
  the dominant scenario, would-be guardrail decision, would-be
  memory routes and the provenance trail.
* ``GET /coverage`` — provenance dashboard for one property.
  Reports the total DecisionCase count, how many already carry a
  ``foundation_scenario_id`` (the W5/W1 chain), the top-N
  foundation scenarios by case coverage, and the rule-level
  provenance ratio.  Lets a tester confirm at a glance whether the
  matcher→case→rule pipeline is actually populating provenance on
  real traffic — without kubectl or DB shell.

Every endpoint is read-only with respect to persistent state — the
orchestrator's ``log_origin`` step does not write to any store on
this code path; the returned origin is for inspection only.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from brain_engine.analysis.models import (
    AnalysisEvent,
    AnalysisEventType,
)
from brain_engine.patterns.stage_labels import (
    format_stage_group,
    lookup_stage_short,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/admin/foundation",
    tags=["Intelligence"],
)


_deps: dict[str, Any] = {
    "orchestrator": None,
    "scenarios_count": 0,
    "foundation_path": "",
    "case_store": None,
    "rule_store": None,
}


def configure_deps(deps: dict[str, Any]) -> None:
    """Publish lifespan-built dependencies into the router scope.

    Mirrors :func:`api_server.routers.memory_status.configure_deps`:
    re-entrant so test fixtures can swap stacks between runs, atomic
    so a partially-wired pod can still answer the status probe with
    explicit ``ready: false`` blocks.
    """
    _deps.update(deps)


# ── Pydantic request / response shapes ─────────────────────────── #


class AnalyzeRequest(BaseModel):
    """Body for ``POST /api/admin/foundation/analyze``.

    Only ``text`` is required.  Optional context fields are passed
    through to the synthesised :class:`AnalysisEvent` so the future
    FL-04 router / FL-05 guardrail can see per-property overrides
    when those steps consult them.
    """

    text: str = Field(
        ...,
        min_length=1,
        description=(
            "Guest message text to analyse.  Required.  The matcher "
            "embeds this verbatim against the 469-scenario reactive "
            "foundation catalogue."
        ),
    )
    property_id: str = Field(
        default="",
        description=(
            "Optional property identifier.  Echoed into the synthesised "
            "AnalysisEvent so downstream stubs that read it (none in "
            "Sprint 6) can act on it.  Defaults to an empty string when "
            "absent."
        ),
    )
    reservation_id: str | None = Field(
        default=None,
        description="Optional reservation identifier.",
    )
    guest_id: str | None = Field(
        default=None,
        description="Optional guest identifier.",
    )


# ── env-flag helpers ────────────────────────────────────────────── #


_FALSY: frozenset[str] = frozenset({"", "0", "false", "no", "off"})


def _flag_on(name: str) -> bool:
    """Return whether a Phase-2 feature flag is currently enabled.

    Read on every call so an operator can flip ``BRAIN_FOUNDATION_*``
    without bouncing the pod.  Matches the parsing rules used inside
    the conversation service.
    """
    raw = os.environ.get(name, "").strip().lower()
    return raw not in _FALSY


def _phase2_flags() -> dict[str, bool]:
    """Snapshot of every Foundation Phase-2 flag the operator can toggle.

    Order matches the documented rollout: orchestrator first
    (observation only), then guardrail (enforcement), then learn
    gate (mining filter).  Keeping the key set stable lets a CI
    watcher diff against a baseline.
    """
    return {
        "orchestrator_enabled": _flag_on(
            "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        ),
        "guardrail_enabled": _flag_on(
            "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        ),
        "learn_gate_enabled": _flag_on(
            "BRAIN_FOUNDATION_LEARN_GATE_ENABLED",
        ),
    }


# ── projection helpers ─────────────────────────────────────────── #


def _project_catalog_entry(entry: Any) -> dict[str, Any]:
    """Project a :class:`FoundationScenario` to the **full** 18-field row.

    Mirrors the 18 columns of the canonical
    ``FOUNDATION_469_SCENARIOS.xlsx`` reference deliverable so an
    operator / PM can read every field of the matched scenario from
    the API response.  Earlier versions returned only an 8-field
    subset — Mümin's feedback was that the response had to contain
    the full row for verification against the reference workbook.

    The 18 columns and their FoundationScenario sources:

      Excel column           → FoundationScenario field
      ------------------------------------------------------------
      Stage Group            → ``stage_label`` (e.g. "Stage 1 — …")
      Scenario Title         → ``title``
      Stage                  → ``stage_label`` (same source)
      Trigger                → ``trigger``
      Signals to Inspect     → ``signals_to_inspect`` (list)
      Risk Level             → ``risk_level``
      AI Default Behavior    → ``ai_default_behavior``
      Required Data Checks   → ``required_data_checks`` (list)
      Should AI Auto-Reply?  → ``should_auto_reply``
      Should AI Escalate?    → ``should_escalate_to_pm``
      Should AI Create Task? → ``should_create_task``
      Should AI Learn?       → ``should_learn_pattern``
      Pattern to Learn       → ``pattern_to_learn``
      Example Learned        → ``example_learned_pattern``
      Memory Type            → ``memory_types`` (list)
      What Not to Learn      → ``what_not_to_learn``
      Future Behavior Impact → ``future_behavior_impact``

    Plus the technical id ``scenario_id`` and the numeric
    ``stage_number`` that callers use to filter / aggregate.
    """
    if entry is None:
        return {}
    stage_number = getattr(entry, "stage_number", None)
    stage_label = getattr(entry, "stage_label", "")
    return {
        "scenario_id": getattr(entry, "scenario_id", ""),
        "title": getattr(entry, "title", ""),
        "stage_number": stage_number,
        "stage_label": stage_label,
        # Additive: ``stage_group`` is the Excel long form
        # ``"Stage N — <label>"`` and ``stage`` is the Excel short
        # form per ``stage_number``.  Both come from the shared
        # :mod:`brain_engine.patterns.stage_labels` helper so the
        # ``/patterns/rules`` listing emits the same strings.
        "stage_group": format_stage_group(stage_number, stage_label),
        "stage": lookup_stage_short(stage_number, stage_label),
        "trigger": getattr(entry, "trigger", ""),
        "signals_to_inspect": list(
            getattr(entry, "signals_to_inspect", ()) or (),
        ),
        "risk_level": getattr(entry, "risk_level", ""),
        "ai_default_behavior": getattr(
            entry,
            "ai_default_behavior",
            "",
        ),
        "required_data_checks": list(
            getattr(entry, "required_data_checks", ()) or (),
        ),
        "should_auto_reply": getattr(entry, "should_auto_reply", ""),
        "should_escalate_to_pm": getattr(
            entry,
            "should_escalate_to_pm",
            "",
        ),
        "should_create_task": getattr(
            entry,
            "should_create_task",
            "",
        ),
        "should_learn_pattern": getattr(
            entry,
            "should_learn_pattern",
            "",
        ),
        "pattern_to_learn": getattr(entry, "pattern_to_learn", ""),
        "example_learned_pattern": getattr(
            entry,
            "example_learned_pattern",
            "",
        ),
        "memory_types": list(
            getattr(entry, "memory_types", ()) or (),
        ),
        "what_not_to_learn": getattr(entry, "what_not_to_learn", ""),
        "future_behavior_impact": getattr(
            entry,
            "future_behavior_impact",
            "",
        ),
    }


def _project_candidate(candidate: Any) -> dict[str, Any]:
    """Reduce a :class:`FoundationMatchCandidate` to the JSON wire.

    The full :class:`FoundationScenario` lives under
    ``catalog_entry`` so downstream tooling can drill in without a
    second round-trip to the catalogue store.
    """
    return {
        "scenario_id": getattr(candidate, "scenario_id", ""),
        "similarity": float(getattr(candidate, "similarity", 0.0)),
        "catalog_entry": _project_catalog_entry(
            getattr(candidate, "catalog_entry", None),
        ),
    }


def _project_origin(origin: Any) -> dict[str, Any]:
    """Reduce a :class:`PatternOrigin` to JSON-safe lists.

    ``contributing_signal_ids`` stays an empty list in Sprint 6;
    FL-09 (deferred Proactive layer) will populate it once the
    :class:`ProactiveSignal` store lands.
    """
    if origin is None:
        return {
            "foundation_scenario_ids": [],
            "source_event_ids": [],
            "contributing_signal_ids": [],
        }
    return {
        "foundation_scenario_ids": list(
            getattr(origin, "foundation_scenario_ids", ()) or (),
        ),
        "source_event_ids": list(
            getattr(origin, "source_event_ids", ()) or (),
        ),
        "contributing_signal_ids": list(
            getattr(origin, "contributing_signal_ids", ()) or (),
        ),
    }


# ── GET /status ────────────────────────────────────────────────── #


@router.get(
    "/status",
    summary="Foundation Layer subsystem snapshot",
)
async def foundation_status() -> JSONResponse:
    """Return the catalogue load state + every Phase-2 flag.

    Always returns 200 — a half-wired pod reports ``ready: false``
    with an explicit reason rather than raising, so the response is
    safe to poll from a dashboard.
    """
    orchestrator = _deps.get("orchestrator")
    scenarios_count = int(_deps.get("scenarios_count") or 0)
    foundation_path = str(_deps.get("foundation_path") or "")

    md_block = _md_block(foundation_path)
    catalog_block = {
        "loaded": scenarios_count > 0,
        "scenarios_count": scenarios_count,
    }
    orchestrator_block = {
        "wired": orchestrator is not None,
    }

    return JSONResponse(
        content={
            "ready": (scenarios_count > 0 and orchestrator is not None),
            "catalog": catalog_block,
            "orchestrator": orchestrator_block,
            "markdown": md_block,
            "flags": _phase2_flags(),
        },
    )


def _md_block(foundation_path: str) -> dict[str, Any]:
    """Filesystem probe for the foundation markdown.

    The path itself is taken from the dependency-injected value so
    test fixtures can point at a different file without monkey-
    patching :data:`DEFAULT_FOUNDATION_PATH`.
    """
    if not foundation_path:
        return {"present": False, "reason": "foundation_path not wired"}
    md_path = Path(foundation_path)
    if not md_path.is_file():
        return {
            "present": False,
            "path": str(md_path),
            "reason": "file not found inside the pod",
        }
    return {
        "present": True,
        "path": str(md_path),
        "size_bytes": md_path.stat().st_size,
    }


# ── POST /analyze ──────────────────────────────────────────────── #


@router.post(
    "/analyze",
    summary="Run a single guest message through the Foundation pipeline",
)
async def foundation_analyze(body: AnalyzeRequest) -> JSONResponse:
    """Return the full :class:`AnalysisResult` for an ad-hoc message.

    Decoupled from the live guest conversation pipeline so a tester
    can paste any text and inspect what the matcher + catalogue
    would do.  Persists nothing — the origin trail returned is for
    inspection only.

    Returns:
        ``200`` with the projected result when the orchestrator is
        wired.  ``503`` when no orchestrator is available — that
        means the foundation catalogue did not load (most often: MD
        missing inside the pod).
    """
    orchestrator = _deps.get("orchestrator")
    if orchestrator is None or not callable(
        getattr(orchestrator, "analyze", None),
    ):
        return JSONResponse(
            status_code=503,
            content={
                "error": "foundation_orchestrator_not_wired",
                "detail": (
                    "The Foundation Analysis Orchestrator is not "
                    "available on this pod.  Most likely the "
                    "foundation markdown failed to load — see "
                    "GET /api/admin/foundation/status."
                ),
            },
        )

    event = AnalysisEvent(
        event_id=str(uuid4()),
        event_type=AnalysisEventType.MESSAGE,
        property_id=body.property_id,
        occurred_at=datetime.now(UTC),
        text=body.text,
        payload={},
        reservation_id=body.reservation_id,
        guest_id=body.guest_id,
    )

    try:
        result = await orchestrator.analyze(event)
    except Exception as exc:
        logger.exception("foundation_audit.analyze_failed")
        return JSONResponse(
            status_code=500,
            content={
                "error": "foundation_orchestrator_failed",
                "detail": f"{exc.__class__.__name__}: {exc}",
            },
        )

    match = getattr(result, "foundation_match", None)
    candidates = [
        _project_candidate(c) for c in (getattr(match, "candidates", ()) or ())
    ]
    return JSONResponse(
        content={
            "event_id": event.event_id,
            "input": {
                "text": body.text,
                "property_id": body.property_id,
                "reservation_id": body.reservation_id,
                "guest_id": body.guest_id,
            },
            "match": {
                "candidates": candidates,
                "candidates_count": len(candidates),
                "dominant_scenario_id": getattr(
                    match,
                    "dominant_scenario_id",
                    None,
                ),
                "dominant_catalog_entry": _project_catalog_entry(
                    getattr(match, "dominant_catalog_entry", None),
                ),
            },
            "decisions": {
                "guardrail_block": bool(
                    getattr(result, "guardrail_block", False),
                ),
                "pattern_candidate_emitted": bool(
                    getattr(result, "pattern_candidate_emitted", False),
                ),
                "memory_routes": list(
                    getattr(result, "memory_routes", ()) or (),
                ),
                # Q5-B observability: the verbatim catalog labels
                # whose target snapshot was empty on the event.
                # Empty tuple when no checks defined / every
                # mappable check satisfied / Q5-A trip cleared
                # dominant entry.
                "missing_required_data": list(
                    getattr(result, "missing_required_data", ()) or (),
                ),
                # Q5-C observability: True when the orchestrator's
                # ``_detect_stage_contradiction`` step detected a
                # hard mismatch between the booking stage implied
                # by the event's calendar and the stage the matched
                # scenario expects.  Detail string carries the
                # stable ``"calendar=<stage> scenario=<stage>"``
                # format so the PM Chat UI / Mümin's regression
                # harness can pattern-match on it.
                "stage_mismatch": bool(
                    getattr(result, "stage_mismatch", False),
                ),
                "stage_mismatch_detail": str(
                    getattr(result, "stage_mismatch_detail", "") or "",
                ),
            },
            "origin": _project_origin(
                getattr(result, "origin", None),
            ),
            "flags": _phase2_flags(),
        },
    )


# ── GET /coverage ──────────────────────────────────────────────── #


_DEFAULT_COVERAGE_SAMPLE: int = 2000
_DEFAULT_COVERAGE_TOP_N: int = 10


@router.get(
    "/coverage",
    summary="Per-property foundation provenance coverage snapshot",
)
async def foundation_coverage(
    property_id: str,
    sample: int = _DEFAULT_COVERAGE_SAMPLE,
    top_n: int = _DEFAULT_COVERAGE_TOP_N,
) -> JSONResponse:
    """Return foundation provenance stats for ``property_id``.

    The endpoint loads up to ``sample`` recent DecisionCases for the
    property and computes:

    * total case count (independent of the sample size, via
      :meth:`DecisionCaseStore.count`)
    * sample size actually inspected
    * sample-level ratio of cases carrying a
      ``foundation_scenario_id``
    * top-N foundation scenarios by case frequency in the sample
    * rule-level provenance ratio (active rules with
      ``foundation_scenario_id`` populated vs total active rules
      for the property)

    Useful for confirming the W1/W5 chain after PR #288 lands —
    even when ``rules_emitted: 0`` after a fresh bootstrap, the
    case-level ratio shows whether the matcher is actually firing
    and propagating provenance.

    Returns:
        ``200`` with the stats payload.  ``503`` when the case store
        is not wired (in that case foundation provenance cannot be
        measured at all).
    """
    case_store = _deps.get("case_store")
    rule_store = _deps.get("rule_store")
    if case_store is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "case_store_not_wired",
                "detail": (
                    "DecisionCaseStore is not available on this pod. "
                    "Coverage requires Postgres-backed storage."
                ),
            },
        )

    sample = max(1, min(sample, 10_000))
    top_n = max(1, min(top_n, 100))

    try:
        total_cases = await case_store.count(
            property_id=property_id,
        )
    except Exception:
        logger.exception("foundation_coverage.count_failed")
        total_cases = -1

    sample_cases = []
    try:
        sample_cases = await case_store.search(
            property_id=property_id,
            limit=sample,
        )
    except Exception:
        logger.exception("foundation_coverage.search_failed")

    inspected = len(sample_cases)
    with_foundation = 0
    scenario_counts: dict[str, int] = {}
    for case in sample_cases:
        slug = getattr(case, "foundation_scenario_id", None)
        if slug:
            with_foundation += 1
            scenario_counts[slug] = scenario_counts.get(slug, 0) + 1

    top_scenarios = sorted(
        scenario_counts.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )[:top_n]

    rules_block = await _rules_block(rule_store, property_id)

    coverage_pct = (
        round(100.0 * with_foundation / inspected, 2) if inspected else 0.0
    )

    return JSONResponse(
        content={
            "property_id": property_id,
            "cases": {
                "total": total_cases,
                "sample_size": inspected,
                "with_foundation_scenario_id": with_foundation,
                "coverage_pct": coverage_pct,
            },
            "top_scenarios": [
                {"scenario_id": slug, "case_count": count}
                for slug, count in top_scenarios
            ],
            "rules": rules_block,
            "flags": _phase2_flags(),
        },
    )


async def _rules_block(
    rule_store: Any,
    property_id: str,
) -> dict[str, Any]:
    """Summarise rule-level provenance for ``property_id``.

    Returns a ``{"available": False, ...}`` block when the rule
    store is missing or the call raises, so the rest of the
    coverage payload still surfaces.
    """
    if rule_store is None:
        return {
            "available": False,
            "reason": "rule_store_not_wired",
        }
    try:
        active = await rule_store.get_active_rules()
    except Exception as exc:
        logger.exception("foundation_coverage.rule_query_failed")
        return {
            "available": False,
            "reason": f"{exc.__class__.__name__}: {exc}",
        }
    matching: list[Any] = []
    for rule in active:
        if getattr(rule, "scope_id", None) != property_id:
            continue
        matching.append(rule)
    total = len(matching)
    with_foundation = sum(
        1 for r in matching if getattr(r, "foundation_scenario_id", None)
    )
    return {
        "available": True,
        "total": total,
        "with_foundation_scenario_id": with_foundation,
        "coverage_pct": (
            round(100.0 * with_foundation / total, 2) if total else 0.0
        ),
    }
