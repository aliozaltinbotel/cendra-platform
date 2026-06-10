"""Calendar intelligence — gap analysis, orphan nights, scheduling feasibility.

Public surface:

- :class:`CalendarEvaluator` — gap analysis + orphan detection +
  min-stay exception feasibility + early/late check timing.
- :class:`GapInfo` — frozen value object for one vacant gap.
- :class:`FeasibilityResult` — return type for every feasibility check.
"""

from __future__ import annotations

from brain_engine.calendar.evaluator import (
    CalendarEvaluator,
    FeasibilityResult,
    GapInfo,
)

__all__ = [
    "CalendarEvaluator",
    "FeasibilityResult",
    "GapInfo",
]
