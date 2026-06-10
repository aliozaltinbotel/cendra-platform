"""Monthly Evaluator — Accuracy metrics, skill quality, city maturity.

Runs monthly to evaluate Brain Engine performance and generate
reports for stakeholders. No training — only metrics and assessments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# City maturity thresholds
_CITY_LEARNING_MIN_BOOKINGS = 10
_CITY_MATURE_MIN_BOOKINGS = 50


class RecorderProtocol(Protocol):
    """Interface for interaction recorder."""

    async def get_graded(self, days: int = 30) -> list[Any]: ...
    async def count(self, days: int = 30) -> int: ...


class SkillEngineProtocol(Protocol):
    """Interface for skill evolution engine."""

    async def get_evolution_count(self, days: int = 30) -> int: ...


class ProceduralMemoryProtocol(Protocol):
    """Interface for procedural memory."""

    async def get_all_procedures(
        self, active_only: bool = True,
    ) -> list[Any]: ...


class CityKnowledgeProtocol(Protocol):
    """Interface for city knowledge graph."""

    async def get_all_cities(self) -> list[Any]: ...


@dataclass
class MonthlyReport:
    """Monthly performance report.

    Attributes:
        period_start: Report period start.
        period_end: Report period end.
        total_interactions: Total interactions in period.
        avg_grader_score: Average quality score.
        owner_intervention_rate: Rate of owner overrides.
        self_resolution_rate: Rate of autonomous resolutions.
        skills_evolved: New skills evolved in period.
        skills_total: Total active skills.
        skill_quality: Breakdown of skill confidence levels.
        city_maturity: City maturity status map.
    """

    period_start: str = ""
    period_end: str = ""
    total_interactions: int = 0
    avg_grader_score: float = 0.0
    owner_intervention_rate: float = 0.0
    self_resolution_rate: float = 0.0
    skills_evolved: int = 0
    skills_total: int = 0
    skill_quality: dict[str, int] = field(default_factory=dict)
    city_maturity: dict[str, str] = field(default_factory=dict)


class MonthlyEvaluator:
    """Generates monthly evaluation reports.

    Computes accuracy metrics, skill quality breakdown,
    and city maturity assessments.

    Args:
        recorder: Interaction recorder.
        skill_engine: Skill evolution engine.
        procedural_memory: Procedural memory store.
        city_knowledge: City knowledge graph.
    """

    def __init__(
        self,
        recorder: RecorderProtocol,
        skill_engine: SkillEngineProtocol,
        procedural_memory: ProceduralMemoryProtocol,
        city_knowledge: CityKnowledgeProtocol | None = None,
    ) -> None:
        self._recorder = recorder
        self._skills = skill_engine
        self._procedural = procedural_memory
        self._city_knowledge = city_knowledge

    async def evaluate(self, days: int = 30) -> MonthlyReport:
        """Run full monthly evaluation.

        Args:
            days: Evaluation window in days.

        Returns:
            MonthlyReport with all metrics.
        """
        report = MonthlyReport(
            period_end=datetime.now(timezone.utc).isoformat(),
        )

        await self._compute_interaction_metrics(report, days)
        await self._compute_skill_metrics(report, days)
        await self._compute_city_maturity(report)

        logger.info(
            "Monthly evaluation: score=%.2f resolution_rate=%.2f skills=%d",
            report.avg_grader_score,
            report.self_resolution_rate,
            report.skills_total,
        )
        return report

    async def get_accuracy(self, days: int = 7) -> float:
        """Get average accuracy score for the given period.

        Args:
            days: Lookback period.

        Returns:
            Average grader score (0.0-1.0).
        """
        graded = await self._recorder.get_graded(days=days)
        scores = [
            i.grader_score for i in graded
            if i.grader_score is not None
        ]
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 3)

    # ── Interaction Metrics ─────────────────────────────────────────── #

    async def _compute_interaction_metrics(
        self, report: MonthlyReport, days: int,
    ) -> None:
        """Fill interaction-related metrics in the report.

        Args:
            report: The report to fill.
            days: Evaluation window.
        """
        graded = await self._recorder.get_graded(days=days)
        report.total_interactions = len(graded)

        if not graded:
            return

        scores = [
            i.grader_score for i in graded
            if i.grader_score is not None
        ]
        report.avg_grader_score = _safe_mean(scores)

        interventions = sum(
            1 for i in graded
            if getattr(i, "owner_intervened", False)
        )
        report.owner_intervention_rate = round(
            interventions / len(graded), 3,
        )

        self_resolved = sum(
            1 for i in graded
            if getattr(i, "resolved_without_escalation", False)
        )
        report.self_resolution_rate = round(
            self_resolved / len(graded), 3,
        )

    # ── Skill Metrics ───────────────────────────────────────────────── #

    async def _compute_skill_metrics(
        self, report: MonthlyReport, days: int,
    ) -> None:
        """Fill skill-related metrics in the report.

        Args:
            report: The report to fill.
            days: Evaluation window.
        """
        report.skills_evolved = await self._skills.get_evolution_count(days)

        all_skills = await self._procedural.get_all_procedures()
        report.skills_total = len(all_skills)
        report.skill_quality = _classify_skills(all_skills)

    # ── City Maturity ───────────────────────────────────────────────── #

    async def _compute_city_maturity(self, report: MonthlyReport) -> None:
        """Evaluate city maturity levels.

        Args:
            report: The report to fill.
        """
        if not self._city_knowledge:
            return

        try:
            cities = await self._city_knowledge.get_all_cities()
        except Exception:
            logger.error("City maturity check failed", exc_info=True)
            return

        for city in cities:
            bookings = getattr(city, "total_bookings", 0)
            name = getattr(city, "city_name", str(city))
            report.city_maturity[name] = _assess_city_maturity(bookings)


# ── Helpers ─────────────────────────────────────────────────────────── #


def _classify_skills(skills: list[Any]) -> dict[str, int]:
    """Classify skills by confidence tier.

    Args:
        skills: List of procedures.

    Returns:
        Dict with tier counts.
    """
    tiers = {"high": 0, "medium": 0, "low": 0}
    for skill in skills:
        confidence = getattr(skill, "confidence", 0.5)
        if confidence >= 0.8:
            tiers["high"] += 1
        elif confidence >= 0.5:
            tiers["medium"] += 1
        else:
            tiers["low"] += 1
    return tiers


def _assess_city_maturity(bookings: int) -> str:
    """Determine city maturity level.

    Args:
        bookings: Total bookings processed for this city.

    Returns:
        Maturity level string.
    """
    if bookings >= _CITY_MATURE_MIN_BOOKINGS:
        return "MATURE"
    if bookings >= _CITY_LEARNING_MIN_BOOKINGS:
        return "LEARNING"
    return "NEW"


def _safe_mean(values: list[float]) -> float:
    """Compute mean with empty-list safety.

    Args:
        values: List of floats.

    Returns:
        Mean or 0.0.
    """
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)
