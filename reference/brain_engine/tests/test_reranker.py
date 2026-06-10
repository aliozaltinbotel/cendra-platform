"""Tests for the Sprint A cross-encoder reranker.

Covers two halves of the contract:

* **Flag plumbing** — ``reranker_enabled`` reads
  ``BRAIN_RERANKER_ENABLED`` correctly and ``build_default_reranker``
  returns ``None`` when the flag is off (no model download is
  attempted, so unit tests never need 568 MB of weights on disk).
* **Reranker behaviour** — sorting, top_n cap, empty input, and the
  scorer-arity invariant.

The cross-encoder is stubbed through ``CrossEncoderProtocol`` so the
suite exercises real :class:`CrossEncoderReranker` semantics without
loading ``BAAI/bge-reranker-v2-m3``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from brain_engine.memory.reranker import (
    DEFAULT_RERANKER_MODEL,
    CrossEncoderProtocol,
    CrossEncoderReranker,
    RerankedItem,
    build_default_reranker,
    configured_model_name,
    reranker_enabled,
)


# ---------------------------------------------------------------------------
# Stubs and fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Doc:
    """Tiny candidate type used in the reranker tests."""

    doc_id: str
    body: str


class _StubScorer:
    """Returns a pre-baked list of scores in the order received."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = list(scores)
        self.calls: list[list[tuple[str, str]]] = []

    def predict(
        self,
        sentences: list[tuple[str, str]],
    ) -> list[float]:
        self.calls.append(list(sentences))
        return list(self._scores)


@pytest.fixture(autouse=True)
def _reset_reranker_env() -> Iterator[None]:
    """Strip Sprint A env vars before each test to avoid leakage.

    The fixture also clears whatever the test sets so adjacent files
    in the same pytest run cannot inherit a half-configured reranker.
    """
    snapshot = {
        key: os.environ.pop(key, None)
        for key in ("BRAIN_RERANKER_ENABLED", "BRAIN_RERANKER_MODEL")
    }
    try:
        yield
    finally:
        for key in ("BRAIN_RERANKER_ENABLED", "BRAIN_RERANKER_MODEL"):
            os.environ.pop(key, None)
        for key, value in snapshot.items():
            if value is not None:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------


def test_flag_off_by_default() -> None:
    assert reranker_enabled() is False


@pytest.mark.parametrize(
    "raw", ["1", "true", "TRUE", "yes", "on", " 1 "],
)
def test_flag_truthy_values(raw: str) -> None:
    os.environ["BRAIN_RERANKER_ENABLED"] = raw
    assert reranker_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "garbage"])
def test_flag_falsy_values(raw: str) -> None:
    os.environ["BRAIN_RERANKER_ENABLED"] = raw
    assert reranker_enabled() is False


def test_default_model_when_env_unset() -> None:
    assert configured_model_name() == DEFAULT_RERANKER_MODEL


def test_model_env_override() -> None:
    os.environ["BRAIN_RERANKER_MODEL"] = "vendor/some-other-reranker"
    assert configured_model_name() == "vendor/some-other-reranker"


def test_build_default_reranker_returns_none_when_off() -> None:
    """No download / no GPU memory when the team has not opted in."""
    assert build_default_reranker() is None


# ---------------------------------------------------------------------------
# Reranker semantics
# ---------------------------------------------------------------------------


def test_protocol_runtime_check_recognises_stub() -> None:
    """``CrossEncoderProtocol`` is runtime_checkable for DI safety."""
    assert isinstance(_StubScorer([0.0]), CrossEncoderProtocol)


def test_rerank_sorts_descending_by_score() -> None:
    scorer = _StubScorer([0.10, 0.99, 0.55])
    reranker: CrossEncoderReranker[_Doc] = CrossEncoderReranker(
        scorer=scorer,
    )
    candidates = [
        _Doc(doc_id="a", body="alpha text"),
        _Doc(doc_id="b", body="beta text"),
        _Doc(doc_id="c", body="gamma text"),
    ]
    out = reranker.rerank(
        query="q",
        candidates=candidates,
        text_of=lambda d: d.body,
    )
    assert [r.item.doc_id for r in out] == ["b", "c", "a"]
    assert [r.score for r in out] == [0.99, 0.55, 0.10]
    assert all(isinstance(r, RerankedItem) for r in out)


def test_rerank_top_n_truncates() -> None:
    scorer = _StubScorer([0.1, 0.9, 0.5, 0.7])
    reranker: CrossEncoderReranker[_Doc] = CrossEncoderReranker(
        scorer=scorer,
    )
    candidates = [
        _Doc(doc_id=str(i), body=f"text {i}") for i in range(4)
    ]
    out = reranker.rerank(
        query="q",
        candidates=candidates,
        text_of=lambda d: d.body,
        top_n=2,
    )
    assert len(out) == 2
    assert [r.item.doc_id for r in out] == ["1", "3"]


def test_rerank_empty_skips_scorer() -> None:
    """Empty candidate list returns empty without calling the scorer."""
    scorer = _StubScorer([])
    reranker: CrossEncoderReranker[_Doc] = CrossEncoderReranker(
        scorer=scorer,
    )
    out = reranker.rerank(
        query="q",
        candidates=[],
        text_of=lambda d: d.body,
    )
    assert out == []
    assert scorer.calls == []


def test_rerank_passes_query_and_text_to_scorer() -> None:
    scorer = _StubScorer([0.3, 0.7])
    reranker: CrossEncoderReranker[_Doc] = CrossEncoderReranker(
        scorer=scorer,
    )
    candidates = [
        _Doc(doc_id="a", body="first body"),
        _Doc(doc_id="b", body="second body"),
    ]
    reranker.rerank(
        query="why?",
        candidates=candidates,
        text_of=lambda d: d.body,
    )
    assert scorer.calls == [
        [("why?", "first body"), ("why?", "second body")]
    ]


def test_rerank_raises_when_scorer_arity_mismatches() -> None:
    """Shape mismatch is a programming bug, not a runtime fallback."""
    scorer = _StubScorer([0.1])  # only one score for two candidates
    reranker: CrossEncoderReranker[_Doc] = CrossEncoderReranker(
        scorer=scorer,
    )
    with pytest.raises(RuntimeError, match="scorer returned"):
        reranker.rerank(
            query="q",
            candidates=[
                _Doc(doc_id="a", body="x"),
                _Doc(doc_id="b", body="y"),
            ],
            text_of=lambda d: d.body,
        )
