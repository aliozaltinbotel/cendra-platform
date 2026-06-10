"""Pull-mode aggregator that feeds :class:`AutonomyEngine`.

Wires two existing primitives together without taking a hard dependency
on either one's concrete shape:

- the interaction recorder (Batch 6)
  is the system of record for every ``BrainEngineInteraction`` (failure
  flags, override flags, response latency, cascade level, …).  Here it
  is consumed through the :class:`InteractionSource` Protocol so tests
  and future stores plug in with a single async method.
- :class:`core.brain.autonomy.AutonomyEngine` consumes
  :class:`WorkflowMetrics` and runs the promotion gate.

The collector is intentionally a *pull* aggregator (called by a
scheduler or HTTP endpoint) rather than a push hook on every interaction
so the gate sees a stable window — not a single noisy event — and the
recorder remains a write-only sink.

The same window aggregation also produces a :class:`KpiSnapshot` for the
eight CEO V2 operational KPIs (autonomous-completion %, PM touches per
reservation, accuracy, override rate, mean response time, captured
extra-fee count, properties observed).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol, runtime_checkable

from core.brain.autonomy.engine import AutonomyEngine
from core.brain.autonomy.models import (
    WorkflowAutonomy,
    WorkflowMetrics,
)
from core.brain.autonomy.workflow_kinds import (
    EXPLICIT_ATTRIBUTE_RESOLVER,
    WorkflowResolver,
)
from core.brain.patterns.wilson import wilson_lower_bound

__all__ = [
    "InteractionSource",
    "KpiSnapshot",
    "MetricsCollector",
    "WindowAggregate",
]


logger = logging.getLogger(__name__)


_DEFAULT_WINDOW_DAYS: Final[int] = 14
_MAX_WINDOW_DAYS: Final[int] = 365
_FAILURE_GRADE_THRESHOLD: Final[float] = 0.4

# Vertical-neutral defaults; vertical packs extend per deployment via the
# collector's incident_event_types param (the reference also pinned the
# hospitality "noise_complaint" / "damage" types — now pack data, see
# packs/hospitality/workflow_kinds.yaml).
_INCIDENT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "incident",
        "complaint",
        "compensation",
    }
)
_EXTRA_FEE_EVENT_TYPES: Final[frozenset[str]] = frozenset({"extra_fee", "extra_person_fee", "upsell_accepted"})


# ---------------------------------------------------------------------------
# Protocol — read side over the recorder (or any compatible store)
# ---------------------------------------------------------------------------


@runtime_checkable
class InteractionSource(Protocol):
    """Adapter that returns interactions for a property within a window.

    A single async method is enough; the Brain Engine ships exactly one
    production implementation today (an adapter over the Redis-backed
    :class:`InteractionRecorder`), but the Protocol exists so tests
    and future stores can substitute freely.
    """

    def list_for_property(
        self,
        property_id: str,
        *,
        since: datetime,
    ) -> Sequence[Any]:
        """Return every interaction touching ``property_id`` since ``since``."""
        ...


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowAggregate:
    """Per-workflow counters from one aggregation pass.

    A pure value object; conversion to :class:`WorkflowMetrics` happens
    in :meth:`MetricsCollector._to_workflow_metrics` so the aggregate
    stays decoupled from the gate's threshold contract.

    Attributes:
        sample_size: Total interactions assigned to the workflow.
        successes: Interactions that did not fail (no override, no
            negative guest signal, no low grader score).
        overrides: Interactions where the PM intervened or rejected.
        incidents: Post-action complaints / damage / noise events.
        latency_seconds_total: Sum of response-time samples (seconds).
        latency_samples: Number of interactions that contributed a
            non-zero response-time sample.
    """

    sample_size: int = 0
    successes: int = 0
    overrides: int = 0
    incidents: int = 0
    latency_seconds_total: float = 0.0
    latency_samples: int = 0

    @property
    def override_rate(self) -> float:
        """PM-override frequency in [0, 1]; ``0.0`` for empty window."""
        if self.sample_size == 0:
            return 0.0
        return self.overrides / self.sample_size

    @property
    def mean_latency_seconds(self) -> float:
        """Mean response-time in seconds; ``0.0`` for no samples."""
        if self.latency_samples == 0:
            return 0.0
        return self.latency_seconds_total / self.latency_samples


@dataclass(frozen=True, slots=True)
class KpiSnapshot:
    """The eight operational KPIs from the CEO V2 directive (2026-04-20).

    All counts and rates are computed over the same window passed to
    :meth:`MetricsCollector.compute_kpis` so dashboard rows line up.

    Attributes:
        window_days: Aggregation window in days.
        total_interactions: Every recorded interaction in the window.
        autonomous_completion_rate: Interactions resolved without
            escalation and without a PM override, as a fraction of
            ``total_interactions``.
        pm_touches_per_reservation: PM-driven actions divided by the
            count of distinct reservations referenced in the window.
        accuracy_rate: ``1.0 - failures / total``.
        override_rate: Override events divided by total interactions.
        mean_response_time_minutes: Mean of the recorder's
            ``response_time_minutes`` field for samples that supplied a
            positive value.
        captured_extra_fee_count: Interactions tagged ``extra_fee``,
            ``extra_person_fee`` or ``upsell_accepted``.
        properties_observed: Distinct property ids requested by the
            caller (input cardinality, not output filter).
    """

    window_days: int
    total_interactions: int
    autonomous_completion_rate: float
    pm_touches_per_reservation: float
    accuracy_rate: float
    override_rate: float
    mean_response_time_minutes: float
    captured_extra_fee_count: int
    properties_observed: int


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Pull-mode aggregator that feeds :class:`AutonomyEngine`.

    Args:
        source: Read-side adapter over interactions.
        autonomy_engine: Target gate to push metrics into.
        workflow_resolver: Maps an interaction to a
            workflow-kind string or ``None`` when it does not belong to
            a tracked workflow.  Defaults to the recorder's
            ``event_type`` mapping (see
            :data:`~core.brain.autonomy.workflow_kinds.EXPLICIT_ATTRIBUTE_RESOLVER`;
            vertical packs supply an event-map resolver via
            :func:`make_event_resolver`).
        actor: Recorded as ``WorkflowAutonomy.changed_by`` for every
            metric flush; defaults to ``"metrics_collector"``.
    """

    def __init__(
        self,
        *,
        source: InteractionSource,
        autonomy_engine: AutonomyEngine,
        workflow_resolver: WorkflowResolver = EXPLICIT_ATTRIBUTE_RESOLVER,
        incident_event_types: frozenset[str] = _INCIDENT_EVENT_TYPES,
        actor: str = "metrics_collector",
    ) -> None:
        self._source = source
        self._engine = autonomy_engine
        self._resolve = workflow_resolver
        self._incident_event_types = incident_event_types
        self._actor = actor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def aggregate(
        self,
        *,
        property_id: str,
        window_days: int = _DEFAULT_WINDOW_DAYS,
    ) -> dict[str, WindowAggregate]:
        """Return per-workflow counters for ``property_id``.

        Skips interactions whose resolver returns ``None``.  Empty
        buckets are omitted from the result so callers can iterate the
        mapping safely.
        """
        since = self._since(window_days)
        interactions = self._source.list_for_property(
            property_id,
            since=since,
        )
        buckets: dict[str, _MutableAggregate] = defaultdict(
            _MutableAggregate,
        )
        for ix in interactions:
            workflow = self._resolve(ix)
            if workflow is None:
                continue
            buckets[workflow].apply(ix, incident_event_types=self._incident_event_types)
        return {wf: agg.freeze() for wf, agg in buckets.items()}

    def flush(
        self,
        *,
        property_id: str,
        window_days: int = _DEFAULT_WINDOW_DAYS,
    ) -> dict[str, WorkflowAutonomy]:
        """Aggregate and push metrics into :class:`AutonomyEngine`.

        Only buckets with ``sample_size > 0`` produce a flush — the
        gate treats an empty bucket as "no evidence", so writing zeros
        would risk demoting a workflow that simply had a quiet window.
        """
        aggregates = self.aggregate(
            property_id=property_id,
            window_days=window_days,
        )
        results: dict[str, WorkflowAutonomy] = {}
        for workflow, agg in aggregates.items():
            if agg.sample_size == 0:
                continue
            metrics = self._to_workflow_metrics(agg)
            updated = self._engine.update_metrics(
                property_id=property_id,
                workflow=workflow,
                metrics=metrics,
                actor=self._actor,
            )
            results[workflow] = updated
            logger.debug(
                "metrics.flushed property_id=%s workflow=%s sample=%s success=%s override=%s",
                property_id,
                workflow,
                metrics.sample_size,
                round(metrics.success_rate, 3),
                round(metrics.override_rate, 3),
            )
        return results

    def compute_kpis(
        self,
        *,
        property_ids: Sequence[str],
        window_days: int = _DEFAULT_WINDOW_DAYS,
    ) -> KpiSnapshot:
        """Compute the eight CEO V2 KPIs across ``property_ids``.

        ``property_ids`` defines both the input fan-out (one fetch per
        property) and the cardinality reported as
        :pyattr:`KpiSnapshot.properties_observed`.
        """
        since = self._since(window_days)
        interactions: list[Any] = []
        for pid in property_ids:
            chunk = self._source.list_for_property(pid, since=since)
            interactions.extend(chunk)
        return _kpis_from_interactions(
            interactions=interactions,
            property_ids=property_ids,
            window_days=window_days,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _since(window_days: int) -> datetime:
        clamped = max(1, min(window_days, _MAX_WINDOW_DAYS))
        return datetime.now(UTC) - timedelta(days=clamped)

    @staticmethod
    def _to_workflow_metrics(agg: WindowAggregate) -> WorkflowMetrics:
        success_rate = wilson_lower_bound(
            successes=agg.successes,
            trials=agg.sample_size,
        )
        return WorkflowMetrics(
            sample_size=agg.sample_size,
            success_rate=success_rate,
            override_rate=agg.override_rate,
            incidents=agg.incidents,
            mean_latency_seconds=agg.mean_latency_seconds,
        )


# ---------------------------------------------------------------------------
# Internal mutable aggregator — kept private so the public surface is
# all immutable value objects.
# ---------------------------------------------------------------------------


class _MutableAggregate:
    """In-place accumulator that materialises to :class:`WindowAggregate`."""

    __slots__ = (
        "_incidents",
        "_latency_samples",
        "_latency_total",
        "_overrides",
        "_sample_size",
        "_successes",
    )

    def __init__(self) -> None:
        self._sample_size = 0
        self._successes = 0
        self._overrides = 0
        self._incidents = 0
        self._latency_total = 0.0
        self._latency_samples = 0

    def apply(self, ix: Any, *, incident_event_types: frozenset[str] = _INCIDENT_EVENT_TYPES) -> None:
        self._sample_size += 1
        if not _is_failure(ix):
            self._successes += 1
        if _is_override(ix):
            self._overrides += 1
        if _is_incident(ix, incident_event_types):
            self._incidents += 1
        latency = _response_time_seconds(ix)
        if latency is not None:
            self._latency_total += latency
            self._latency_samples += 1

    def freeze(self) -> WindowAggregate:
        return WindowAggregate(
            sample_size=self._sample_size,
            successes=self._successes,
            overrides=self._overrides,
            incidents=self._incidents,
            latency_seconds_total=self._latency_total,
            latency_samples=self._latency_samples,
        )


# ---------------------------------------------------------------------------
# Predicates over the recorder's BrainEngineInteraction shape
# ---------------------------------------------------------------------------


def _is_failure(ix: Any) -> bool:
    """Mirror :pyattr:`BrainEngineInteraction.is_failure` defensively.

    Re-implemented here (instead of importing the property) so that the
    collector remains usable with any object that exposes the same
    duck-typed attribute surface.
    """
    if getattr(ix, "owner_intervened", False):
        return True
    if getattr(ix, "guest_satisfied", None) == "negative":
        return True
    score = getattr(ix, "grader_score", None)
    if score is not None and score < _FAILURE_GRADE_THRESHOLD:
        return True
    return False


def _is_override(ix: Any) -> bool:
    """An override is a PM intervention or an explicit rejection."""
    if getattr(ix, "owner_intervened", False):
        return True
    if getattr(ix, "owner_approved", None) is False:
        return True
    return False


def _is_incident(ix: Any, incident_event_types: frozenset[str] = _INCIDENT_EVENT_TYPES) -> bool:
    return str(getattr(ix, "event_type", "")).lower() in incident_event_types


def _response_time_seconds(ix: Any) -> float | None:
    """Return latency in seconds, or ``None`` for non-positive samples."""
    minutes = getattr(ix, "response_time_minutes", 0.0)
    if minutes is None or minutes <= 0.0:
        return None
    return float(minutes) * 60.0


def _reservation_id(ix: Any) -> str | None:
    """Pull a reservation id from the interaction's context dict."""
    context = getattr(ix, "context", None)
    if not isinstance(context, dict):
        return None
    raw = context.get("reservation_id")
    return str(raw) if raw else None


# ---------------------------------------------------------------------------
# KPI roll-up
# ---------------------------------------------------------------------------


def _kpis_from_interactions(
    *,
    interactions: Iterable[Any],
    property_ids: Sequence[str],
    window_days: int,
) -> KpiSnapshot:
    """Pure aggregation over a flat interaction iterable.

    Kept as a module-level function (not a method) so it is trivially
    unit-testable without instantiating the whole collector.
    """
    total = 0
    autonomous = 0
    overrides = 0
    failures = 0
    latency_total_min = 0.0
    latency_samples = 0
    extra_fee = 0
    pm_touches = 0
    reservations: set[str] = set()

    for ix in interactions:
        total += 1
        is_override = _is_override(ix)
        if _is_failure(ix):
            failures += 1
        if is_override:
            overrides += 1
            pm_touches += 1
        if getattr(ix, "resolved_without_escalation", False) and not is_override:
            autonomous += 1
        minutes = getattr(ix, "response_time_minutes", 0.0) or 0.0
        if minutes > 0.0:
            latency_total_min += float(minutes)
            latency_samples += 1
        event_type = str(getattr(ix, "event_type", "")).lower()
        if event_type in _EXTRA_FEE_EVENT_TYPES:
            extra_fee += 1
        rid = _reservation_id(ix)
        if rid is not None:
            reservations.add(rid)

    autonomous_rate = (autonomous / total) if total else 0.0
    override_rate = (overrides / total) if total else 0.0
    accuracy = 1.0 - (failures / total) if total else 0.0
    mean_response = latency_total_min / latency_samples if latency_samples else 0.0
    pm_per_reservation = pm_touches / len(reservations) if reservations else 0.0
    return KpiSnapshot(
        window_days=window_days,
        total_interactions=total,
        autonomous_completion_rate=autonomous_rate,
        pm_touches_per_reservation=pm_per_reservation,
        accuracy_rate=accuracy,
        override_rate=override_rate,
        mean_response_time_minutes=mean_response,
        captured_extra_fee_count=extra_fee,
        properties_observed=len(set(property_ids)),
    )
