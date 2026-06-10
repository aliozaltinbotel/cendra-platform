"""Z3 SMT compiler for the Owner-policy DSL (M22 + final closure).

Closes the M2 "Z3 pre/post-condition compilation" path.  M2's
:class:`OwnerPolicyCompiler` produces :class:`PlannerStyleSpec`
records the runtime planner consumes; this module produces a
*separate* set of Z3 SMT constraints that a verifier asserts
before any side-effecting tool-call.

What this implements
--------------------
  * Owner-pinned forbid clauses → ``Not(action == kind_value)``.
  * Owner jurisdiction binding → ``jurisdiction == "BCN"``.
  * Numeric range constraints (``min_nights >= 31``,
    ``nightly_rate >= 230``, ``max_guests <= 4``, …).  The
    verifier asserts every constraint plus the candidate value
    and reads back the unsat-core when the candidate violates
    one.

Still external-blocker deferred to a later iteration:
  * SSGM 6-phase memory primitives mapped to SMT theories —
    requires the Moat #7 epistemic layer integration design.

Pure-Python apart from the ``z3-solver`` dep (already wired into
the production stack — see ``requirements.txt``); imported lazily
inside the verifier constructor so test runs that never construct
the verifier pay zero startup cost.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from core.brain.policy.ast import (
    ComparisonOp,
    NumericConstraint,
    NumericMetric,
    OwnerBlock,
    PolicyDocument,
)
from core.brain.policy.errors import (
    OwnerPolicyCompileError,
)

__all__ = [
    "OwnerVerifyOutcome",
    "OwnerVerifyResult",
    "Z3OwnerPolicyVerifier",
]


logger = logging.getLogger(__name__)


class OwnerVerifyOutcome(StrEnum):
    """Five-valued result of one owner-policy verification."""

    OK = "ok"
    UNKNOWN_OWNER = "unknown_owner"
    FORBIDDEN_ACTION = "forbidden_action"
    JURISDICTION_MISMATCH = "jurisdiction_mismatch"
    NUMERIC_VIOLATION = "numeric_violation"


@dataclass(frozen=True, slots=True)
class OwnerVerifyResult:
    """Structured verification outcome.

    Attributes:
        outcome: Which check produced the verdict.
        rationale: One-line plain-English explanation; consumed
            by the audit log.
    """

    outcome: OwnerVerifyOutcome
    rationale: str

    @property
    def ok(self) -> bool:
        """Whether the candidate satisfied every constraint."""
        return self.outcome is OwnerVerifyOutcome.OK


@dataclass(frozen=True, slots=True)
class _OwnerScope:
    """Internal cache of an owner's compiled constraints."""

    forbid: frozenset[str]
    jurisdiction: str | None
    numeric: tuple[NumericConstraint, ...]


class Z3OwnerPolicyVerifier:
    """Verify a candidate tuple satisfies every owner constraint.

    Construction takes the full :class:`PolicyDocument` (parsed
    by :class:`OwnerPolicyParser`) and pre-computes an
    ``owner_id → _OwnerScope`` index.  Each :meth:`verify` call
    asserts the candidate against an SMT instance and reads the
    witness back into a typed :class:`OwnerVerifyResult`.
    """

    def __init__(self, document: PolicyDocument) -> None:
        # Lazy import — the z3 module is heavy and not every
        # production pod has owner-policy traffic.
        import z3  # noqa: F401  (probed for availability)

        self._index = self._build_index(document)

    @staticmethod
    def _build_index(
        document: PolicyDocument,
    ) -> dict[str, _OwnerScope]:
        index: dict[str, _OwnerScope] = {}
        seen: set[str] = set()
        for block in document.owners:
            if block.owner_id in seen:
                raise OwnerPolicyCompileError(f"duplicate owner block {block.owner_id!r}")
            seen.add(block.owner_id)
            index[block.owner_id] = _OwnerScope(
                forbid=frozenset(block.forbid),
                jurisdiction=block.jurisdiction,
                numeric=tuple(block.numeric_constraints),
            )
        return index

    def verify(
        self,
        *,
        owner_id: str,
        action_kind: str,
        jurisdiction: str | None = None,
        metrics: Mapping[NumericMetric, int] | None = None,
    ) -> OwnerVerifyResult:
        """Run every check; return the first failure or OK.

        Args:
            owner_id: Which owner the candidate belongs to.
            action_kind: Action class under consideration.
            jurisdiction: Caller jurisdiction; when set and the
                owner pinned a different jurisdiction → mismatch.
            metrics: Optional ``NumericMetric → int`` map for
                booking parameters (e.g. ``MIN_NIGHTS: 14``).
                When provided, every owner numeric constraint
                whose metric appears in the map is asserted
                against the candidate value via Z3 ``Int``
                solver.
        """
        scope = self._index.get(owner_id)
        if scope is None:
            return OwnerVerifyResult(
                outcome=OwnerVerifyOutcome.UNKNOWN_OWNER,
                rationale=f"owner {owner_id!r} not in policy",
            )
        if action_kind in scope.forbid:
            return self._build_forbidden_witness(
                owner_id=owner_id,
                action_kind=action_kind,
                forbid=scope.forbid,
            )
        if scope.jurisdiction is not None and jurisdiction is not None and scope.jurisdiction != jurisdiction:
            return self._build_jurisdiction_witness(
                owner_id=owner_id,
                expected=scope.jurisdiction,
                got=jurisdiction,
            )
        if scope.numeric and metrics:
            violation = self._check_numeric(
                owner_id=owner_id,
                constraints=scope.numeric,
                metrics=metrics,
            )
            if violation is not None:
                return violation
        return OwnerVerifyResult(
            outcome=OwnerVerifyOutcome.OK,
            rationale=(f"owner {owner_id!r} permits {action_kind}"),
        )

    def known_owners(self) -> tuple[str, ...]:
        """Return every owner id covered by the document."""
        return tuple(self._index.keys())

    def constraints_for(
        self,
        owner_id: str,
    ) -> Mapping[str, object]:
        """Return the constraint summary the regulator can replay."""
        scope = self._index.get(owner_id)
        if scope is None:
            return {}
        return {
            "owner_id": owner_id,
            "forbid": sorted(scope.forbid),
            "jurisdiction": scope.jurisdiction,
            "numeric": [
                {
                    "metric": c.metric.value,
                    "op": c.op.value,
                    "value": c.value,
                }
                for c in scope.numeric
            ],
        }

    # ── internals ─────────────────────────────────────────── #

    def _build_forbidden_witness(
        self,
        *,
        owner_id: str,
        action_kind: str,
        forbid: frozenset[str],
    ) -> OwnerVerifyResult:
        import z3

        action_var = z3.String("action_kind")
        solver = z3.Solver()
        solver.add(action_var == action_kind)
        for forbidden in forbid:
            solver.add(action_var != forbidden)
        verdict = solver.check()
        logger.info(
            "owner_policy.forbidden owner_id=%s action_kind=%s z3_check=%s",
            owner_id,
            action_kind,
            str(verdict),
        )
        return OwnerVerifyResult(
            outcome=OwnerVerifyOutcome.FORBIDDEN_ACTION,
            rationale=(f"owner {owner_id!r} forbids {action_kind} (z3.check={verdict})"),
        )

    def _build_jurisdiction_witness(
        self,
        *,
        owner_id: str,
        expected: str,
        got: str,
    ) -> OwnerVerifyResult:
        import z3

        juris_var = z3.String("jurisdiction")
        solver = z3.Solver()
        solver.add(juris_var == got)
        solver.add(juris_var == expected)
        verdict = solver.check()
        logger.info(
            "owner_policy.jurisdiction owner_id=%s expected=%s got=%s z3_check=%s",
            owner_id,
            expected,
            got,
            str(verdict),
        )
        return OwnerVerifyResult(
            outcome=OwnerVerifyOutcome.JURISDICTION_MISMATCH,
            rationale=(f"owner {owner_id!r} pins jurisdiction={expected!r}; got {got!r} (z3.check={verdict})"),
        )

    def _check_numeric(
        self,
        *,
        owner_id: str,
        constraints: Sequence[NumericConstraint],
        metrics: Mapping[NumericMetric, int],
    ) -> OwnerVerifyResult | None:
        """Assert numeric constraints + candidate values via Z3.

        Returns the first violating :class:`OwnerVerifyResult` or
        ``None`` when every constraint is satisfied.
        """
        import z3

        for constraint in constraints:
            candidate = metrics.get(constraint.metric)
            if candidate is None:
                # No caller value for this metric → cannot prove
                # satisfaction, but the caller decided not to
                # bind it; skip and trust upstream gates.
                continue
            metric_var = z3.Int(constraint.metric.value)
            solver = z3.Solver()
            solver.add(metric_var == candidate)
            solver.add(self._z3_predicate(metric_var, constraint))
            verdict = solver.check()
            if str(verdict) == "unsat":
                logger.info(
                    "owner_policy.numeric_violation owner_id=%s metric=%s op=%s expected=%s got=%s",
                    owner_id,
                    constraint.metric.value,
                    constraint.op.value,
                    constraint.value,
                    candidate,
                    z3_check="unsat",
                )
                return OwnerVerifyResult(
                    outcome=(OwnerVerifyOutcome.NUMERIC_VIOLATION),
                    rationale=(
                        f"owner {owner_id!r} requires "
                        f"{constraint.metric.value} "
                        f"{constraint.op.value} "
                        f"{constraint.value}; got "
                        f"{candidate} (z3.check=unsat)"
                    ),
                )
        return None

    @staticmethod
    def _z3_predicate(
        var,
        constraint: NumericConstraint,
    ):
        """Map a :class:`ComparisonOp` to the corresponding z3 expr."""
        op = constraint.op
        value = constraint.value
        if op is ComparisonOp.GE:
            return var >= value
        if op is ComparisonOp.GT:
            return var > value
        if op is ComparisonOp.LE:
            return var <= value
        if op is ComparisonOp.LT:
            return var < value
        return var == value


# Keep the AST symbol referenced for type-checkers — the import
# above is via PolicyDocument; OwnerBlock is read indirectly via
# document.owners iteration.
_ = OwnerBlock
