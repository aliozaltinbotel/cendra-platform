"""Tests for the Sprint E ML-augmented pattern miner.

Three groups of guarantees:

* **Flag plumbing** — ``ml_synthesizer_enabled`` and
  ``configured_max_depth`` honour the documented env vars and reject
  malformed values rather than silently corrupting tree fitting.
* **Synthesizer behaviour** — empty buckets short-circuit, valid
  inputs build aligned ``(X, y)``, importances below the gate are
  dropped, and the result is sorted by importance descending.
* **Value encoding** — bool/int/float/None coerce predictably so the
  stub classifier sees stable rows in unit tests.

The classifier is stubbed through :class:`TreeClassifierProtocol` so
the suite exercises real :class:`MlSynthesizer` semantics without
loading scikit-learn weights.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from typing import Any

import pytest

from brain_engine.patterns.ml_synthesizer import (
    DEFAULT_IMPORTANCE_THRESHOLD,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MIN_SAMPLES_LEAF,
    FeatureImportance,
    MlSynthesisResult,
    MlSynthesizer,
    TreeClassifierProtocol,
    _encode_value,
    build_default_ml_synthesizer,
    configured_max_depth,
    ml_synthesizer_enabled,
)


# ---------------------------------------------------------------------------
# Stubs and fixtures
# ---------------------------------------------------------------------------


class _StubClassifier:
    """Returns canned ``feature_importances_`` and records ``fit``."""

    def __init__(self, importances: Sequence[float]) -> None:
        self.feature_importances_ = list(importances)
        self.last_fit: tuple[
            Sequence[Sequence[float]], Sequence[int],
        ] | None = None

    def fit(
        self,
        X: Sequence[Sequence[float]],
        y: Sequence[int],
    ) -> "_StubClassifier":
        self.last_fit = (list(X), list(y))
        return self


def _stub_factory(
    importances: Sequence[float],
) -> Any:
    """Factory closure capturing the canned importances."""

    def factory(
        max_depth: int,  # noqa: ARG001
        min_samples_leaf: int,  # noqa: ARG001
    ) -> _StubClassifier:
        return _StubClassifier(importances)

    return factory


@pytest.fixture(autouse=True)
def _reset_ml_env() -> Iterator[None]:
    """Strip Sprint E env vars before each test to avoid leakage."""
    snapshot = {
        key: os.environ.pop(key, None)
        for key in (
            "BRAIN_ML_SYNTHESIZER_ENABLED",
            "BRAIN_ML_SYNTHESIZER_MAX_DEPTH",
        )
    }
    try:
        yield
    finally:
        for key in (
            "BRAIN_ML_SYNTHESIZER_ENABLED",
            "BRAIN_ML_SYNTHESIZER_MAX_DEPTH",
        ):
            os.environ.pop(key, None)
        for key, value in snapshot.items():
            if value is not None:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------


def test_flag_off_by_default() -> None:
    assert ml_synthesizer_enabled() is False


@pytest.mark.parametrize(
    "raw", ["1", "true", "TRUE", "yes", "on", " 1 "],
)
def test_flag_truthy_values(raw: str) -> None:
    os.environ["BRAIN_ML_SYNTHESIZER_ENABLED"] = raw
    assert ml_synthesizer_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "garbage"])
def test_flag_falsy_values(raw: str) -> None:
    os.environ["BRAIN_ML_SYNTHESIZER_ENABLED"] = raw
    assert ml_synthesizer_enabled() is False


def test_default_max_depth_when_env_unset() -> None:
    assert configured_max_depth() == DEFAULT_MAX_DEPTH


def test_max_depth_env_override() -> None:
    os.environ["BRAIN_ML_SYNTHESIZER_MAX_DEPTH"] = "5"
    assert configured_max_depth() == 5


@pytest.mark.parametrize("raw", ["abc", "1.5"])
def test_malformed_max_depth_raises(raw: str) -> None:
    os.environ["BRAIN_ML_SYNTHESIZER_MAX_DEPTH"] = raw
    with pytest.raises(ValueError, match="positive integer"):
        configured_max_depth()


@pytest.mark.parametrize("raw", ["0", "-3"])
def test_non_positive_max_depth_raises(raw: str) -> None:
    os.environ["BRAIN_ML_SYNTHESIZER_MAX_DEPTH"] = raw
    with pytest.raises(ValueError, match="positive integer"):
        configured_max_depth()


def test_build_default_returns_none_when_off() -> None:
    assert build_default_ml_synthesizer() is None


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_constructor_rejects_negative_threshold() -> None:
    with pytest.raises(ValueError, match=r"\[0.0, 1.0\]"):
        MlSynthesizer(
            classifier_factory=_stub_factory([1.0]),
            importance_threshold=-0.1,
        )


def test_constructor_rejects_threshold_above_one() -> None:
    with pytest.raises(ValueError, match=r"\[0.0, 1.0\]"):
        MlSynthesizer(
            classifier_factory=_stub_factory([1.0]),
            importance_threshold=1.5,
        )


def test_constructor_rejects_zero_min_samples_leaf() -> None:
    with pytest.raises(ValueError, match="min_samples_leaf"):
        MlSynthesizer(
            classifier_factory=_stub_factory([1.0]),
            min_samples_leaf=0,
        )


# ---------------------------------------------------------------------------
# Synthesis behaviour
# ---------------------------------------------------------------------------


def test_synthesize_empty_target_short_circuits() -> None:
    ml = MlSynthesizer(classifier_factory=_stub_factory([1.0]))
    result = ml.synthesize(
        target_features=[],
        other_features=[{"a": 1}],
    )
    assert result == MlSynthesisResult(
        feature_importance=(),
        target_count=0,
        other_count=1,
        max_depth_used=DEFAULT_MAX_DEPTH,
    )


def test_synthesize_empty_other_short_circuits() -> None:
    ml = MlSynthesizer(classifier_factory=_stub_factory([1.0]))
    result = ml.synthesize(
        target_features=[{"a": 1}],
        other_features=[],
    )
    assert result.target_count == 1
    assert result.other_count == 0
    assert result.feature_importance == ()


def test_synthesize_returns_importances_above_gate_sorted() -> None:
    """Features below the gate are dropped; output is sorted desc."""
    ml = MlSynthesizer(
        classifier_factory=_stub_factory([0.0, 0.5, 0.001, 0.3]),
        importance_threshold=0.01,
    )
    result = ml.synthesize(
        target_features=[
            {"a": 1, "b": 2, "c": 3, "d": 4},
            {"a": 1, "b": 2, "c": 3, "d": 4},
        ],
        other_features=[
            {"a": 5, "b": 6, "c": 7, "d": 8},
        ],
    )
    names = [f.feature_name for f in result.feature_importance]
    assert names == ["b", "d"]  # 0.5 > 0.3; "a" and "c" below gate
    assert all(
        isinstance(f, FeatureImportance)
        for f in result.feature_importance
    )


def test_synthesize_records_max_depth_used() -> None:
    ml = MlSynthesizer(classifier_factory=_stub_factory([1.0]))
    result = ml.synthesize(
        target_features=[{"a": 1}],
        other_features=[{"a": 2}],
        max_depth=7,
    )
    assert result.max_depth_used == 7


def test_synthesize_passes_aligned_xy_to_classifier() -> None:
    """Classifier sees ``X`` aligned to sorted feature keys, ``y`` 1/0."""
    classifier_holder: list[_StubClassifier] = []

    def factory(
        max_depth: int,  # noqa: ARG001
        min_samples_leaf: int,  # noqa: ARG001
    ) -> _StubClassifier:
        clf = _StubClassifier([0.5, 0.5])
        classifier_holder.append(clf)
        return clf

    ml = MlSynthesizer(classifier_factory=factory)
    ml.synthesize(
        target_features=[{"a": 1, "b": 2}],
        other_features=[{"a": 3, "b": 4}],
    )

    clf = classifier_holder[0]
    assert clf.last_fit is not None
    X, y = clf.last_fit
    # Feature keys are sorted: ["a", "b"].
    assert X == [[1.0, 2.0], [3.0, 4.0]]
    assert y == [1, 0]


def test_synthesize_handles_missing_keys_with_zero() -> None:
    """Records missing a feature contribute 0.0 to that column."""
    classifier_holder: list[_StubClassifier] = []

    def factory(
        max_depth: int,  # noqa: ARG001
        min_samples_leaf: int,  # noqa: ARG001
    ) -> _StubClassifier:
        clf = _StubClassifier([0.5, 0.5])
        classifier_holder.append(clf)
        return clf

    ml = MlSynthesizer(classifier_factory=factory)
    ml.synthesize(
        target_features=[{"a": 1}],         # b missing -> 0
        other_features=[{"b": 4}],          # a missing -> 0
    )

    X, _ = classifier_holder[0].last_fit  # type: ignore[misc]
    assert X == [[1.0, 0.0], [0.0, 4.0]]


# ---------------------------------------------------------------------------
# Value encoding
# ---------------------------------------------------------------------------


def test_encode_none_is_zero() -> None:
    assert _encode_value(None) == 0.0


def test_encode_bool_true_is_one() -> None:
    assert _encode_value(True) == 1.0


def test_encode_bool_false_is_zero() -> None:
    assert _encode_value(False) == 0.0


def test_encode_int_passes_through() -> None:
    assert _encode_value(42) == 42.0


def test_encode_float_passes_through() -> None:
    assert _encode_value(3.14) == pytest.approx(3.14)


def test_encode_string_is_stable_across_calls() -> None:
    """Repeated values must map to the same float for the tree."""
    assert _encode_value("bookingcom") == _encode_value("bookingcom")
    assert _encode_value("airbnb") == _encode_value("airbnb")
    assert _encode_value("bookingcom") != _encode_value("airbnb")


# ---------------------------------------------------------------------------
# Module structure
# ---------------------------------------------------------------------------


def test_classifier_protocol_recognises_stub() -> None:
    assert isinstance(_StubClassifier([1.0]), TreeClassifierProtocol)


def test_default_constants_anchor() -> None:
    """Anchor: the documented defaults stay at sensible Sprint E values."""
    assert DEFAULT_MAX_DEPTH == 3
    assert DEFAULT_MIN_SAMPLES_LEAF == 2
    assert DEFAULT_IMPORTANCE_THRESHOLD == 0.01
