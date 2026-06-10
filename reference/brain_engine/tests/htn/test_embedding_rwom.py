"""Behaviour of :mod:`brain_engine.htn.embedding_rwom`."""

from __future__ import annotations

import pytest

from brain_engine.htn.embedding_rwom import (
    EmbeddedPlanRecord,
    EmbeddingPlanCorpus,
    EmbeddingValueEstimator,
    StubEmbedder,
    cosine_vec,
    plan_to_text,
)
from brain_engine.htn.models import (
    HTNPlan,
    Operator,
    PreferredSolver,
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


# ── plan_to_text ─────────────────────────────────────────── #


def test_plan_to_text_renders_steps() -> None:
    text = plan_to_text((_op("a"), _op("b"), _op("c")))
    assert text == "step 1: a; step 2: b; step 3: c"


def test_plan_to_text_empty() -> None:
    assert plan_to_text(()) == "empty plan"


def test_plan_to_text_accepts_strings() -> None:
    assert plan_to_text(["a", "b"]) == "step 1: a; step 2: b"


def test_plan_to_text_rejects_other_types() -> None:
    with pytest.raises(TypeError, match="unsupported"):
        plan_to_text([42])  # type: ignore[list-item]


# ── cosine_vec ───────────────────────────────────────────── #


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ([1.0, 0.0], [1.0, 0.0], pytest.approx(1.0)),
        ([1.0, 0.0], [0.0, 1.0], pytest.approx(0.0)),
        ([1.0, 1.0], [1.0, 1.0], pytest.approx(1.0)),
        ([0.0, 0.0], [1.0, 1.0], pytest.approx(0.0)),
    ],
    ids=["identical", "orthogonal", "parallel", "zero_norm"],
)
def test_cosine_vec(
    a: list[float],
    b: list[float],
    expected: float,
) -> None:
    assert cosine_vec(a, b) == expected


def test_cosine_vec_empty_returns_zero() -> None:
    assert cosine_vec([], []) == 0.0


def test_cosine_vec_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        cosine_vec([1.0], [1.0, 2.0])


# ── StubEmbedder ─────────────────────────────────────────── #


def test_stub_embedder_dim_validation() -> None:
    with pytest.raises(ValueError, match="dim"):
        StubEmbedder(dim=0)


def test_stub_embedder_is_deterministic() -> None:
    embedder = StubEmbedder(dim=16)
    assert embedder.encode("hello") == embedder.encode("hello")


def test_stub_embedder_different_inputs_differ() -> None:
    embedder = StubEmbedder(dim=16)
    assert embedder.encode("alpha") != embedder.encode("beta")


def test_stub_embedder_dim_round_trips() -> None:
    embedder = StubEmbedder(dim=32)
    assert embedder.dim == 32
    assert len(embedder.encode("x")) == 32


def test_stub_embedder_supports_large_dim() -> None:
    """SHAKE128 backing supports arbitrary dims (not blocked by 64-byte cap)."""
    embedder = StubEmbedder(dim=128)
    assert len(embedder.encode("x")) == 128


# ── EmbeddedPlanRecord ──────────────────────────────────── #


def test_embedded_plan_record_validation() -> None:
    with pytest.raises(ValueError, match="reward"):
        EmbeddedPlanRecord(embedding=(0.1,), reward=1.5)
    with pytest.raises(ValueError, match="embedding"):
        EmbeddedPlanRecord(embedding=(), reward=0.5)


# ── EmbeddingPlanCorpus ─────────────────────────────────── #


def test_corpus_add_text_round_trip() -> None:
    embedder = StubEmbedder(dim=8)
    corpus = EmbeddingPlanCorpus(embedder=embedder)
    corpus.add_text(text="hello", reward=0.9)
    records = corpus.records()
    assert len(records) == 1
    assert records[0].reward == 0.9
    assert records[0].embedding == tuple(embedder.encode("hello"))


def test_corpus_add_plan_uses_plan_to_text() -> None:
    embedder = StubEmbedder(dim=8)
    corpus = EmbeddingPlanCorpus(embedder=embedder)
    corpus.add_plan(
        HTNPlan(operators=(_op("send"), _op("log"))),
        reward=0.7,
    )
    expected = tuple(
        embedder.encode(plan_to_text((_op("send"), _op("log"))))
    )
    assert corpus.records()[0].embedding == expected


def test_corpus_len_and_records() -> None:
    embedder = StubEmbedder(dim=8)
    corpus = EmbeddingPlanCorpus(embedder=embedder)
    corpus.add_text(text="a", reward=0.1)
    corpus.add_text(text="b", reward=0.2)
    assert len(corpus) == 2
    assert {r.reward for r in corpus.records()} == {0.1, 0.2}


# ── EmbeddingValueEstimator ─────────────────────────────── #


def test_estimator_constructor_validation() -> None:
    embedder = StubEmbedder(dim=8)
    corpus = EmbeddingPlanCorpus(embedder=embedder)
    with pytest.raises(ValueError, match="min_similarity"):
        EmbeddingValueEstimator(
            corpus=corpus,
            embedder=embedder,
            min_similarity=1.5,
        )
    with pytest.raises(ValueError, match="empty_score"):
        EmbeddingValueEstimator(
            corpus=corpus,
            embedder=embedder,
            empty_score=2.0,
        )


def test_estimator_empty_corpus_returns_empty_score() -> None:
    embedder = StubEmbedder(dim=8)
    estimator = EmbeddingValueEstimator(
        corpus=EmbeddingPlanCorpus(embedder=embedder),
        embedder=embedder,
        empty_score=0.42,
    )
    assert estimator.score(_node("x")) == 0.42


def test_estimator_empty_plan_returns_empty_score() -> None:
    embedder = StubEmbedder(dim=8)
    corpus = EmbeddingPlanCorpus(embedder=embedder)
    corpus.add_text(text="x", reward=0.9)
    estimator = EmbeddingValueEstimator(
        corpus=corpus,
        embedder=embedder,
        empty_score=0.33,
    )
    empty = PlanTreeNode(
        depth=0,
        operators=(),
        status=PlanTreeStatus.EVALUATED,
    )
    assert estimator.score(empty) == 0.33


def test_estimator_identical_plan_returns_record_reward() -> None:
    """Identical plan text → cosine 1.0 → reward of single record."""
    embedder = StubEmbedder(dim=16)
    corpus = EmbeddingPlanCorpus(embedder=embedder)
    corpus.add_plan(
        HTNPlan(operators=(_op("send"), _op("log"))),
        reward=0.77,
    )
    estimator = EmbeddingValueEstimator(
        corpus=corpus,
        embedder=embedder,
        min_similarity=0.99,  # only near-identical contributes
    )
    score = estimator.score(_node("send", "log"))
    assert score == pytest.approx(0.77)


def test_estimator_high_min_similarity_filters_unrelated() -> None:
    """Floor blocks records that fall below the cosine threshold."""
    embedder = StubEmbedder(dim=16)
    corpus = EmbeddingPlanCorpus(embedder=embedder)
    corpus.add_plan(
        HTNPlan(operators=(_op("escalate"),)), reward=0.1,
    )
    estimator = EmbeddingValueEstimator(
        corpus=corpus,
        embedder=embedder,
        min_similarity=0.99,
        empty_score=0.5,
    )
    # Query is unrelated → no record clears 0.99 → empty_score.
    assert estimator.score(_node("send", "log")) == 0.5


def test_estimator_returns_float_in_unit_interval() -> None:
    """Score for any non-empty case lies in ``[0, 1]``."""
    embedder = StubEmbedder(dim=16)
    corpus = EmbeddingPlanCorpus(embedder=embedder)
    corpus.add_text(text="alpha", reward=0.3)
    corpus.add_text(text="beta", reward=0.8)
    estimator = EmbeddingValueEstimator(
        corpus=corpus,
        embedder=embedder,
    )
    score = estimator.score(_node("alpha"))
    assert 0.0 <= score <= 1.0


# ── FastembedAdapter integration ─────────────────────────── #


@pytest.mark.slow
def test_fastembed_adapter_real_model() -> None:
    """Real fastembed encoder produces a stable 384-dim embedding.

    Marked ``slow`` because the first call downloads the
    BAAI/bge-small-en-v1.5 weights (~150 MB) and pays one ONNX
    runtime warm-up.  Skipped by default; opt in via
    ``pytest -m slow``.
    """
    pytest.importorskip("fastembed")
    from brain_engine.htn.embedding_rwom import FastembedAdapter

    embedder = FastembedAdapter()
    assert embedder.dim > 0
    a = embedder.encode("step 1: send_warning")
    b = embedder.encode("step 1: send_warning")
    c = embedder.encode("step 1: escalate")
    # Identical input → cosine 1.0 (within FP epsilon).
    assert cosine_vec(a, b) == pytest.approx(1.0, abs=1e-5)
    # Different inputs → less than identical.
    assert cosine_vec(a, c) < 1.0


@pytest.mark.slow
def test_fastembed_estimator_end_to_end() -> None:
    """Full estimator pipeline with the real fastembed encoder."""
    pytest.importorskip("fastembed")
    from brain_engine.htn.embedding_rwom import FastembedAdapter

    embedder = FastembedAdapter()
    corpus = EmbeddingPlanCorpus(embedder=embedder)
    corpus.add_text(text="step 1: send_warning", reward=0.9)
    corpus.add_text(text="step 1: send_warning", reward=0.95)
    estimator = EmbeddingValueEstimator(
        corpus=corpus,
        embedder=embedder,
        min_similarity=0.99,
    )
    node = _node("send_warning")
    score = estimator.score(node)
    # Identical text in corpus → cosine 1.0 → weighted mean of
    # the two rewards = 0.925.
    assert score == pytest.approx(0.925, abs=1e-3)
