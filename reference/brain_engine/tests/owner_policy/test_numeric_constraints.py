"""Numeric range constraints in the Z3 owner-policy verifier."""

from __future__ import annotations

import pytest

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.owner_policy.ast import (
    ComparisonOp,
    NumericConstraint,
    NumericMetric,
)
from brain_engine.owner_policy.errors import (
    OwnerPolicyCompileError,
)
from brain_engine.owner_policy.parser import OwnerPolicyParser
from brain_engine.owner_policy.z3_compiler import (
    OwnerVerifyOutcome,
    Z3OwnerPolicyVerifier,
)


_SAMPLE = """
owner "alice" {
  jurisdiction = "BCN";
  forbid: charge_fee;
  min_nights >= 31;
  nightly_rate >= 230;
  max_guests <= 4;
}
"""


@pytest.fixture
def verifier() -> Z3OwnerPolicyVerifier:
    return Z3OwnerPolicyVerifier(OwnerPolicyParser().parse(_SAMPLE))


# ── Parser ──────────────────────────────────────────────── #


def test_parser_extracts_numeric_constraints() -> None:
    """Three numeric statements parse into three constraints."""
    doc = OwnerPolicyParser().parse(_SAMPLE)
    block = doc.owners[0]
    assert len(block.numeric_constraints) == 3
    metrics = {c.metric for c in block.numeric_constraints}
    assert metrics == {
        NumericMetric.MIN_NIGHTS,
        NumericMetric.NIGHTLY_RATE,
        NumericMetric.MAX_GUESTS,
    }


def test_parser_supports_every_comparison_op() -> None:
    """All five comparison ops round-trip through the parser."""
    source = """
    owner "x" {
      min_nights >= 1;
      max_nights <= 30;
      nightly_rate > 100;
      max_guests < 10;
      max_guests == 4;
    }
    """
    block = OwnerPolicyParser().parse(source).owners[0]
    ops = {c.op for c in block.numeric_constraints}
    assert ops == {
        ComparisonOp.GE,
        ComparisonOp.LE,
        ComparisonOp.GT,
        ComparisonOp.LT,
        ComparisonOp.EQ,
    }


def test_parser_rejects_unknown_numeric_metric() -> None:
    """An unknown metric raises :class:`OwnerPolicyCompileError`."""
    # Use a Lark-valid identifier shape that still won't be a
    # recognized NumericMetric.  The grammar restricts the
    # token to known metric names so this raises at parse
    # time as a syntax error rather than a compile error.
    with pytest.raises(Exception):
        OwnerPolicyParser().parse(
            'owner "x" { unknown_metric >= 1; }'
        )


# ── Z3 verifier ────────────────────────────────────────── #


def test_all_constraints_pass(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """Candidate satisfying every numeric constraint passes."""
    result = verifier.verify(
        owner_id="alice",
        action_kind=CardActionKind.CONFIRM_BOOKING,
        jurisdiction="BCN",
        metrics={
            NumericMetric.MIN_NIGHTS: 35,
            NumericMetric.NIGHTLY_RATE: 250,
            NumericMetric.MAX_GUESTS: 3,
        },
    )
    assert result.outcome is OwnerVerifyOutcome.OK


def test_min_nights_violation_blocks(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """``min_nights >= 31`` violated by 14 → NUMERIC_VIOLATION."""
    result = verifier.verify(
        owner_id="alice",
        action_kind=CardActionKind.CONFIRM_BOOKING,
        jurisdiction="BCN",
        metrics={NumericMetric.MIN_NIGHTS: 14},
    )
    assert result.outcome is OwnerVerifyOutcome.NUMERIC_VIOLATION
    assert "min_nights" in result.rationale
    assert "31" in result.rationale
    assert "z3.check=unsat" in result.rationale


def test_nightly_rate_violation_blocks(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """``nightly_rate >= 230`` violated by 180."""
    result = verifier.verify(
        owner_id="alice",
        action_kind=CardActionKind.CONFIRM_BOOKING,
        jurisdiction="BCN",
        metrics={NumericMetric.NIGHTLY_RATE: 180},
    )
    assert result.outcome is OwnerVerifyOutcome.NUMERIC_VIOLATION
    assert "nightly_rate" in result.rationale


def test_max_guests_violation_blocks(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """``max_guests <= 4`` violated by 6."""
    result = verifier.verify(
        owner_id="alice",
        action_kind=CardActionKind.CONFIRM_BOOKING,
        jurisdiction="BCN",
        metrics={NumericMetric.MAX_GUESTS: 6},
    )
    assert result.outcome is OwnerVerifyOutcome.NUMERIC_VIOLATION


def test_missing_metric_skips_check(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """Caller may decline to bind a metric → check is skipped."""
    # No metrics supplied — every numeric constraint is skipped.
    result = verifier.verify(
        owner_id="alice",
        action_kind=CardActionKind.CONFIRM_BOOKING,
        jurisdiction="BCN",
    )
    assert result.outcome is OwnerVerifyOutcome.OK


def test_constraints_for_includes_numeric(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """The regulator-replay summary lists every numeric clause."""
    summary = verifier.constraints_for("alice")
    numeric = summary["numeric"]
    assert isinstance(numeric, list)
    assert len(numeric) == 3
    by_metric = {row["metric"]: row for row in numeric}
    assert by_metric["min_nights"]["op"] == ">="
    assert by_metric["min_nights"]["value"] == 31


def test_numeric_constraint_value_type_validation() -> None:
    """``NumericConstraint.value`` must be ``int``."""
    with pytest.raises(TypeError, match="int"):
        NumericConstraint(
            metric=NumericMetric.MIN_NIGHTS,
            op=ComparisonOp.GE,
            value="thirty-one",  # type: ignore[arg-type]
        )


def test_partial_metrics_only_check_supplied(
    verifier: Z3OwnerPolicyVerifier,
) -> None:
    """Verifier checks only the metrics the caller supplies."""
    # Supply just MIN_NIGHTS — others are skipped.
    result = verifier.verify(
        owner_id="alice",
        action_kind=CardActionKind.CONFIRM_BOOKING,
        jurisdiction="BCN",
        metrics={NumericMetric.MIN_NIGHTS: 40},
    )
    assert result.outcome is OwnerVerifyOutcome.OK
