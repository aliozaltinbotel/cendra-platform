"""Retrieval-augmented World Model value estimator (R-WoM).

Closes the second deferred TODO from M12 PR #218 ("R-WoM
retrieval scoring (v1.0)").  Brain Engine's M12 ``tree.py``
shipped the :class:`ValueEstimator` Protocol; this module
provides a concrete implementation that scores a partial plan by
*retrieving similar past plans* from a :class:`PlanCorpus` and
returning a similarity-weighted average of their realised
rewards.

Scoring pipeline:

1. :func:`feature_bag` extracts a per-operator-name count
   :class:`Counter` from an ordered tuple of operators.
2. :func:`cosine_similarity` returns the angle-cosine between two
   bags (range ``[0.0, 1.0]``).
3. :class:`PlanCorpus` keeps a list of :class:`PlanRecord` rows
   (operators tuple + realised reward).
4. :class:`RetrievalValueEstimator.score(node)` returns the
   similarity-weighted mean reward over the corpus, falling back
   to a configurable ``empty_score`` when no record matches.

Honest scope:
  * Pure-Python, no ML / NumPy.  Linear scan over the corpus.
  * Bag-of-operator-names similarity — adequate when the
    corpus has on the order of hundreds of records.
  * v1.0 swap-in: embedding similarity over a pretrained
    encoder, served from an ANN store.  The Protocol stays
    unchanged.

Reference:
    arXiv:2510.11892 — *Retrieval-augmented World Models* (R-WoM).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import structlog

from brain_engine.htn.models import HTNPlan, Operator
from brain_engine.htn.tree import PlanTreeNode


__all__ = [
    "DEFAULT_EMPTY_SCORE",
    "DEFAULT_MIN_SIMILARITY",
    "PlanCorpus",
    "PlanRecord",
    "RetrievalValueEstimator",
    "cosine_similarity",
    "feature_bag",
]


DEFAULT_EMPTY_SCORE: Final[float] = 0.0
DEFAULT_MIN_SIMILARITY: Final[float] = 0.0


logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """One past (plan, realised reward) pair the scorer consults.

    Attributes:
        operators: Ordered tuple of operator names the plan
            executed (we keep names rather than the full
            :class:`Operator` to make corpora portable across
            tenants without leaking custom solver tags).
        reward: Realised reward in ``[0.0, 1.0]`` — the same
            range :class:`brain_engine.htn.search.default_reward`
            produces.  Out-of-range values raise.
        bag: Lazily-cached operator-name :class:`Counter`.  The
            constructor populates it once so repeated similarity
            calls do not rebuild the bag.
    """

    operators: tuple[str, ...]
    reward: float
    bag: Counter = field(init=False, compare=False)

    def __post_init__(self) -> None:
        if not 0.0 <= self.reward <= 1.0:
            raise ValueError(
                "reward must be in [0.0, 1.0]"
            )
        # frozen dataclass: bypass __setattr__ via object.__setattr__
        object.__setattr__(
            self,
            "bag",
            Counter(self.operators),
        )

    @classmethod
    def from_plan(
        cls,
        plan: HTNPlan,
        *,
        reward: float,
    ) -> PlanRecord:
        """Build a :class:`PlanRecord` from a finished :class:`HTNPlan`."""
        return cls(
            operators=tuple(op.name for op in plan.operators),
            reward=reward,
        )


def feature_bag(operators: Sequence[Operator | str]) -> Counter:
    """Return a per-name :class:`Counter` over the operator sequence.

    Accepts either :class:`Operator` instances or raw name strings
    so callers can score either a planned or an ongoing rollout.
    """
    names: list[str] = []
    for item in operators:
        if isinstance(item, Operator):
            names.append(item.name)
            continue
        if not isinstance(item, str):
            raise TypeError(
                f"unsupported operator entry: {type(item).__name__}"
            )
        names.append(item)
    return Counter(names)


def cosine_similarity(a: Mapping[str, int], b: Mapping[str, int]) -> float:
    """Return cosine similarity between two count mappings.

    Both mappings are treated as sparse vectors over the union of
    keys.  Returns ``0.0`` when either side is empty.
    """
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for key in keys:
        va = float(a.get(key, 0))
        vb = float(b.get(key, 0))
        dot += va * vb
        norm_a += va * va
        norm_b += vb * vb
    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denom == 0.0:
        return 0.0
    return dot / denom


class PlanCorpus:
    """In-memory store of :class:`PlanRecord` rows.

    Multi-tenant deployments should construct one corpus per
    tenant — the records embed realised reward signals that may
    leak strategy across owners.
    """

    def __init__(
        self,
        records: Iterable[PlanRecord] = (),
    ) -> None:
        self._records: list[PlanRecord] = list(records)

    def add(self, record: PlanRecord) -> None:
        """Append a single record."""
        self._records.append(record)

    def add_plan(
        self,
        plan: HTNPlan,
        *,
        reward: float,
    ) -> None:
        """Record an :class:`HTNPlan` paired with its realised reward."""
        self._records.append(
            PlanRecord.from_plan(plan, reward=reward),
        )

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> tuple[PlanRecord, ...]:
        """Return every stored record (snapshot)."""
        return tuple(self._records)


class RetrievalValueEstimator:
    """Concrete :class:`ValueEstimator` over a :class:`PlanCorpus`."""

    def __init__(
        self,
        *,
        corpus: PlanCorpus,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        empty_score: float = DEFAULT_EMPTY_SCORE,
    ) -> None:
        if not 0.0 <= min_similarity <= 1.0:
            raise ValueError(
                "min_similarity must be in [0.0, 1.0]"
            )
        if not 0.0 <= empty_score <= 1.0:
            raise ValueError(
                "empty_score must be in [0.0, 1.0]"
            )
        self._corpus = corpus
        self._min_similarity = min_similarity
        self._empty_score = empty_score
        self._log = logger.bind(component="rwom_estimator")

    def score(self, node: PlanTreeNode) -> float:
        """Return the similarity-weighted mean reward for ``node``.

        When the corpus is empty or no record clears the
        similarity floor, returns ``empty_score``.
        """
        records = self._corpus.records()
        if not records:
            return self._empty_score
        query_bag = feature_bag(node.operators)
        if not query_bag:
            return self._empty_score
        weighted_sum = 0.0
        weight_total = 0.0
        for record in records:
            sim = cosine_similarity(query_bag, record.bag)
            if sim < self._min_similarity:
                continue
            weighted_sum += sim * record.reward
            weight_total += sim
        if weight_total <= 0.0:
            return self._empty_score
        return weighted_sum / weight_total
