"""Service-level objective constants for Brain Engine.

Reference: ``brain_engine_advisory.md`` §5 — SLOs section.

Lifting the numbers into a single module gives:

* one place to bump a target when the floor moves;
* a deterministic source for the alert rule generator
  (``deploy/alerts/*.yml`` is generated from this table);
* a stable place for the runbook to point to.

All values are *targets*, not *guarantees*.  The pipeline gate that
fails on a regression lives in benchmark CI (advisory §1.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class SLO:
    """A single objective; ``target`` is interpreted by ``unit``."""

    name: str
    target: float
    unit: str
    rationale: str


SLOS: Final[tuple[SLO, ...]] = (
    SLO(
        name="l1_latency_p95_seconds",
        target=0.6,
        unit="seconds",
        rationale=(
            "L1 instinct turns must feel instantaneous; >600 ms "
            "breaks the conversational rhythm."
        ),
    ),
    SLO(
        name="l3_latency_p95_seconds",
        target=3.5,
        unit="seconds",
        rationale=(
            "L3 experience tolerates a longer turn but >3.5 s "
            "starts looking like a stall to the operator."
        ),
    ),
    SLO(
        name="llm_cost_per_booking_usd",
        target=0.15,
        unit="usd",
        rationale=(
            "Unit-economics floor for V1 pricing model; revisit "
            "when pricing tier changes."
        ),
    ),
    SLO(
        name="memory_retrieval_p99_seconds",
        target=0.1,
        unit="seconds",
        rationale=(
            "P99 because P95 is too generous on latency-tail-"
            "sensitive cognitive turns."
        ),
    ),
    SLO(
        name="skill_evolution_success_rate",
        target=0.85,
        unit="ratio",
        rationale=(
            "<85% success means SkillEvolution's quality gate is "
            "either too lax (evolves bad rules) or too strict "
            "(blocks good ones)."
        ),
    ),
    SLO(
        name="approval_response_p95_seconds",
        target=300.0,
        unit="seconds",
        rationale=(
            "Five-minute floor during business hours; off-hours "
            "queues are tracked separately."
        ),
    ),
)


def slo_by_name(name: str) -> SLO:
    """Lookup helper used by the alert rule generator."""
    for slo in SLOS:
        if slo.name == name:
            return slo
    raise KeyError(f"unknown SLO {name!r}")
