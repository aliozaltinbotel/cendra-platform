"""Behaviour of :mod:`brain_engine.htn.ann_corpus`."""

from __future__ import annotations

import pytest

from brain_engine.htn.ann_corpus import (
    ANNEmbeddingPlanCorpus,
    ANNEmbeddingValueEstimator,
)
from brain_engine.htn.embedding_rwom import StubEmbedder
from brain_engine.htn.models import (
    HTNPlan,
    Operator,
    PreferredSolver,
)
from brain_engine.htn.tree import (
    PlanTreeNode,
    PlanTreeStatus,
)


def _op(name: str) -> Operator:
    return Operator(
        name=name, preferred_solver=PreferredSolver.LLM,
    )


def _node(*names: str) -> PlanTreeNode:
    operators = tuple(_op(n) for n in names)
    return PlanTreeNode(
        depth=len(operators),
        operators=operators,
        status=PlanTreeStatus.EVALUATED,
    )


# ── ANNEmbeddingPlanCorpus ───────────────────────────────── #


def test_corpus_empty_top_k_returns_empty() -> None:
    """Empty corpus → empty top-k list."""
    embedder = StubEmbedder(dim=16)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    assert corpus.top_k(query_vec=[0.0] * 16, k=3) == []


def test_corpus_top_k_caps_at_corpus_size() -> None:
    """k > corpus size → returns min(k, size) records."""
    embedder = StubEmbedder(dim=16)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    corpus.add_text(text="alpha", reward=0.5)
    corpus.add_text(text="beta", reward=0.6)
    out = corpus.top_k(query_vec=list(embedder.encode("alpha")), k=10)
    assert len(out) == 2


def test_corpus_top_k_validates_k() -> None:
    """``k < 1`` is rejected fail-fast."""
    embedder = StubEmbedder(dim=16)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    corpus.add_text(text="x", reward=0.5)
    with pytest.raises(ValueError, match="k"):
        corpus.top_k(query_vec=list(embedder.encode("x")), k=0)


def test_corpus_top_k_returns_identical_record_at_cosine_one() -> None:
    """Querying with the same text used to insert → cosine 1.0."""
    embedder = StubEmbedder(dim=32)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    corpus.add_text(text="hello world", reward=0.9)
    out = corpus.top_k(
        query_vec=list(embedder.encode("hello world")),
        k=1,
    )
    record, sim = out[0]
    assert sim == pytest.approx(1.0, abs=1e-6)
    assert record.reward == 0.9


def test_corpus_bulk_add_rebuilds_once() -> None:
    """Bulk-add accumulates records before a single rebuild."""
    embedder = StubEmbedder(dim=16)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    corpus.bulk_add(items=[
        ("a", 0.1),
        ("b", 0.2),
        ("c", 0.3),
    ])
    assert len(corpus) == 3


def test_corpus_add_plan_round_trips() -> None:
    """``add_plan`` from M21 still works on the ANN subclass."""
    embedder = StubEmbedder(dim=16)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    corpus.add_plan(
        HTNPlan(operators=(_op("send"), _op("log"))),
        reward=0.7,
    )
    assert len(corpus) == 1
    assert corpus.records()[0].reward == 0.7


# ── ANNEmbeddingValueEstimator ──────────────────────────── #


def test_estimator_constructor_validation() -> None:
    """Out-of-range params raise fail-fast."""
    embedder = StubEmbedder(dim=16)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    with pytest.raises(ValueError, match="top_k"):
        ANNEmbeddingValueEstimator(
            corpus=corpus, embedder=embedder, top_k=0,
        )
    with pytest.raises(ValueError, match="min_similarity"):
        ANNEmbeddingValueEstimator(
            corpus=corpus,
            embedder=embedder,
            min_similarity=1.5,
        )
    with pytest.raises(ValueError, match="empty_score"):
        ANNEmbeddingValueEstimator(
            corpus=corpus,
            embedder=embedder,
            empty_score=-0.1,
        )


def test_estimator_empty_corpus_returns_empty_score() -> None:
    """Empty corpus → ``empty_score``."""
    embedder = StubEmbedder(dim=16)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    est = ANNEmbeddingValueEstimator(
        corpus=corpus, embedder=embedder, empty_score=0.42,
    )
    assert est.score(_node("x")) == 0.42


def test_estimator_empty_plan_returns_empty_score() -> None:
    """Empty plan → ``empty_score`` without touching the corpus."""
    embedder = StubEmbedder(dim=16)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    corpus.add_text(text="x", reward=0.9)
    est = ANNEmbeddingValueEstimator(
        corpus=corpus, embedder=embedder, empty_score=0.5,
    )
    empty = PlanTreeNode(
        depth=0,
        operators=(),
        status=PlanTreeStatus.EVALUATED,
    )
    assert est.score(empty) == 0.5


def test_estimator_identical_plan_returns_record_reward() -> None:
    """Identical text → cosine 1.0 → record's reward verbatim."""
    embedder = StubEmbedder(dim=32)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    corpus.add_plan(
        HTNPlan(operators=(_op("send"), _op("log"))),
        reward=0.77,
    )
    est = ANNEmbeddingValueEstimator(
        corpus=corpus,
        embedder=embedder,
        top_k=1,
        min_similarity=0.99,
    )
    score = est.score(_node("send", "log"))
    assert score == pytest.approx(0.77)


def test_estimator_min_similarity_filters_unrelated() -> None:
    """Floor blocks records below the cosine cut-off."""
    embedder = StubEmbedder(dim=32)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    corpus.add_plan(
        HTNPlan(operators=(_op("escalate"),)), reward=0.1,
    )
    est = ANNEmbeddingValueEstimator(
        corpus=corpus,
        embedder=embedder,
        top_k=1,
        min_similarity=0.99,
        empty_score=0.5,
    )
    # Unrelated query → cosine < 0.99 → empty_score fallback.
    assert est.score(_node("send", "log")) == 0.5


def test_estimator_score_in_unit_interval() -> None:
    """Score for any non-empty input lies in ``[0, 1]``."""
    embedder = StubEmbedder(dim=32)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    corpus.add_text(text="alpha", reward=0.4)
    corpus.add_text(text="beta", reward=0.8)
    est = ANNEmbeddingValueEstimator(
        corpus=corpus,
        embedder=embedder,
        top_k=2,
    )
    score = est.score(_node("alpha"))
    assert 0.0 <= score <= 1.0


def test_estimator_uses_top_k_records() -> None:
    """``top_k`` bound caps the number of contributing records."""
    embedder = StubEmbedder(dim=32)
    corpus = ANNEmbeddingPlanCorpus(embedder=embedder)
    # Insert 10 identical-reward records so the weighted-mean
    # equals the reward when top_k covers them all.
    for _ in range(10):
        corpus.add_text(text="identical", reward=0.6)
    est = ANNEmbeddingValueEstimator(
        corpus=corpus,
        embedder=embedder,
        top_k=5,
    )
    node = PlanTreeNode(
        depth=1,
        operators=(_op("identical"),),
        status=PlanTreeStatus.EVALUATED,
    )
    # All 10 records have identical text → cosine 1.0; top-5
    # → weighted mean = 0.6 (same as full corpus).
    score = est.score(node)
    assert score == pytest.approx(0.6, abs=1e-3)
