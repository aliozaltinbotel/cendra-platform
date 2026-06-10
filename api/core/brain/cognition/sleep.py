"""Sleep-time consolidation summary (Moat #14 v0.1).

Brain Engine already runs a nightly worker in
:mod:`brain_engine.continual_learning.nightly_consolidator`; this
module ships only the *summary* surface the v0.1 ACE / Memory-R1
protocol needs — a frozen :class:`ConsolidationReport` carrying
the day's tally of resolved decisions plus the playbook delta the
worker proposes for the next day.  The actual back-propagation of
rewards into the GRPO trainer lands in v1.0.

The summary is computed by :func:`summarise_decisions` over a
sequence of :class:`ResolvedDecision` records; each kind tally
becomes one entry in :attr:`ConsolidationReport.kind_counts`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from core.brain.cognition.models import (
    MemoryOpKind,
    ResolvedDecision,
)

__all__ = [
    "MIN_DECISIONS_FOR_PLAYBOOK_BUMP",
    "ConsolidationReport",
    "summarise_decisions",
]


MIN_DECISIONS_FOR_PLAYBOOK_BUMP: Final[int] = 5


@dataclass(frozen=True, slots=True)
class ConsolidationReport:
    """Summary of one nightly consolidation run.

    Attributes:
        period_start: tz-aware UTC instant the period began.
        period_end: tz-aware UTC instant the period ended.
        kind_counts: ``MemoryOpKind`` → count of resolved
            decisions whose ``applied_kind`` matched.  Includes
            zero entries only when the kind appeared at least
            once.
        unresolved_targets: Targets whose decisions were
            split-vote NOOPs ("ACE ADD vs Memory-R1 DELETE; defer
            to nightly consolidation") and now warrant a human
            review or a follow-up cycle.
        playbook_bump_recommended: ``True`` when ``ADD`` count
            crosses :data:`MIN_DECISIONS_FOR_PLAYBOOK_BUMP` and
            no unresolved targets remain — the worker is signal-
            ling a new stable playbook entry.
    """

    period_start: datetime
    period_end: datetime
    kind_counts: Mapping[MemoryOpKind, int]
    unresolved_targets: tuple[str, ...] = ()
    playbook_bump_recommended: bool = False

    def __post_init__(self) -> None:
        if self.period_start.tzinfo is None:
            raise ValueError("period_start must be tz-aware")
        if self.period_end.tzinfo is None:
            raise ValueError("period_end must be tz-aware")
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")


def summarise_decisions(
    decisions: Sequence[ResolvedDecision],
    *,
    period_start: datetime,
    period_end: datetime,
) -> ConsolidationReport:
    """Build a :class:`ConsolidationReport` from a decision log."""
    if period_start.tzinfo is None:
        raise ValueError("period_start must be tz-aware")
    if period_end.tzinfo is None:
        raise ValueError("period_end must be tz-aware")
    counter: Counter[MemoryOpKind] = Counter()
    unresolved: list[str] = []
    for decision in decisions:
        counter[decision.applied_kind] += 1
        if "defer to nightly consolidation" in decision.reason:
            unresolved.append(decision.target)
    add_count = counter.get(MemoryOpKind.ADD, 0)
    bump = add_count >= MIN_DECISIONS_FOR_PLAYBOOK_BUMP and not unresolved
    return ConsolidationReport(
        period_start=period_start,
        period_end=period_end,
        kind_counts=dict(counter),
        unresolved_targets=tuple(unresolved),
        playbook_bump_recommended=bump,
    )
