"""Metacognition Module — Self-monitoring and self-correction for the Brain Engine.

Implements metacognitive capabilities inspired by cognitive neuroscience:
- Self-monitoring: Tracks reasoning quality, detects uncertainty
- Self-correction: Adjusts strategy when confidence drops or errors accumulate
- Epistemic awareness: Knows what it knows and what it doesn't
- Performance tracking: Monitors outcomes of past decisions
- Reasoning trace: Maintains an audit log for introspection

References:
- Metacognition in AI (Neuro-Symbolic AI survey, 2025)
- CoALA cognitive architecture (Princeton/Berkeley, 2024)
- Dual Process Theory — System 2 as metacognitive oversight of System 1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Confidence thresholds
CONFIDENCE_HIGH = 0.8
CONFIDENCE_MEDIUM = 0.5
CONFIDENCE_LOW = 0.3
CONFIDENCE_ESCALATE = 0.2

# Maximum consecutive low-confidence decisions before forcing System 2
MAX_LOW_CONFIDENCE_STREAK = 3


@dataclass
class ReasoningTrace:
    """A single step in the agent's reasoning process."""

    step_id: str
    event: str
    reasoning_mode: str  # system1 or system2
    confidence_before: float
    confidence_after: float
    decision: str
    outcome: str | None = None  # success, failure, pending
    correction_applied: bool = False
    correction_reason: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class EpistemicState:
    """What the agent knows about its own knowledge."""

    known_facts: list[str] = field(default_factory=list)
    known_unknowns: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


@dataclass
class PerformanceMetrics:
    """Tracks decision quality over time."""

    total_decisions: int = 0
    successful_decisions: int = 0
    failed_decisions: int = 0
    corrections_made: int = 0
    escalations: int = 0
    avg_confidence: float = 0.8
    low_confidence_streak: int = 0

    @property
    def success_rate(self) -> float:
        if self.total_decisions == 0:
            return 0.5
        return self.successful_decisions / self.total_decisions

    @property
    def correction_rate(self) -> float:
        if self.total_decisions == 0:
            return 0.0
        return self.corrections_made / self.total_decisions


class MetacognitiveMonitor:
    """Self-monitoring and self-correction engine.

    Provides metacognitive oversight for the cognitive controller:
    - Monitors reasoning confidence and detects degradation
    - Triggers System 1 → System 2 escalation when needed
    - Tracks epistemic state (what is known/unknown/assumed)
    - Records reasoning traces for auditing and learning
    - Suggests corrections when patterns of failure are detected

    Args:
        max_trace_size: Maximum number of reasoning traces to retain.
    """

    def __init__(self, max_trace_size: int = 200) -> None:
        self._traces: list[ReasoningTrace] = []
        self._max_trace_size = max_trace_size
        self._metrics = PerformanceMetrics()
        self._epistemic = EpistemicState()
        self._active_corrections: list[dict[str, Any]] = []

    @property
    def metrics(self) -> PerformanceMetrics:
        return self._metrics

    @property
    def epistemic_state(self) -> EpistemicState:
        return self._epistemic

    @property
    def traces(self) -> list[ReasoningTrace]:
        return list(self._traces)

    # ── Self-Monitoring ──────────────────────────────────────────────── #

    def assess_confidence(
        self,
        event: str,
        reasoning_mode: str,
        context_quality: dict[str, Any],
    ) -> dict[str, Any]:
        """Assess confidence level based on available context and history.

        Checks:
        - Is there enough context to make a good decision?
        - Has the agent seen similar events before?
        - Are there conflicting signals in the context?

        Returns assessment with confidence score and recommendations.
        """
        confidence = 0.8  # Base confidence
        flags: list[str] = []
        recommendations: list[str] = []

        # Check context completeness
        recent_events = context_quality.get("recent_events_count", 0)
        relevant_knowledge = context_quality.get("relevant_knowledge_count", 0)
        entity_context = context_quality.get("entity_context_available", False)
        procedures_found = context_quality.get("procedures_count", 0)

        if recent_events == 0:
            confidence -= 0.15
            flags.append("no_recent_context")
            recommendations.append("Gather more context before deciding")

        if relevant_knowledge == 0:
            confidence -= 0.1
            flags.append("no_relevant_knowledge")

        if not entity_context:
            confidence -= 0.1
            flags.append("missing_entity_context")
            self._epistemic.known_unknowns.append(
                f"No entity context for event: {event}"
            )

        if procedures_found == 0 and reasoning_mode == "system1":
            confidence -= 0.2
            flags.append("no_matching_procedures_for_system1")
            recommendations.append("Switch to System 2: no procedure matches")

        # Check historical performance on similar events
        similar_traces = [
            t for t in self._traces[-50:]
            if t.event == event and t.outcome is not None
        ]
        if similar_traces:
            recent_failures = sum(
                1 for t in similar_traces if t.outcome == "failure"
            )
            if recent_failures > len(similar_traces) * 0.5:
                confidence -= 0.2
                flags.append("high_failure_rate_on_similar_events")
                recommendations.append("Past failures on similar events — use caution")

        # Check low-confidence streak
        if self._metrics.low_confidence_streak >= MAX_LOW_CONFIDENCE_STREAK:
            confidence -= 0.15
            flags.append("low_confidence_streak")
            recommendations.append("Multiple uncertain decisions — consider escalating to human")

        confidence = max(0.0, min(1.0, confidence))

        return {
            "confidence": confidence,
            "flags": flags,
            "recommendations": recommendations,
            "should_escalate": confidence < CONFIDENCE_ESCALATE,
            "should_switch_to_system2": confidence < CONFIDENCE_MEDIUM and reasoning_mode == "system1",
        }

    def detect_conflicts(self, context: dict[str, Any]) -> list[str]:
        """Detect conflicting information in the retrieved context.

        Looks for contradictions between facts, beliefs, and recent events
        that could lead to incorrect reasoning.
        """
        conflicts: list[str] = []

        # Check for conflicting entity facts/beliefs
        for key, value in context.items():
            if not key.startswith("entity_"):
                continue
            facts = value.get("facts", [])
            beliefs = value.get("beliefs", [])

            # Flag low-confidence facts
            for fact in facts:
                if fact.get("confidence", 1.0) < CONFIDENCE_MEDIUM:
                    conflicts.append(
                        f"Low-confidence fact for {key}: {fact.get('content', '')[:80]}"
                    )

            # Flag beliefs that contradict high-confidence facts
            fact_contents = {f.get("content", "").lower() for f in facts}
            for belief in beliefs:
                belief_text = belief.get("content", "").lower()
                if any(
                    ("not " in belief_text and fc in belief_text.replace("not ", ""))
                    or ("not " in fc and belief_text in fc.replace("not ", ""))
                    for fc in fact_contents
                ):
                    conflicts.append(
                        f"Belief may contradict fact: {belief.get('content', '')[:80]}"
                    )

        if conflicts:
            logger.info("Metacognition: %d conflicts detected", len(conflicts))

        return conflicts

    # ── Self-Correction ──────────────────────────────────────────────── #

    def evaluate_and_correct(
        self,
        decision: dict[str, Any],
        perception: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Review a decision and apply corrections if needed.

        Metacognitive oversight of the reasoning output:
        - Checks if confidence is too low for autonomous action
        - Validates that recommended actions align with context
        - Suggests System 1→2 escalation when appropriate
        - Flags decisions that need human review

        Returns the (potentially modified) decision with correction metadata.
        """
        corrections: list[str] = []
        original_confidence = decision.get("confidence", 0.5)
        original_mode = decision.get("reasoning_mode", "system1")
        actions = decision.get("recommended_actions", [])

        # Correction 1: Empty actions with high surprise → force deliberation
        surprise_score = perception.get("surprise", {}).get("score", 0)
        if not actions and surprise_score >= 0.5:
            corrections.append("No actions for surprising event — adding investigation step")
            actions.append("investigate_before_acting")
            decision["recommended_actions"] = actions

        # Correction 2: System 1 with low confidence → upgrade to System 2
        if original_mode == "system1" and original_confidence < CONFIDENCE_MEDIUM:
            corrections.append(
                f"System 1 confidence too low ({original_confidence:.2f}) — upgrading to System 2"
            )
            decision["reasoning_mode"] = "system2"

        # Correction 3: Very low confidence → add escalation
        if original_confidence < CONFIDENCE_ESCALATE:
            if "escalate_to_human" not in actions:
                corrections.append("Confidence below threshold — adding human escalation")
                actions.insert(0, "escalate_to_human")
                decision["recommended_actions"] = actions
            self._metrics.escalations += 1

        # Correction 4: Conflicting context detected → flag for review
        conflicts = self.detect_conflicts(context)
        if conflicts:
            corrections.append(f"Conflicting context detected: {len(conflicts)} conflicts")
            decision.setdefault("metacognition", {})["conflicts"] = conflicts
            # Reduce confidence when conflicts exist
            decision["confidence"] = max(
                CONFIDENCE_LOW, original_confidence - 0.1 * len(conflicts)
            )

        # Correction 5: Repeated failures on same event type → try different approach
        event = perception.get("event", "")
        recent_failures = [
            t for t in self._traces[-20:]
            if t.event == event and t.outcome == "failure"
        ]
        if len(recent_failures) >= 2:
            corrections.append(
                f"Repeated failures on '{event}' — suggesting alternative approach"
            )
            decision.setdefault("metacognition", {})["repeated_failure"] = True
            if "request_alternative_approach" not in actions:
                actions.append("request_alternative_approach")
                decision["recommended_actions"] = actions

        if corrections:
            self._metrics.corrections_made += 1
            decision.setdefault("metacognition", {})["corrections"] = corrections
            logger.info(
                "Metacognition corrections applied: %s",
                "; ".join(corrections),
            )

        return decision

    # ── Reasoning Trace ──────────────────────────────────────────────── #

    def record_trace(
        self,
        event: str,
        reasoning_mode: str,
        confidence_before: float,
        confidence_after: float,
        decision_summary: str,
        correction_applied: bool = False,
        correction_reason: str = "",
    ) -> ReasoningTrace:
        """Record a reasoning step for audit and learning."""
        trace = ReasoningTrace(
            step_id=f"trace-{len(self._traces):04d}",
            event=event,
            reasoning_mode=reasoning_mode,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            decision=decision_summary,
            correction_applied=correction_applied,
            correction_reason=correction_reason,
        )
        self._traces.append(trace)

        # Trim old traces
        if len(self._traces) > self._max_trace_size:
            self._traces = self._traces[-self._max_trace_size:]

        # Update metrics
        self._metrics.total_decisions += 1

        # Running average confidence
        n = self._metrics.total_decisions
        self._metrics.avg_confidence = (
            (self._metrics.avg_confidence * (n - 1) + confidence_after) / n
        )

        # Track low-confidence streak
        if confidence_after < CONFIDENCE_MEDIUM:
            self._metrics.low_confidence_streak += 1
        else:
            self._metrics.low_confidence_streak = 0

        return trace

    def record_outcome(self, step_id: str, outcome: str) -> None:
        """Record the outcome of a previous decision (success/failure)."""
        for trace in reversed(self._traces):
            if trace.step_id == step_id:
                trace.outcome = outcome
                if outcome == "success":
                    self._metrics.successful_decisions += 1
                elif outcome == "failure":
                    self._metrics.failed_decisions += 1
                return
        logger.warning("Trace %s not found for outcome recording", step_id)

    # ── Epistemic Awareness ──────────────────────────────────────────── #

    def register_known_fact(self, fact: str) -> None:
        """Register something the agent knows for certain."""
        if fact not in self._epistemic.known_facts:
            self._epistemic.known_facts.append(fact)
            # If it was previously unknown, remove from unknowns
            self._epistemic.known_unknowns = [
                u for u in self._epistemic.known_unknowns if u != fact
            ]

    def register_unknown(self, question: str) -> None:
        """Register something the agent knows it doesn't know."""
        if question not in self._epistemic.known_unknowns:
            self._epistemic.known_unknowns.append(question)

    def register_assumption(self, assumption: str) -> None:
        """Register an assumption the agent is making."""
        if assumption not in self._epistemic.assumptions:
            self._epistemic.assumptions.append(assumption)

    def clear_assumption(self, assumption: str) -> None:
        """Remove an assumption that has been resolved."""
        self._epistemic.assumptions = [
            a for a in self._epistemic.assumptions if a != assumption
        ]

    # ── Introspection Report ─────────────────────────────────────────── #

    def introspection_report(self) -> dict[str, Any]:
        """Generate a self-assessment report.

        Useful for debugging, auditing, and displaying the agent's
        metacognitive state to operators.
        """
        recent_traces = self._traces[-10:]
        recent_corrections = [t for t in recent_traces if t.correction_applied]
        recent_outcomes = [t for t in recent_traces if t.outcome is not None]

        return {
            "performance": {
                "total_decisions": self._metrics.total_decisions,
                "success_rate": round(self._metrics.success_rate, 3),
                "correction_rate": round(self._metrics.correction_rate, 3),
                "avg_confidence": round(self._metrics.avg_confidence, 3),
                "escalations": self._metrics.escalations,
                "low_confidence_streak": self._metrics.low_confidence_streak,
            },
            "epistemic_state": {
                "known_facts": len(self._epistemic.known_facts),
                "known_unknowns": self._epistemic.known_unknowns[-5:],
                "active_assumptions": self._epistemic.assumptions[-5:],
            },
            "recent_activity": {
                "last_10_decisions": [
                    {
                        "event": t.event,
                        "mode": t.reasoning_mode,
                        "confidence": t.confidence_after,
                        "outcome": t.outcome,
                        "corrected": t.correction_applied,
                    }
                    for t in recent_traces
                ],
                "recent_corrections": len(recent_corrections),
                "recent_success_rate": (
                    sum(1 for t in recent_outcomes if t.outcome == "success")
                    / max(1, len(recent_outcomes))
                ),
            },
            "health": self._assess_health(),
        }

    def _assess_health(self) -> str:
        """Overall health assessment of the agent's reasoning."""
        if self._metrics.total_decisions < 5:
            return "warming_up"
        if self._metrics.low_confidence_streak >= MAX_LOW_CONFIDENCE_STREAK:
            return "degraded"
        if self._metrics.success_rate < 0.4:
            return "poor"
        if self._metrics.avg_confidence < CONFIDENCE_MEDIUM:
            return "uncertain"
        if self._metrics.success_rate >= 0.7 and self._metrics.avg_confidence >= CONFIDENCE_HIGH:
            return "healthy"
        return "stable"
