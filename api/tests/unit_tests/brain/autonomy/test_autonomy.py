"""Autonomy gate / engine / trust meter / collector behaviour.

Written at port time — the reference has no dedicated autonomy tests
(only incidental planner imports).  Pins the five-metric promotion
gate, the engine lifecycle and audit trail, the trust-meter projection
with registry-supplied workflow kinds, and the metrics collector's
resolver + aggregation semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.brain.autonomy import (
    AutonomyEngine,
    AutonomyState,
    InMemoryAutonomyStore,
    InMemoryWorkflowKindRegistry,
    MetricsCollector,
    PromotionGate,
    PromotionThresholds,
    TrustMeterService,
    WorkflowMetrics,
    make_event_resolver,
    state_rank,
)

GOOD_SEMI = WorkflowMetrics(
    sample_size=25,
    success_rate=0.85,
    override_rate=0.10,
    incidents=1,
    mean_latency_seconds=30.0,
)
GOOD_AUTO = WorkflowMetrics(
    sample_size=60,
    success_rate=0.95,
    override_rate=0.02,
    incidents=0,
    mean_latency_seconds=20.0,
)
BAD = WorkflowMetrics(sample_size=5, success_rate=0.4, override_rate=0.5, incidents=3)


class TestPromotionGate:
    def test_promotion_is_one_step_at_a_time(self):
        gate = PromotionGate()
        # metrics qualify for AUTOPILOT but OBSERVE only climbs one tier
        assert gate.evaluate(current=AutonomyState.OBSERVE, metrics=GOOD_AUTO) is AutonomyState.SEMI_AUTO
        assert gate.evaluate(current=AutonomyState.SEMI_AUTO, metrics=GOOD_AUTO) is AutonomyState.AUTOPILOT

    def test_demotion_on_any_breach(self):
        gate = PromotionGate()
        assert gate.evaluate(current=AutonomyState.AUTOPILOT, metrics=BAD) is AutonomyState.SEMI_AUTO
        assert gate.evaluate(current=AutonomyState.SEMI_AUTO, metrics=BAD) is AutonomyState.OBSERVE
        assert gate.evaluate(current=AutonomyState.OBSERVE, metrics=BAD) is AutonomyState.OBSERVE

    def test_all_criteria_required_for_promotion(self):
        gate = PromotionGate()
        nearly = WorkflowMetrics(
            sample_size=25,
            success_rate=0.85,
            override_rate=0.20,  # breaches max_override_rate=0.15
            incidents=0,
            mean_latency_seconds=30.0,
        )
        assert gate.evaluate(current=AutonomyState.OBSERVE, metrics=nearly) is AutonomyState.OBSERVE

    def test_custom_thresholds_and_lookup(self):
        custom = PromotionThresholds(
            min_sample_size=1,
            min_success_rate=0.5,
            max_override_rate=1.0,
            max_incidents=10,
            max_mean_latency_seconds=999.0,
        )
        gate = PromotionGate(to_semi_auto=custom)
        assert gate.thresholds_for(AutonomyState.SEMI_AUTO) is custom
        assert gate.thresholds_for(AutonomyState.OBSERVE) is None
        assert (
            gate.evaluate(
                current=AutonomyState.OBSERVE,
                metrics=WorkflowMetrics(sample_size=2, success_rate=0.6),
            )
            is AutonomyState.SEMI_AUTO
        )

    def test_state_rank_ordering(self):
        assert (
            state_rank(AutonomyState.OBSERVE)
            < state_rank(AutonomyState.SEMI_AUTO)
            < state_rank(AutonomyState.AUTOPILOT)
        )


class TestAutonomyEngine:
    def test_unknown_workflow_defaults_to_observe(self):
        engine = AutonomyEngine(store=InMemoryAutonomyStore())
        assert engine.state_for(property_id="p1", workflow="anything") is AutonomyState.OBSERVE

    def test_update_metrics_transitions_and_audit_trail(self):
        engine = AutonomyEngine(store=InMemoryAutonomyStore())
        updated = engine.update_metrics(
            property_id="p1",
            workflow="code_release",
            metrics=GOOD_SEMI,
            actor="nightly",
        )
        assert updated.state is AutonomyState.SEMI_AUTO
        assert updated.reason == "promoted"
        assert updated.changed_by == "nightly"
        # breach demotes one step and records it
        demoted = engine.update_metrics(
            property_id="p1",
            workflow="code_release",
            metrics=BAD,
        )
        assert demoted.state is AutonomyState.OBSERVE
        assert demoted.reason == "demoted"

    def test_force_state_bypasses_gate(self):
        engine = AutonomyEngine(store=InMemoryAutonomyStore())
        forced = engine.force_state(
            property_id="p1",
            workflow="code_release",
            state=AutonomyState.AUTOPILOT,
            actor="pm:7",
            reason="manual trial",
        )
        assert forced.state is AutonomyState.AUTOPILOT
        assert forced.changed_by == "pm:7"
        assert engine.state_for(property_id="p1", workflow="code_release") is AutonomyState.AUTOPILOT

    def test_record_initialises_once(self):
        engine = AutonomyEngine(store=InMemoryAutonomyStore())
        first = engine.record(property_id="p1", workflow="w1")
        again = engine.record(property_id="p1", workflow="w1")
        assert first == again
        assert [r.workflow for r in engine.list_for_property("p1")] == ["w1"]


class TestTrustMeter:
    def test_view_covers_registry_kinds_exactly_once(self):
        engine = AutonomyEngine(store=InMemoryAutonomyStore())
        engine.update_metrics(property_id="p1", workflow="code_release", metrics=GOOD_SEMI)
        service = TrustMeterService(
            engine=engine,
            workflows=("code_release", "late_checkout"),
        )
        view = service.for_property("p1")
        assert view.property_id == "p1"
        assert [b.workflow for b in view.bands] == ["code_release", "late_checkout"]
        by_wf = {b.workflow: b for b in view.bands}
        assert by_wf["code_release"].state is AutonomyState.SEMI_AUTO
        # unknown workflow collapses to the OBSERVE default band
        assert by_wf["late_checkout"].state is AutonomyState.OBSERVE
        assert by_wf["late_checkout"].progress.target_state is AutonomyState.SEMI_AUTO

    def test_kernel_default_is_no_workflows(self):
        engine = AutonomyEngine(store=InMemoryAutonomyStore())
        view = TrustMeterService(engine=engine).for_property("p1")
        assert view.bands == ()


@dataclass
class _Interaction:
    event_type: str = ""
    workflow: str = ""
    owner_intervened: bool = False
    owner_approved: bool | None = None
    guest_satisfied: str | None = None
    grader_score: float | None = None
    response_time_minutes: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)


class _Source:
    def __init__(self, interactions):
        self._interactions = list(interactions)

    def list_for_property(self, property_id: str, *, since: datetime):
        return self._interactions


REGISTRY = InMemoryWorkflowKindRegistry(
    {
        "code_release": ("send_access_code", "access_code_request"),
        "late_checkout": ("late_checkout_request",),
    }
)


class TestWorkflowKindRegistry:
    def test_resolution_is_case_insensitive_and_alias_aware(self):
        assert REGISTRY.resolve_event("SEND_ACCESS_CODE") == "code_release"
        assert REGISTRY.resolve_event("code_release") == "code_release"
        assert REGISTRY.resolve_event("unknown") is None
        assert REGISTRY.resolve_event("") is None
        assert REGISTRY.kinds() == ("code_release", "late_checkout")

    def test_event_resolver_honours_explicit_attribute(self):
        resolver = make_event_resolver(REGISTRY)
        assert resolver(_Interaction(workflow="code_release")) == "code_release"
        # explicit but unregistered kinds are skipped, not invented
        assert resolver(_Interaction(workflow="not_registered")) is None
        assert resolver(_Interaction(event_type="late_checkout_request")) == "late_checkout"
        assert resolver(_Interaction(event_type="mystery")) is None

    def test_labels_default_to_kind_and_ctor_is_back_compatible(self):
        # ctor with no labels (existing event_aliases-only callers) ->
        # every kind labels to itself, never null
        assert REGISTRY.labels() == {"code_release": "code_release", "late_checkout": "late_checkout"}
        # labels map merges, missing entries still fall back to the kind
        labelled = InMemoryWorkflowKindRegistry(
            {"code_release": ("send_access_code",), "late_checkout": ()},
            {"code_release": "Access Code Release"},
        )
        assert labelled.labels() == {
            "code_release": "Access Code Release",
            "late_checkout": "late_checkout",
        }


class TestMetricsCollector:
    def test_aggregate_buckets_by_resolved_workflow(self):
        interactions = [
            _Interaction(event_type="send_access_code", response_time_minutes=2.0),
            _Interaction(event_type="send_access_code", owner_intervened=True),
            _Interaction(event_type="late_checkout_request", guest_satisfied="negative"),
            _Interaction(event_type="unrelated_event"),  # resolver skips
            _Interaction(event_type="complaint", workflow="code_release"),  # incident
        ]
        engine = AutonomyEngine(store=InMemoryAutonomyStore())
        collector = MetricsCollector(
            source=_Source(interactions),
            autonomy_engine=engine,
            workflow_resolver=make_event_resolver(REGISTRY),
        )
        aggregates = collector.aggregate(property_id="p1")
        assert set(aggregates) == {"code_release", "late_checkout"}
        code = aggregates["code_release"]
        assert code.sample_size == 3
        assert code.overrides == 1
        assert code.incidents == 1
        assert code.override_rate == 1 / 3
        assert code.mean_latency_seconds == 120.0
        late = aggregates["late_checkout"]
        assert late.sample_size == 1
        assert late.successes == 0  # negative guest signal counts as failure

    def test_default_resolver_is_vocabulary_free(self):
        interactions = [
            _Interaction(event_type="send_access_code"),  # no explicit attr -> skipped
            _Interaction(workflow="anything_explicit"),
        ]
        engine = AutonomyEngine(store=InMemoryAutonomyStore())
        collector = MetricsCollector(source=_Source(interactions), autonomy_engine=engine)
        aggregates = collector.aggregate(property_id="p1")
        assert set(aggregates) == {"anything_explicit"}

    def test_flush_runs_gate_through_engine(self):
        interactions = [_Interaction(event_type="send_access_code", response_time_minutes=0.5) for _ in range(25)]
        engine = AutonomyEngine(store=InMemoryAutonomyStore())
        collector = MetricsCollector(
            source=_Source(interactions),
            autonomy_engine=engine,
            workflow_resolver=make_event_resolver(REGISTRY),
        )
        results = collector.flush(property_id="p1")
        assert results["code_release"].state is AutonomyState.SEMI_AUTO

    def test_pack_extended_incident_types(self):
        interactions = [_Interaction(event_type="noise_complaint", workflow="code_release")]
        engine = AutonomyEngine(store=InMemoryAutonomyStore())
        base = MetricsCollector(source=_Source(interactions), autonomy_engine=engine)
        assert base.aggregate(property_id="p1")["code_release"].incidents == 0
        extended = MetricsCollector(
            source=_Source(interactions),
            autonomy_engine=engine,
            incident_event_types=frozenset({"incident", "complaint", "noise_complaint"}),
        )
        assert extended.aggregate(property_id="p1")["code_release"].incidents == 1


def _now() -> datetime:
    return datetime.now(UTC)
