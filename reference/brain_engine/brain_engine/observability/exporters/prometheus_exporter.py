"""Prometheus-format metric exporter for Brain Engine.

Reference: ``brain_engine_advisory.md`` §5 — the metric names are
copied verbatim from the advisory so dashboards can be authored
once against either source.

The exporter owns a private ``CollectorRegistry`` instead of using
``prometheus_client.REGISTRY`` (the global default) — this keeps
test isolation simple (each test creates a fresh exporter) and
prevents accidental metric collisions between unrelated processes
running in the same Python interpreter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry, Counter, Histogram


CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


@dataclass(slots=True)
class PrometheusExporter:
    """Holds the registry and the metric handles.

    Construction is two-step: ``__init__`` only stores configuration,
    ``ensure_initialised`` creates the metric handles on first use.
    This lets the exporter be imported in environments that lack
    ``prometheus_client`` (CI lint, AST parse) without raising.
    """

    namespace: str = "brain"
    _registry: "CollectorRegistry | None" = None
    _llm_cost: "Counter | None" = None
    _llm_tokens_input: "Counter | None" = None
    _llm_tokens_output: "Counter | None" = None
    _llm_latency: "Histogram | None" = None
    _llm_errors: "Counter | None" = None
    _memory_retrieval_latency: "Histogram | None" = None
    _memory_retrieval_hits: "Counter | None" = None
    _memory_retrieval_misses: "Counter | None" = None
    _guardrail_violations: "Counter | None" = None
    _approval_pending: "Counter | None" = None
    _skill_evolved: "Counter | None" = None
    _patterns_cases_ingested: "Counter | None" = None
    _patterns_rules_emitted: "Counter | None" = None
    _patterns_synthesis_attempts: "Counter | None" = None
    _patterns_synthesis_rejects: "Counter | None" = None
    _refusal_signals: "Counter | None" = None
    _orchestrator_tier_hits: "Counter | None" = None
    _orchestrator_decision_modes: "Counter | None" = None
    _classifier_hint_used: "Counter | None" = None
    _classifier_hint_invalid: "Counter | None" = None
    _classifier_hint_disagreement: "Counter | None" = None
    _classifier_fallback_inform: "Counter | None" = None
    _pattern_rules_invalidated: "Counter | None" = None

    def ensure_initialised(self) -> None:
        """Create handles on first use."""
        if self._registry is not None:
            return
        from prometheus_client import (
            CollectorRegistry,
            Counter,
            Histogram,
        )

        registry = CollectorRegistry()
        ns = self.namespace

        self._llm_cost = Counter(
            f"{ns}_llm_cost_usd_total",
            "Cumulative LLM spend in USD.",
            labelnames=("provider", "model", "cognitive_level"),
            registry=registry,
        )
        self._llm_tokens_input = Counter(
            f"{ns}_llm_tokens_input_total",
            "Cumulative input tokens sent to LLM.",
            labelnames=("provider", "model"),
            registry=registry,
        )
        self._llm_tokens_output = Counter(
            f"{ns}_llm_tokens_output_total",
            "Cumulative output tokens received from LLM.",
            labelnames=("provider", "model"),
            registry=registry,
        )
        self._llm_latency = Histogram(
            f"{ns}_llm_latency_seconds",
            "LLM call wall-clock latency.",
            labelnames=("provider", "model"),
            registry=registry,
            buckets=(
                0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0,
            ),
        )
        self._memory_retrieval_latency = Histogram(
            f"{ns}_memory_retrieval_seconds",
            "Memory retrieval wall-clock latency per tier.",
            labelnames=("tier",),
            registry=registry,
            buckets=(
                0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 1.0,
            ),
        )
        self._guardrail_violations = Counter(
            f"{ns}_guardrail_violations_total",
            "Times a candidate output was blocked by guardrails.",
            labelnames=("rule", "layer"),
            registry=registry,
        )
        self._approval_pending = Counter(
            f"{ns}_approval_pending_total",
            "Approval requests queued, by urgency.",
            labelnames=("action_type", "urgency"),
            registry=registry,
        )
        self._skill_evolved = Counter(
            f"{ns}_skill_evolved_total",
            "Skill-evolution outcomes.",
            labelnames=("status",),
            registry=registry,
        )
        self._llm_errors = Counter(
            f"{ns}_llm_errors_total",
            "LLM call failures by provider/model.",
            labelnames=("provider", "model"),
            registry=registry,
        )
        self._memory_retrieval_hits = Counter(
            f"{ns}_memory_retrieval_hits_total",
            "Memory recall hits by tier.",
            labelnames=("tier",),
            registry=registry,
        )
        self._memory_retrieval_misses = Counter(
            f"{ns}_memory_retrieval_misses_total",
            "Memory recall misses by tier.",
            labelnames=("tier",),
            registry=registry,
        )
        self._patterns_cases_ingested = Counter(
            f"{ns}_patterns_cases_ingested_total",
            "DecisionCases inserted into the patterns store.",
            labelnames=("scenario", "source"),
            registry=registry,
        )
        self._patterns_rules_emitted = Counter(
            f"{ns}_patterns_rules_emitted_total",
            "PatternRules emitted by the miner.",
            labelnames=("scenario", "kind"),
            registry=registry,
        )
        self._patterns_synthesis_attempts = Counter(
            f"{ns}_patterns_synthesis_attempts_total",
            "Condition-synthesis attempts on minority buckets.",
            labelnames=("scenario",),
            registry=registry,
        )
        self._patterns_synthesis_rejects = Counter(
            f"{ns}_patterns_synthesis_rejects_total",
            "Synthesis attempts rejected for purity / support.",
            labelnames=("scenario",),
            registry=registry,
        )
        self._refusal_signals = Counter(
            f"{ns}_refusal_signals_total",
            "RefusalSignal occurrences detected by the extractor.",
            labelnames=("language", "type"),
            registry=registry,
        )
        self._orchestrator_tier_hits = Counter(
            f"{ns}_orchestrator_tier_hits_total",
            "§10 priority-chain tier that produced the decision.",
            labelnames=("tier",),
            registry=registry,
        )
        self._orchestrator_decision_modes = Counter(
            f"{ns}_orchestrator_decision_modes_total",
            "Final execution mode dispatched by the orchestrator.",
            labelnames=("mode",),
            registry=registry,
        )
        # Stage-2 LLM hint telemetry — feeds the dashboard panel
        # that proves the hint path actually fires (and how often
        # it disagrees with / replaces the keyword chain).
        self._classifier_hint_used = Counter(
            f"{ns}_decision_classifier_hint_used_total",
            "LLM scenario_hint won over the keyword chain.",
            labelnames=("scenario",),
            registry=registry,
        )
        self._classifier_hint_invalid = Counter(
            f"{ns}_decision_classifier_hint_invalid_total",
            "LLM scenario_hint was not a known Scenario value.",
            labelnames=("raw_value",),
            registry=registry,
        )
        self._classifier_hint_disagreement = Counter(
            f"{ns}_decision_classifier_hint_disagreement_total",
            (
                "LLM hint and keyword chain produced "
                "different non-GENERAL scenarios."
            ),
            labelnames=("hint_scenario", "keyword_scenario"),
            registry=registry,
        )
        # Sprint-1 bi-temporal soft-invalidate observability — fires
        # whenever ``_resolve_pattern_rule_contradictions`` closes a
        # candidate.  The counter surfaces the *regime-change rate*
        # per scope+scenario so operators can tell "PM shifted policy
        # on this property" from "miner is producing noise".
        self._pattern_rules_invalidated = Counter(
            f"{ns}_pattern_rules_invalidated_total",
            (
                "PatternRules soft-invalidated by "
                "_resolve_pattern_rule_contradictions."
            ),
            labelnames=("scenario", "scope"),
            registry=registry,
        )
        # Aybüke Q3 — INFORM-fallback rate per scenario.  Fires
        # whenever DecisionClassifier returns ``INFORM`` after both
        # the LLM hint AND the keyword chain failed to commit to a
        # more specific action.  Pair with
        # ``_classifier_hint_used_total`` to tell "LLM rescued the
        # case" apart from "raw fallback to INFORM" — a sustained
        # high rate per scenario is the early signal that a non-EN
        # message family is bypassing the keyword tables.
        self._classifier_fallback_inform = Counter(
            f"{ns}_decision_classifier_fallback_inform_total",
            (
                "DecisionClassifier returned INFORM after both LLM "
                "hint and keyword chain failed to commit."
            ),
            labelnames=("scenario",),
            registry=registry,
        )

        self._registry = registry

    # ── Recording API ───────────────────────────────────────────────

    def record_llm_call(
        self,
        *,
        provider: str,
        model: str,
        cognitive_level: str,
        cost_usd: float,
        tokens_input: int,
        tokens_output: int,
        latency_seconds: float,
    ) -> None:
        """Record one LLM round-trip across all relevant series."""
        self.ensure_initialised()
        assert self._llm_cost is not None
        assert self._llm_tokens_input is not None
        assert self._llm_tokens_output is not None
        assert self._llm_latency is not None
        self._llm_cost.labels(provider, model, cognitive_level).inc(
            cost_usd,
        )
        self._llm_tokens_input.labels(provider, model).inc(tokens_input)
        self._llm_tokens_output.labels(provider, model).inc(
            tokens_output,
        )
        self._llm_latency.labels(provider, model).observe(
            latency_seconds,
        )

    def record_memory_retrieval(
        self,
        *,
        tier: str,
        latency_seconds: float,
    ) -> None:
        self.ensure_initialised()
        assert self._memory_retrieval_latency is not None
        self._memory_retrieval_latency.labels(tier).observe(
            latency_seconds,
        )

    def record_guardrail_violation(
        self, *, rule: str, layer: str,
    ) -> None:
        self.ensure_initialised()
        assert self._guardrail_violations is not None
        self._guardrail_violations.labels(rule, layer).inc()

    def record_approval_queued(
        self, *, action_type: str, urgency: str,
    ) -> None:
        self.ensure_initialised()
        assert self._approval_pending is not None
        self._approval_pending.labels(action_type, urgency).inc()

    def record_skill_evolution(self, *, status: str) -> None:
        self.ensure_initialised()
        assert self._skill_evolved is not None
        self._skill_evolved.labels(status).inc()

    def record_llm_error(
        self, *, provider: str, model: str,
    ) -> None:
        """Increment the LLM error counter for one failure."""
        self.ensure_initialised()
        assert self._llm_errors is not None
        self._llm_errors.labels(provider, model).inc()

    def record_memory_hit(self, *, tier: str) -> None:
        """Count one memory recall hit for ``tier``."""
        self.ensure_initialised()
        assert self._memory_retrieval_hits is not None
        self._memory_retrieval_hits.labels(tier).inc()

    def record_memory_miss(self, *, tier: str) -> None:
        """Count one memory recall miss for ``tier``."""
        self.ensure_initialised()
        assert self._memory_retrieval_misses is not None
        self._memory_retrieval_misses.labels(tier).inc()

    def record_pattern_case_ingested(
        self, *, scenario: str, source: str,
    ) -> None:
        """Count one DecisionCase inserted into the store."""
        self.ensure_initialised()
        assert self._patterns_cases_ingested is not None
        self._patterns_cases_ingested.labels(scenario, source).inc()

    def record_pattern_rule_emitted(
        self, *, scenario: str, conditional: bool,
    ) -> None:
        """Count one PatternRule emitted by the miner.

        ``conditional=True`` means the rule carries a non-empty
        conditions dict (synthesised by ConditionSynthesizer);
        ``False`` is the unconditional baseline path.
        """
        self.ensure_initialised()
        assert self._patterns_rules_emitted is not None
        kind = "conditional" if conditional else "baseline"
        self._patterns_rules_emitted.labels(scenario, kind).inc()

    def record_pattern_synthesis_attempt(
        self, *, scenario: str, accepted: bool,
    ) -> None:
        """Count one synthesis attempt; route to attempts/rejects."""
        self.ensure_initialised()
        assert self._patterns_synthesis_attempts is not None
        assert self._patterns_synthesis_rejects is not None
        self._patterns_synthesis_attempts.labels(scenario).inc()
        if not accepted:
            self._patterns_synthesis_rejects.labels(scenario).inc()

    def record_refusal_signal(
        self, *, language: str, refusal_type: str,
    ) -> None:
        """Count one RefusalSignal occurrence."""
        self.ensure_initialised()
        assert self._refusal_signals is not None
        self._refusal_signals.labels(language, refusal_type).inc()

    def record_orchestrator_decision(
        self, *, tier: str, mode: str,
    ) -> None:
        """Record one priority-chain decision (tier + final mode)."""
        self.ensure_initialised()
        assert self._orchestrator_tier_hits is not None
        assert self._orchestrator_decision_modes is not None
        self._orchestrator_tier_hits.labels(tier).inc()
        self._orchestrator_decision_modes.labels(mode).inc()

    def record_classifier_hint_used(self, *, scenario: str) -> None:
        """Count one LLM scenario_hint that won over keywords."""
        self.ensure_initialised()
        assert self._classifier_hint_used is not None
        self._classifier_hint_used.labels(scenario).inc()

    def record_classifier_hint_invalid(self, *, raw_value: str) -> None:
        """Count one LLM scenario_hint that wasn't a known Scenario."""
        self.ensure_initialised()
        assert self._classifier_hint_invalid is not None
        # Cap label cardinality — unknown hints should be rare and
        # short.  Truncate to keep the registry from blowing up if a
        # broken prompt emits free-form text.
        self._classifier_hint_invalid.labels(raw_value[:32]).inc()

    def record_classifier_hint_disagreement(
        self, *, hint_scenario: str, keyword_scenario: str,
    ) -> None:
        """Count one (hint, keyword) pair that disagreed."""
        self.ensure_initialised()
        assert self._classifier_hint_disagreement is not None
        self._classifier_hint_disagreement.labels(
            hint_scenario, keyword_scenario,
        ).inc()

    def record_pattern_rule_invalidated(
        self, *, scenario: str, scope: str,
    ) -> None:
        """Count one PatternRule closed by the contradiction resolver."""
        self.ensure_initialised()
        assert self._pattern_rules_invalidated is not None
        self._pattern_rules_invalidated.labels(scenario, scope).inc()

    def record_classifier_fallback_inform(self, *, scenario: str) -> None:
        """Count one INFORM-fallback decision (Aybüke Q3 telemetry)."""
        self.ensure_initialised()
        assert self._classifier_fallback_inform is not None
        self._classifier_fallback_inform.labels(scenario).inc()

    # ── Export API ──────────────────────────────────────────────────

    def render(self) -> bytes:
        """Render the registry in Prometheus text format."""
        self.ensure_initialised()
        from prometheus_client import generate_latest

        assert self._registry is not None
        return generate_latest(self._registry)


_DEFAULT: PrometheusExporter | None = None


def build_default_exporter() -> PrometheusExporter:
    """Return a process-wide singleton exporter.

    Singleton because the FastAPI app and the cognitive pipeline
    must agree on which registry the ``/metrics`` endpoint reads
    from.  Tests that need isolation should construct their own
    ``PrometheusExporter()`` directly.
    """
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = PrometheusExporter()
        _DEFAULT.ensure_initialised()
    return _DEFAULT
