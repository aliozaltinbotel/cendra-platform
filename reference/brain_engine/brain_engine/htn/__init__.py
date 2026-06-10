"""HTN + LATS + R-WoM hybrid planner (Moat #12).

Brain Engine's macro-action planner.  Combines:

1. *HTN decomposition* (Erol/Hendler/Nau 1994; SHOP2 2003) — the
   recursive descent in :mod:`brain_engine.htn.planner` that turns
   a high-level :class:`Task` into an ordered tuple of grounded
   :class:`Operator` records.
2. *LATS-style partial-plan tree* (Zhou et al. arXiv:2310.04406)
   — the data model in :mod:`brain_engine.htn.tree` plus the
   UCB1 MCTS expansion in :mod:`brain_engine.htn.search` (M15).
3. *R-WoM-style value estimator* (arXiv:2510.11892) — the
   :class:`ValueEstimator` Protocol with two concrete
   implementations: bag-of-operator-name cosine in
   :mod:`brain_engine.htn.rwom` (M16, count-cosine) and
   embedding cosine in :mod:`brain_engine.htn.embedding_rwom`
   (M21, semantic).

Per GR00T P2 (decoupled WBC), every :class:`Operator` carries a
:class:`PreferredSolver` tag (LLM / Utility / SMT /
Deterministic / HITL).  The runtime middleware routes each
operator through the matching handler instead of forcing one
method on every move.

Public surface:

- :class:`Task` / :class:`Operator` / :class:`Method` /
  :class:`TaskNetwork` / :class:`HTNPlan` / :class:`PreferredSolver`.
- :class:`HTNPlanner` — recursive decomposer.
- :class:`HTNPlanFailure` — raised when no decomposition fits.
- :class:`PlanTreeNode` / :class:`PlanTreeStatus` /
  :class:`ValueEstimator` Protocol +
  :func:`tree_from_plan`.
- :class:`LATSSearch` / :class:`SearchStatistics` /
  :func:`default_reward` — UCB1 MCTS over method choices (M15).
- :class:`PlanCorpus` / :class:`PlanRecord` /
  :class:`RetrievalValueEstimator` /
  :func:`feature_bag` / :func:`cosine_similarity` —
  bag-of-name R-WoM (M16).
- :class:`EmbedderProtocol` / :class:`StubEmbedder` /
  :class:`FastembedAdapter` / :class:`EmbeddedPlanRecord` /
  :class:`EmbeddingPlanCorpus` /
  :class:`EmbeddingValueEstimator` / :func:`plan_to_text` /
  :func:`cosine_vec` — semantic-embedding R-WoM (M21).

Defensibility (Moat #12): integrated planning architecture —
HTN-as-macro-actions + LATS-MCTS expansion + R-WoM evaluation —
none of the surveyed frontier systems combines all three in one
runtime (latest_research §3.7).  USPTO Examples-47-49-fit
independent claim.
"""

from __future__ import annotations

from brain_engine.htn.ann_corpus import (
    ANNEmbeddingPlanCorpus,
    ANNEmbeddingValueEstimator,
    DEFAULT_ANN_TOP_K,
)
from brain_engine.htn.embedding_rwom import (
    DEFAULT_EMBEDDER_DIM,
    DEFAULT_EMBEDDING_EMPTY_SCORE,
    DEFAULT_EMBEDDING_MIN_SIMILARITY,
    EmbeddedPlanRecord,
    EmbedderProtocol,
    EmbeddingPlanCorpus,
    EmbeddingValueEstimator,
    FastembedAdapter,
    StubEmbedder,
    cosine_vec,
    plan_to_text,
)
from brain_engine.htn.models import (
    DEFAULT_PRECONDITION,
    HTNPlan,
    Method,
    Operator,
    PreferredSolver,
    Task,
    TaskNetwork,
)
from brain_engine.htn.planner import (
    HTNPlanFailure,
    HTNPlanner,
)
from brain_engine.htn.rwom import (
    DEFAULT_EMPTY_SCORE,
    DEFAULT_MIN_SIMILARITY,
    PlanCorpus,
    PlanRecord,
    RetrievalValueEstimator,
    cosine_similarity,
    feature_bag,
)
from brain_engine.htn.search import (
    DEFAULT_EXPLORATION,
    DEFAULT_ITERATIONS,
    DEFAULT_MAX_DEPTH,
    LATSSearch,
    SearchStatistics,
    default_reward,
)
from brain_engine.htn.tree import (
    PlanTreeNode,
    PlanTreeStatus,
    ValueEstimator,
    tree_from_plan,
)


__all__ = [
    "ANNEmbeddingPlanCorpus",
    "ANNEmbeddingValueEstimator",
    "DEFAULT_ANN_TOP_K",
    "DEFAULT_EMBEDDER_DIM",
    "DEFAULT_EMBEDDING_EMPTY_SCORE",
    "DEFAULT_EMBEDDING_MIN_SIMILARITY",
    "DEFAULT_EMPTY_SCORE",
    "DEFAULT_EXPLORATION",
    "DEFAULT_ITERATIONS",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MIN_SIMILARITY",
    "DEFAULT_PRECONDITION",
    "EmbeddedPlanRecord",
    "EmbedderProtocol",
    "EmbeddingPlanCorpus",
    "EmbeddingValueEstimator",
    "FastembedAdapter",
    "HTNPlan",
    "HTNPlanFailure",
    "HTNPlanner",
    "LATSSearch",
    "Method",
    "Operator",
    "PlanCorpus",
    "PlanRecord",
    "PlanTreeNode",
    "PlanTreeStatus",
    "PreferredSolver",
    "RetrievalValueEstimator",
    "SearchStatistics",
    "StubEmbedder",
    "Task",
    "TaskNetwork",
    "ValueEstimator",
    "cosine_similarity",
    "cosine_vec",
    "default_reward",
    "feature_bag",
    "plan_to_text",
    "tree_from_plan",
]
