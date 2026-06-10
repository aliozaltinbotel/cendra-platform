"""AST nodes the parser emits.

Nodes are frozen dataclasses with slots so the compiler can rely on
hash-by-value equality and zero mutation surprises.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.brain.planning.styles import PlannerStyleId

__all__ = [
    "ComparisonOp",
    "NumericConstraint",
    "NumericMetric",
    "OwnerBlock",
    "PolicyDocument",
]


class NumericMetric(StrEnum):
    """Metrics callers may constrain in the DSL."""

    MIN_NIGHTS = "min_nights"
    MAX_NIGHTS = "max_nights"
    NIGHTLY_RATE = "nightly_rate"
    MAX_GUESTS = "max_guests"


class ComparisonOp(StrEnum):
    """Comparison operators the DSL accepts."""

    GE = ">="
    LE = "<="
    EQ = "=="
    GT = ">"
    LT = "<"


@dataclass(frozen=True, slots=True)
class NumericConstraint:
    """One ``min_nights >= 31``-style range clause.

    Attributes:
        metric: Which numeric quantity the constraint targets.
        op: Comparison operator.
        value: Right-hand integer literal.  Stored as ``int`` so
            the Z3 verifier can map directly to ``z3.Int``.
    """

    metric: NumericMetric
    op: ComparisonOp
    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int):
            raise TypeError("value must be int")


@dataclass(frozen=True, slots=True)
class OwnerBlock:
    """One ``owner "..." { ... }`` block in the source document.

    Attributes:
        owner_id: The owner identifier from the block header.
        style_id: Planner style pinned for the owner; ``None`` when
            no ``style = ...`` statement was present.
        jurisdiction: Jurisdiction code from a
            ``jurisdiction = "..."`` statement; ``None`` when absent.
        forbid: Extra action kinds the owner forbids on top of any
            denylist the pinned style already carries.  Empty when
            no ``forbid:`` statement was present.
        numeric_constraints: Tuple of numeric range clauses parsed
            from ``min_nights >= 31`` / ``nightly_rate >= 230``
            statements.  Empty when no numeric statements were
            present.
    """

    owner_id: str
    style_id: PlannerStyleId | None
    jurisdiction: str | None
    forbid: tuple[str, ...]
    numeric_constraints: tuple[NumericConstraint, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    """One parsed owner-policy DSL document.

    Attributes:
        owners: Ordered tuple of :class:`OwnerBlock` records, one
            per ``owner`` block in the source.  Order is preserved
            so error messages can cite the original line order.
    """

    owners: tuple[OwnerBlock, ...]
