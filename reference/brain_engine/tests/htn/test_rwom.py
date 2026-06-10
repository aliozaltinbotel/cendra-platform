"""Behaviour of :class:`RetrievalValueEstimator` and helpers."""

from __future__ import annotations

import pytest

from brain_engine.htn.models import (
    HTNPlan,
    Operator,
    PreferredSolver,
)
from brain_engine.htn.rwom import (
    PlanCorpus,
    PlanRecord,
    RetrievalValueEstimator,
    cosine_similarity,
    feature_bag,
)
from brain_engine.htn.tree import (
    PlanTreeNode,
    PlanTreeStatus,
)


def _op(name: str, *, cost: float = 1.0) -> Operator:
    return Operator(
        name=name, preferred_solver=PreferredSolver.LLM, cost=cost,
    )


def _node(*names: str) -> PlanTreeNode:
    operators = tuple(_op(n) for n in names)
    return PlanTreeNode(
        depth=len(operators),
        operators=operators,
        status=PlanTreeStatus.EVALUATED,
    )


def test_plan_record_reward_validation() -> None:
    """Reward outside ``[0, 1]`` is rejected."""
    with pytest.raises(ValueError, match="reward"):
        PlanRecord(operators=("a",), reward=1.5)
    with pytest.raises(ValueError, match="reward"):
        PlanRecord(operators=("a",), reward=-0.1)


def test_plan_record_caches_bag() -> None:
    """The bag is populated at construction (sequence Counter)."""
    record = PlanRecord(operators=("send", "log", "send"), reward=0.5)
    assert record.bag["send"] == 2
    assert record.bag["log"] == 1


def test_plan_record_from_plan_round_trip() -> None:
    """``from_plan`` extracts operator names + records reward."""
    plan = HTNPlan(operators=(_op("a"), _op("b")))
    record = PlanRecord.from_plan(plan, reward=0.7)
    assert record.operators == ("a", "b")
    assert record.reward == 0.7


def test_feature_bag_accepts_operators_and_strings() -> None:
    """Feature bag accepts mixed input types."""
    bag = feature_bag([_op("a"), "a", "b"])
    assert bag["a"] == 2
    assert bag["b"] == 1


def test_feature_bag_rejects_other_types() -> None:
    """Non-Operator non-str entries raise ``TypeError``."""
    with pytest.raises(TypeError, match="unsupported"):
        feature_bag([42])  # type: ignore[list-item]


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ({"x": 1}, {"x": 1}, 1.0),
        ({"x": 1}, {"y": 1}, 0.0),
        ({"x": 1, "y": 1}, {"x": 1}, pytest.approx(0.7071, abs=1e-3)),
        ({}, {"x": 1}, 0.0),
        ({"x": 1}, {}, 0.0),
    ],
    ids=["identical", "disjoint", "subset", "empty_a", "empty_b"],
)
def test_cosine_similarity(
    a: dict[str, int],
    b: dict[str, int],
    expected: float,
) -> None:
    """Cosine similarity returns the expected geometric value."""
    assert cosine_similarity(a, b) == expected


def test_corpus_add_and_records_round_trip() -> None:
    """Records added to the corpus appear in :meth:`records`."""
    corpus = PlanCorpus()
    corpus.add(PlanRecord(operators=("a",), reward=0.5))
    corpus.add_plan(HTNPlan(operators=(_op("b"),)), reward=0.7)
    assert len(corpus) == 2
    assert {r.operators for r in corpus.records()} == {
        ("a",),
        ("b",),
    }


def test_estimator_returns_empty_score_when_corpus_empty() -> None:
    """Empty corpus → ``empty_score`` (configurable)."""
    estimator = RetrievalValueEstimator(
        corpus=PlanCorpus(),
        empty_score=0.42,
    )
    assert estimator.score(_node("a")) == 0.42


def test_estimator_returns_empty_score_when_query_empty() -> None:
    """Query with no operators → empty_score."""
    corpus = PlanCorpus(
        records=[PlanRecord(operators=("a",), reward=0.9)],
    )
    estimator = RetrievalValueEstimator(corpus=corpus)
    empty_node = PlanTreeNode(
        depth=0, operators=(), status=PlanTreeStatus.EVALUATED,
    )
    assert estimator.score(empty_node) == 0.0


def test_estimator_returns_weighted_mean() -> None:
    """Score is the similarity-weighted mean of corpus rewards."""
    corpus = PlanCorpus(
        records=[
            PlanRecord(operators=("send", "log"), reward=0.8),
            PlanRecord(operators=("send", "log"), reward=1.0),
        ],
    )
    estimator = RetrievalValueEstimator(corpus=corpus)
    score = estimator.score(_node("send", "log"))
    # Both records perfectly match → mean = 0.9.
    assert score == pytest.approx(0.9)


def test_estimator_min_similarity_filters_dissimilar_records() -> None:
    """Records below ``min_similarity`` do not contribute."""
    corpus = PlanCorpus(
        records=[
            PlanRecord(operators=("send", "log"), reward=0.9),
            PlanRecord(operators=("escalate",), reward=0.1),
        ],
    )
    high_floor = RetrievalValueEstimator(
        corpus=corpus, min_similarity=0.5,
    )
    score = high_floor.score(_node("send", "log"))
    # Escalate record is dissimilar → only the matching record
    # contributes → score equals its reward.
    assert score == pytest.approx(0.9)


def test_estimator_constructor_validation() -> None:
    """Out-of-range floor / empty_score raise."""
    corpus = PlanCorpus()
    with pytest.raises(ValueError, match="min_similarity"):
        RetrievalValueEstimator(corpus=corpus, min_similarity=1.5)
    with pytest.raises(ValueError, match="empty_score"):
        RetrievalValueEstimator(corpus=corpus, empty_score=-0.1)


def test_estimator_returns_empty_score_when_no_record_clears_floor() -> None:
    """All records below floor → fall back to empty_score."""
    corpus = PlanCorpus(
        records=[PlanRecord(operators=("escalate",), reward=0.5)],
    )
    estimator = RetrievalValueEstimator(
        corpus=corpus,
        min_similarity=0.99,
        empty_score=0.123,
    )
    assert estimator.score(_node("send")) == pytest.approx(0.123)
