"""Foundation analyser — learns per-scenario feature importance (Sprint I).

Given a batch of :class:`brain_engine.patterns.models.DecisionCase`
rows for a ``(property_id, scenario)`` pair, the analyser:

1. Splits cases into ``target`` (cases whose action is the dominant
   one we want to learn) and ``other`` (counterexamples).
2. Flattens both buckets into the same feature surface used by the
   Wilson :class:`ConditionSynthesizer` so the importance landscape
   is comparable to the conditions actually mineable downstream.
3. Hands ``(target_features, other_features)`` to a
   :class:`MlSynthesizer` which fits a shallow tree and returns
   normalised importances.
4. Persists the result in a :class:`FoundationStore` keyed by
   ``(property_id, scenario, feature_name)`` so the synthesiser can
   read it on the hot path.

The analyser is intentionally storage- and scheduler-agnostic.  The
nightly job that selects properties, fetches recent cases and
invokes the analyser is a separate ticket; this module is the unit
that does the actual learning + persistence step.

Gated by ``BRAIN_FOUNDATION_ANALYZER_ENABLED``; when the flag is
off, callers are expected to skip the analyser entirely so the
hardcoded Sprint H whitelist remains authoritative.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from brain_engine.patterns.condition_synthesizer import _flatten
from brain_engine.patterns.foundation_store import (
    FoundationStore,
    ScenarioFoundation,
)
from brain_engine.patterns.ml_synthesizer import (
    MlSynthesisResult,
    MlSynthesizer,
)
from brain_engine.patterns.models import DecisionCase, DecisionType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants and flag plumbing
# ---------------------------------------------------------------------------


_ANALYZER_FLAG_ENV: Final[str] = "BRAIN_FOUNDATION_ANALYZER_ENABLED"
_REFRESH_DAYS_ENV: Final[str] = "BRAIN_FOUNDATION_REFRESH_DAYS"

# Refresh cadence default — once a week is enough at PM-decision
# velocity (10s of cases / property / week).  Operators can shorten
# the window for high-volume properties via the env var.
DEFAULT_REFRESH_DAYS: Final[int] = 7


def foundation_analyzer_enabled() -> bool:
    """Whether the Sprint I foundation analyser is active.

    Read on every call so a deploy can flip
    ``BRAIN_FOUNDATION_ANALYZER_ENABLED`` without restarting the
    pod.  Default off — the Sprint H static whitelist remains
    authoritative until the team explicitly opts in.
    """
    raw = os.environ.get(_ANALYZER_FLAG_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def configured_refresh_days() -> int:
    """Return the freshness window for cached foundations.

    Honours ``BRAIN_FOUNDATION_REFRESH_DAYS`` for per-tenant tuning;
    raises :class:`ValueError` for malformed values rather than
    silently corrupting freshness checks.
    """
    raw = os.environ.get(_REFRESH_DAYS_ENV, "").strip()
    if not raw:
        return DEFAULT_REFRESH_DAYS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{_REFRESH_DAYS_ENV} must be a positive integer, "
            f"got {raw!r}",
        ) from exc
    if value <= 0:
        raise ValueError(
            f"{_REFRESH_DAYS_ENV} must be a positive integer, "
            f"got {value}",
        )
    return value


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FoundationAnalysisOutcome:
    """Diagnostic counters returned alongside the persistence count.

    Attributes:
        property_id: Property the analysis covered.
        scenario: Scenario StrEnum value the analysis covered.
        target_count: DecisionCases the analyser treated as targets.
        other_count: DecisionCases treated as counterexamples.
        rows_written: Number of importance rows persisted.
        skipped_reason: Populated when the analyser short-circuited
            (empty bucket, too little data) and wrote nothing.
            ``None`` on a successful refresh.
    """

    property_id: str
    scenario: str
    target_count: int
    other_count: int
    rows_written: int
    skipped_reason: str | None = None


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class FoundationAnalyzer:
    """Drives one ``(property_id, scenario)`` analysis pass.

    Args:
        store: Where importance rows land after fitting.
        synthesizer: ML model wrapper that turns
            ``(target_features, other_features)`` into normalised
            feature importances.
        clock: Time source.  Defaults to ``datetime.now(timezone.utc)``;
            tests inject a frozen clock for deterministic timestamps.

    The analyser is single-pair: each call covers exactly one
    ``(property_id, scenario, target_action)`` slice.  A nightly
    runner (separate ticket) iterates over properties / scenarios
    and calls :meth:`analyze` per slice.
    """

    def __init__(
        self,
        *,
        store: FoundationStore,
        synthesizer: MlSynthesizer,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._synthesizer = synthesizer
        self._clock = clock or _utcnow

    async def analyze(
        self,
        *,
        property_id: str,
        scenario: str,
        cases: Sequence[DecisionCase],
        target_action: DecisionType,
    ) -> FoundationAnalysisOutcome:
        """Fit a tree on ``cases`` and persist the importance landscape.

        Args:
            property_id: Property whose history ``cases`` belongs to.
            scenario: Scenario StrEnum value the slice covers.
            cases: DecisionCases for the slice.  All four
                ``(property_id, scenario)`` filtering happens
                upstream — this method trusts the caller.
            target_action: Action whose feature surface we want to
                learn.  Cases with this action become *targets*;
                everything else becomes *counterexamples*.

        Returns:
            :class:`FoundationAnalysisOutcome` with the counts and
            persistence metadata.
        """
        target_features, other_features = _split_features(
            cases=cases, target_action=target_action,
        )
        if not target_features or not other_features:
            return FoundationAnalysisOutcome(
                property_id=property_id,
                scenario=scenario,
                target_count=len(target_features),
                other_count=len(other_features),
                rows_written=0,
                skipped_reason="empty_bucket",
            )

        result: MlSynthesisResult = self._synthesizer.synthesize(
            target_features=target_features,
            other_features=other_features,
        )
        if not result.feature_importance:
            return FoundationAnalysisOutcome(
                property_id=property_id,
                scenario=scenario,
                target_count=result.target_count,
                other_count=result.other_count,
                rows_written=0,
                skipped_reason="no_features_above_gate",
            )

        now = self._clock()
        sample_count = result.target_count + result.other_count
        rows = [
            ScenarioFoundation(
                property_id=property_id,
                scenario=scenario,
                feature_name=fi.feature_name,
                importance=fi.importance,
                sample_count=sample_count,
                computed_at=now,
            )
            for fi in result.feature_importance
        ]
        written = await self._store.upsert_many(rows)
        return FoundationAnalysisOutcome(
            property_id=property_id,
            scenario=scenario,
            target_count=result.target_count,
            other_count=result.other_count,
            rows_written=written,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_features(
    *,
    cases: Sequence[DecisionCase],
    target_action: DecisionType,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Partition cases by action and flatten each side.

    Reuses ``ConditionSynthesizer._flatten`` so the importance
    landscape is computed on exactly the surface the runtime
    synthesiser would mine — a feature missing in ``_flatten`` is
    invisible here too, which keeps both pipelines aligned.
    """
    target: list[dict[str, object]] = []
    other: list[dict[str, object]] = []
    for case in cases:
        flat = _flatten(case)
        if case.decision.action_type == target_action:
            target.append(flat)
        else:
            other.append(flat)
    return target, other


def _utcnow() -> datetime:
    """Default clock — UTC-aware, no implicit local TZ surprise."""
    return datetime.now(timezone.utc)


__all__ = [
    "DEFAULT_REFRESH_DAYS",
    "FoundationAnalysisOutcome",
    "FoundationAnalyzer",
    "configured_refresh_days",
    "foundation_analyzer_enabled",
]
