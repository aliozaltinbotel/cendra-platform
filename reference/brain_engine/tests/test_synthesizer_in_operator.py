"""Tests for the Sprint 7 categorical ``in`` operator.

Synthesiser previously emitted only ``gte`` / ``lte`` / ``eq``
candidates.  For categoricals this meant a useful split like
"target action wins on ``source IN (booking.com, airbnb)``" had to
be approximated as a chain of two ``eq`` rules — which the greedy
search rejects when neither value alone clears the purity gate.

Sprint 7 adds an ``in`` candidate proposer behind the
``BRAIN_SYNTH_IN_OPERATOR_ENABLED`` env flag.  These tests pin
both halves of the contract:

* **Flag off (default)** — candidate set is bit-for-bit identical
  to the pre-Sprint-7 path: only ``eq`` for categoricals, only
  ``gte`` / ``lte`` for numerics.
* **Flag on** — ``in`` candidates of size 2 and 3 join the pool
  for fields with 3..8 distinct values; the synthesiser surfaces
  the higher-purity ``in`` rule when no single ``eq`` clears the
  gate.

Runtime evaluation (``models._evaluate_condition``) already
supports ``in``; no schema migration is required.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from brain_engine.patterns.condition_synthesizer import (
    ConditionCandidate,
    ConditionSynthesizer,
    _categorical_candidates,
    _categorical_in_candidates,
    _in_operator_enabled,
    _matches,
)
from brain_engine.patterns.models import (
    BookingStage,
    DecisionAction,
    DecisionCase,
    DecisionType,
    Scenario,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_in_operator_flag() -> Iterator[None]:
    """Ensure the Sprint 7 flag is unset at every test entry.

    Tests that need the flag on toggle it explicitly; the autouse
    reset guarantees no leak between tests on the same worker.
    """
    previous = os.environ.pop("BRAIN_SYNTH_IN_OPERATOR_ENABLED", None)
    try:
        yield
    finally:
        if previous is not None:
            os.environ["BRAIN_SYNTH_IN_OPERATOR_ENABLED"] = previous


def _make_case(
    *,
    case_id: str,
    decision_type: DecisionType,
    source: str,
) -> DecisionCase:
    """Build a minimal DecisionCase with one categorical PMS feature."""
    return DecisionCase(
        case_id=case_id,
        property_id="p1",
        owner_id="o1",
        stage=BookingStage.PRE_ARRIVAL,
        scenario=Scenario.ACCESS_CODE_RELEASE,
        message_text="msg",
        response_text="resp",
        decision=DecisionAction(action_type=decision_type),
        pms_snapshot={"source": source},
    )


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------


def test_flag_off_by_default() -> None:
    assert _in_operator_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " 1 "])
def test_flag_truthy_values(raw: str) -> None:
    os.environ["BRAIN_SYNTH_IN_OPERATOR_ENABLED"] = raw
    assert _in_operator_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "anything"])
def test_flag_falsy_values(raw: str) -> None:
    os.environ["BRAIN_SYNTH_IN_OPERATOR_ENABLED"] = raw
    assert _in_operator_enabled() is False


# ---------------------------------------------------------------------------
# _matches() now understands ``in`` and ``not_in``
# ---------------------------------------------------------------------------


def test_matches_in_hits_canonical_member() -> None:
    assert _matches("Booking.com", "in", ["booking.com", "airbnb"]) is True


def test_matches_in_misses_canonical_non_member() -> None:
    assert _matches("vrbo", "in", ["booking.com", "airbnb"]) is False


def test_matches_not_in_inverse() -> None:
    assert _matches("vrbo", "not_in", ["booking.com", "airbnb"]) is True
    assert _matches("Booking.com", "not_in", ["booking.com", "airbnb"]) is False


def test_matches_in_returns_false_when_actual_none() -> None:
    assert _matches(None, "in", ["a", "b"]) is False


def test_matches_in_returns_false_on_type_error() -> None:
    # Iterating over a non-iterable would raise; the helper traps and
    # returns False so a malformed condition cannot crash mining.
    assert _matches("a", "in", 5) is False


# ---------------------------------------------------------------------------
# _categorical_in_candidates direct invariants
# ---------------------------------------------------------------------------


def test_in_candidates_empty_when_below_three_distinct() -> None:
    target = [{"source": "a"}, {"source": "a"}]
    other = [{"source": "b"}]
    out = _categorical_in_candidates(
        key="source",
        target_features=target,
        other_features=other,
    )
    assert out == []


def test_in_candidates_empty_when_above_eight_distinct() -> None:
    target = [{"source": f"src{i}"} for i in range(9)]
    other: list[dict[str, str]] = []
    out = _categorical_in_candidates(
        key="source",
        target_features=target,
        other_features=other,
    )
    assert out == []


def test_in_candidates_emit_for_three_distinct() -> None:
    target = [{"source": "a"}, {"source": "b"}, {"source": "a"}]
    other = [{"source": "c"}, {"source": "c"}, {"source": "b"}]
    out = _categorical_in_candidates(
        key="source",
        target_features=target,
        other_features=other,
    )
    # Distinct = {a, b, c}.  Subsets size 2: {a,b}, {a,c}, {b,c}.
    # Subset size 3 == len(distinct) — skipped.  → 3 candidates.
    assert len(out) == 3
    operators = {candidate.operator for candidate in out}
    assert operators == {"in"}
    values = sorted(tuple(candidate.value) for candidate in out)
    assert values == [("a", "b"), ("a", "c"), ("b", "c")]


def test_in_candidates_purity_distinguishes_subsets() -> None:
    target = [
        {"source": "booking.com"},
        {"source": "airbnb"},
        {"source": "booking.com"},
    ]
    other = [
        {"source": "vrbo"},
        {"source": "vrbo"},
        {"source": "direct"},
    ]
    out = _categorical_in_candidates(
        key="source",
        target_features=target,
        other_features=other,
    )
    by_value = {tuple(candidate.value): candidate for candidate in out}
    pure_pair = by_value[("airbnb", "booking.com")]
    # All 3 target cases match, 0 other cases match → purity 1.0.
    assert pure_pair.target_matched == 3
    assert pure_pair.other_matched == 0
    assert pure_pair.purity == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _categorical_candidates flag gate — flag-off bit-for-bit identical
# ---------------------------------------------------------------------------


def test_categorical_candidates_flag_off_emits_only_eq() -> None:
    target = [{"source": "a"}, {"source": "b"}, {"source": "a"}]
    other = [{"source": "c"}, {"source": "c"}, {"source": "b"}]
    candidates = _categorical_candidates(
        key="source",
        target_features=target,
        other_features=other,
    )
    operators = {candidate.operator for candidate in candidates}
    assert operators == {"eq"}


def test_categorical_candidates_flag_on_appends_in_candidates() -> None:
    os.environ["BRAIN_SYNTH_IN_OPERATOR_ENABLED"] = "1"
    target = [{"source": "a"}, {"source": "b"}, {"source": "a"}]
    other = [{"source": "c"}, {"source": "c"}, {"source": "b"}]
    candidates = _categorical_candidates(
        key="source",
        target_features=target,
        other_features=other,
    )
    operators = {candidate.operator for candidate in candidates}
    # eq for each value seen on TARGET (a, b) plus in for each
    # 2-subset of distinct target+other (a,b),(a,c),(b,c).
    assert operators == {"eq", "in"}
    eq_values = sorted(
        candidate.value
        for candidate in candidates
        if candidate.operator == "eq"
    )
    assert eq_values == ["a", "b"]


# ---------------------------------------------------------------------------
# Synthesiser end-to-end: ``in`` rule wins when no single eq clears gate
# ---------------------------------------------------------------------------


def test_synthesiser_picks_in_subset_over_lone_eq_when_better_purity() -> None:
    os.environ["BRAIN_SYNTH_IN_OPERATOR_ENABLED"] = "1"
    target = [
        _make_case(case_id=f"t{i}", decision_type=DecisionType.APPROVE, source=src)
        for i, src in enumerate(("booking.com", "airbnb", "booking.com", "airbnb"))
    ]
    other = [
        _make_case(case_id=f"o{i}", decision_type=DecisionType.DENY, source=src)
        for i, src in enumerate(("vrbo", "vrbo", "direct", "direct"))
    ]
    synth = ConditionSynthesizer(min_purity=0.9, min_support_after=2)
    result, _ = synth.synthesize(target, other)
    assert not result.is_empty
    condition = result.conditions["source"]
    # Pure 2-subset {airbnb, booking.com} has purity 1.0 and matches
    # all 4 target cases — strictly dominates either ``eq``
    # singleton (each only matches 2 target cases).
    assert condition["operator"] == "in"
    assert sorted(condition["value"]) == ["airbnb", "booking.com"]
    assert result.support_count == 4
    assert result.counterexample_count == 0


def test_synthesiser_flag_off_falls_back_to_eq_or_empty() -> None:
    target = [
        _make_case(case_id=f"t{i}", decision_type=DecisionType.APPROVE, source=src)
        for i, src in enumerate(("booking.com", "airbnb", "booking.com", "airbnb"))
    ]
    other = [
        _make_case(case_id=f"o{i}", decision_type=DecisionType.DENY, source=src)
        for i, src in enumerate(("vrbo", "vrbo", "direct", "direct"))
    ]
    synth = ConditionSynthesizer(min_purity=0.9, min_support_after=2)
    result, _ = synth.synthesize(target, other)
    # With min_support_after=2 and min_purity=0.9, neither ``eq``
    # singleton clears (booking.com → support=2 purity=1.0 OK,
    # airbnb → support=2 purity=1.0 OK).  So the greedy first-pick
    # is one of them — but it splits the residual incorrectly so
    # the synthesiser can fall short of the full 4-case support.
    # We only assert that operator is NOT ``in`` here, since that's
    # the flag-off invariant.
    if not result.is_empty:
        for condition in result.conditions.values():
            assert condition["operator"] != "in"


# ---------------------------------------------------------------------------
# Crash isolation — bug in the proposer cannot kill mining
# ---------------------------------------------------------------------------


def test_in_proposer_crash_does_not_break_eq_path(monkeypatch) -> None:
    os.environ["BRAIN_SYNTH_IN_OPERATOR_ENABLED"] = "1"

    def _boom(**_kwargs: object) -> list[ConditionCandidate]:
        raise RuntimeError("simulated bug")

    monkeypatch.setattr(
        "brain_engine.patterns.condition_synthesizer."
        "_categorical_in_candidates",
        _boom,
    )
    target = [{"source": "a"}, {"source": "b"}, {"source": "a"}]
    other = [{"source": "c"}, {"source": "c"}, {"source": "b"}]
    candidates = _categorical_candidates(
        key="source",
        target_features=target,
        other_features=other,
    )
    # ``eq`` candidates still emitted; the ``in`` failure was
    # captured and logged, not propagated.
    operators = {candidate.operator for candidate in candidates}
    assert operators == {"eq"}
