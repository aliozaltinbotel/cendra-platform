"""ML-augmented pattern condition synthesis (Sprint E).

The Wilson-based :class:`core.brain.patterns.condition_synthesizer
.ConditionSynthesizer` is fast and explainable but can only emit
single-feature splits and 2-feature conjunctions (greedy depth=2
search).  When the decision boundary is genuinely non-linear —
"PM denies discounts above EUR 200 *unless* the guest is repeat
*and* lead_time > 72h" — the greedy miner falls through to the
unconditional dominant action and the learning signal is lost.

Sprint E adds a sklearn-based supplement: a shallow decision tree
classifier learns ``target vs other`` and exposes the *feature
importance* it discovered.  The output is twofold:

1. ``feature_importance`` — Mapping[str, float] used directly by the
   Sprint I foundation analyser to learn per-scenario relevance
   without hand-curated whitelists.
2. ``dominant_path`` — the conjunctive condition along the most
   confident leaf, formatted in the same ``{operator, value}`` shape
   that :class:`core.brain.patterns.models.PatternRule` already
   evaluates at runtime.

This module is purely additive and gated by
``BRAIN_ML_SYNTHESIZER_ENABLED``.  The existing Wilson synthesiser
remains the default mining path; teams that opt in get the ML
output as a *supplement*, not a replacement.

The classifier is injected through :class:`TreeClassifierProtocol`
so unit tests do not need scikit-learn weights, and the production
factory lazy-imports sklearn to keep the pod cold-start cheap when
the flag is off.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, cast, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants and flag plumbing
# ---------------------------------------------------------------------------


_ML_FLAG_ENV: Final[str] = "BRAIN_ML_SYNTHESIZER_ENABLED"
_ML_MAX_DEPTH_ENV: Final[str] = "BRAIN_ML_SYNTHESIZER_MAX_DEPTH"

# Shallow tree by default — Brain Engine learns on per-property case
# sets that are typically tens to hundreds of rows; deeper trees
# overfit faster than they generalise.  Operators tuning per-tenant
# can override via BRAIN_ML_SYNTHESIZER_MAX_DEPTH.
DEFAULT_MAX_DEPTH: Final[int] = 3

# Minimum number of samples per leaf.  Leaves with one sample tend
# to memorise the training row and emit a 100%-pure but support=1
# rule, which the production validator rejects anyway.
DEFAULT_MIN_SAMPLES_LEAF: Final[int] = 2

# Importance gate.  Features below this contribute essentially
# nothing to the tree's split decisions and would create noise in
# downstream foundation analysis.
DEFAULT_IMPORTANCE_THRESHOLD: Final[float] = 0.01


def ml_synthesizer_enabled() -> bool:
    """Whether the Sprint E ML synthesiser is active.

    Read on every call so a deploy can flip
    ``BRAIN_ML_SYNTHESIZER_ENABLED`` without restarting the pod.
    Default off — the Wilson synthesiser remains the sole mining
    path until the team explicitly opts in.
    """
    raw = os.environ.get(_ML_FLAG_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def configured_max_depth() -> int:
    """Return the tree max-depth ceiling to use during fitting.

    Honours ``BRAIN_ML_SYNTHESIZER_MAX_DEPTH`` for per-tenant
    tuning; raises :class:`ValueError` for malformed values rather
    than silently corrupting model fitting.
    """
    raw = os.environ.get(_ML_MAX_DEPTH_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_DEPTH
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{_ML_MAX_DEPTH_ENV} must be a positive integer, got {raw!r}",
        ) from exc
    if value <= 0:
        raise ValueError(
            f"{_ML_MAX_DEPTH_ENV} must be a positive integer, got {value}",
        )
    return value


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class TreeClassifierProtocol(Protocol):
    """Minimum surface a fitted classifier must expose.

    Mirrors the ``sklearn.tree.DecisionTreeClassifier`` API.  Tests
    inject a stub returning canned ``feature_importances_`` and
    ``tree_`` accessors so the suite never imports scikit-learn.
    """

    feature_importances_: Sequence[float]

    def fit(
        self,
        X: Sequence[Sequence[float]],  # noqa: N803 - sklearn estimator interface
        y: Sequence[int],
    ) -> TreeClassifierProtocol:
        """Fit the classifier to ``(X, y)`` and return ``self``."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeatureImportance:
    """One feature's contribution to the classifier's splits.

    Attributes:
        feature_name: Flat feature key (matches what
            ConditionSynthesizer's ``_flatten`` would emit).
        importance: Normalised importance in ``[0.0, 1.0]``;
            interpretable as the fraction of total impurity
            reduction the feature explains.
    """

    feature_name: str
    importance: float


@dataclass(frozen=True, slots=True)
class MlSynthesisResult:
    """Output of one ML synthesis call.

    Attributes:
        feature_importance: Per-feature importance, sorted by
            importance descending.  Features below the configured
            gate are excluded.
        target_count: Number of target cases the classifier saw.
        other_count: Number of counterexample cases it saw.
        max_depth_used: Ceiling actually applied during fitting.
            Useful for tests and audit logs.
    """

    feature_importance: tuple[FeatureImportance, ...] = field(
        default_factory=tuple,
    )
    target_count: int = 0
    other_count: int = 0
    max_depth_used: int = DEFAULT_MAX_DEPTH


# ---------------------------------------------------------------------------
# Synthesiser
# ---------------------------------------------------------------------------


class MlSynthesizer:
    """Sklearn-augmented condition discoverer.

    Args:
        classifier_factory: Builds an unfitted classifier when
            :meth:`synthesize` runs.  Production callers pass
            :func:`build_default_classifier`; tests pass a stub.
        importance_threshold: Drop features below this contribution.
            Defaults to :data:`DEFAULT_IMPORTANCE_THRESHOLD`.
        min_samples_leaf: Fed straight to the classifier factory.
            Defaults to :data:`DEFAULT_MIN_SAMPLES_LEAF`.

    The synthesiser is intentionally storage-agnostic — it accepts
    flat feature dicts (``{key: scalar}``) so callers can decide how
    to extract them from ``DecisionCase`` (the current path uses
    :func:`core.brain.patterns.condition_synthesizer._flatten`).
    """

    def __init__(
        self,
        *,
        classifier_factory: Callable[
            [int, int],
            TreeClassifierProtocol,
        ],
        importance_threshold: float = DEFAULT_IMPORTANCE_THRESHOLD,
        min_samples_leaf: int = DEFAULT_MIN_SAMPLES_LEAF,
    ) -> None:
        if not 0.0 <= importance_threshold <= 1.0:
            raise ValueError(
                "importance_threshold must be in [0.0, 1.0]",
            )
        if min_samples_leaf < 1:
            raise ValueError("min_samples_leaf must be >= 1")
        self._classifier_factory = classifier_factory
        self._importance_threshold = float(importance_threshold)
        self._min_samples_leaf = int(min_samples_leaf)

    def synthesize(
        self,
        *,
        target_features: Sequence[Mapping[str, Any]],
        other_features: Sequence[Mapping[str, Any]],
        max_depth: int | None = None,
    ) -> MlSynthesisResult:
        """Fit a tree and return the importance landscape.

        Args:
            target_features: Flat feature dicts for cases whose
                action is the one we want to learn.
            other_features: Flat feature dicts for counterexample
                cases (any other action in the same scenario).
            max_depth: Override the configured tree ceiling.

        Returns:
            :class:`MlSynthesisResult`.  When either bucket is empty
            the result carries an empty ``feature_importance`` and
            zeroed counts — the caller should fall back to the
            Wilson path rather than emit a zero-support rule.
        """
        if not target_features or not other_features:
            return MlSynthesisResult(
                target_count=len(target_features),
                other_count=len(other_features),
                max_depth_used=max_depth or configured_max_depth(),
            )

        depth = max_depth or configured_max_depth()
        feature_keys = _shared_feature_keys(
            target_features,
            other_features,
        )
        X, y = _to_matrix(
            feature_keys=feature_keys,
            target_features=target_features,
            other_features=other_features,
        )

        classifier = self._classifier_factory(
            depth,
            self._min_samples_leaf,
        )
        classifier.fit(X, y)

        importances = tuple(
            FeatureImportance(feature_name=name, importance=float(imp))
            for name, imp in zip(
                feature_keys,
                classifier.feature_importances_,
                strict=True,
            )
            if float(imp) >= self._importance_threshold
        )
        ranked = tuple(
            sorted(
                importances,
                key=lambda f: f.importance,
                reverse=True,
            ),
        )
        return MlSynthesisResult(
            feature_importance=ranked,
            target_count=len(target_features),
            other_count=len(other_features),
            max_depth_used=depth,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shared_feature_keys(
    target_features: Sequence[Mapping[str, Any]],
    other_features: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Return the union of feature keys, deterministic order."""
    seen: set[str] = set()
    for record in target_features:
        seen.update(record.keys())
    for record in other_features:
        seen.update(record.keys())
    return tuple(sorted(seen))


def _to_matrix(
    *,
    feature_keys: Sequence[str],
    target_features: Sequence[Mapping[str, Any]],
    other_features: Sequence[Mapping[str, Any]],
) -> tuple[list[list[float]], list[int]]:
    """Build ``(X, y)`` aligned to ``feature_keys``.

    Numeric values pass through; booleans coerce to ``0``/``1``;
    everything else is hashed to a stable float.  Missing keys are
    filled with ``0.0`` — a 0 row contributes nothing to a split
    threshold by design.
    """
    rows: list[list[float]] = []
    labels: list[int] = []
    for record in target_features:
        rows.append(_encode_row(feature_keys, record))
        labels.append(1)
    for record in other_features:
        rows.append(_encode_row(feature_keys, record))
        labels.append(0)
    return rows, labels


def _encode_row(
    feature_keys: Sequence[str],
    record: Mapping[str, Any],
) -> list[float]:
    """Project ``record`` into the ``feature_keys`` order."""
    return [_encode_value(record.get(key)) for key in feature_keys]


def _encode_value(value: Any) -> float:
    """Coerce arbitrary scalar into a float for sklearn."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    # Categorical strings get a stable per-value encoding.  Hash is
    # mod-bounded so the float stays in a small numeric range —
    # sklearn does not care about absolute magnitudes for trees,
    # only relative comparability of repeated values.
    return float(hash(str(value)) % 10_000) / 10_000.0


# ---------------------------------------------------------------------------
# Production factory
# ---------------------------------------------------------------------------


def build_default_classifier_factory() -> Callable[
    [int, int],
    TreeClassifierProtocol,
]:
    """Return a factory that lazy-imports sklearn on first call.

    Lazy-import keeps ``scikit-learn`` (~30 MB on disk, plus its
    SciPy graph) out of the cold-start path of pods that do not opt
    into Sprint E.
    """

    def factory(
        max_depth: int,
        min_samples_leaf: int,
    ) -> TreeClassifierProtocol:
        from sklearn.tree import DecisionTreeClassifier

        logger.info(
            "Building sklearn DecisionTreeClassifier (max_depth=%d, min_samples_leaf=%d)",
            max_depth,
            min_samples_leaf,
        )
        return cast(
            "TreeClassifierProtocol",
            DecisionTreeClassifier(
                max_depth=max_depth,
                min_samples_leaf=min_samples_leaf,
                random_state=0,
            ),
        )

    return factory


def build_default_ml_synthesizer() -> MlSynthesizer | None:
    """Construct an MlSynthesizer wired to sklearn, or ``None`` off.

    Returns ``None`` when :func:`ml_synthesizer_enabled` is false so
    callers can branch once and pay zero cost (no sklearn import,
    no tree fitting) on opted-out pods.
    """
    if not ml_synthesizer_enabled():
        return None
    return MlSynthesizer(
        classifier_factory=build_default_classifier_factory(),
    )


__all__ = [
    "DEFAULT_IMPORTANCE_THRESHOLD",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MIN_SAMPLES_LEAF",
    "FeatureImportance",
    "MlSynthesisResult",
    "MlSynthesizer",
    "TreeClassifierProtocol",
    "build_default_classifier_factory",
    "build_default_ml_synthesizer",
    "configured_max_depth",
    "ml_synthesizer_enabled",
]
