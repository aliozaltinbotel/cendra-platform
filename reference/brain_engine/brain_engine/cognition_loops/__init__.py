"""ACE + sleep + Memory-R1 cognition loops (Moat #14, Cand. 6).

Three replay-loop primitives Brain Engine combines into one
runtime where no published frontier system has done it:

- *Online ACE* (Generator → Reflector → Curator) — Zhang et al.
  arXiv:2510.04618 — fires on every action.
- *Per-step Memory-R1 RL policy* — Yan et al. arXiv:2508.19828 —
  votes ADD / UPDATE / DELETE / NOOP / SUMMARIZE / RETRIEVE on
  the ACE Curator's intended write.
- *Nightly sleep-time consolidation* — Letta + UCB
  arXiv:2504.13171 + Anthropic Auto Dream — distils the day's
  outcomes into a playbook delta.

Each loop has prior art in isolation; their *interaction protocol*
is the patent-defensible novelty (latest_research §3.6).  v0.1
ships the conflict-resolution rules + the sleep summary surface;
v1.0 plugs the actual GRPO trainer + Anthropic Auto Dream
integration.

Public surface:

- :class:`AceVerdict` / :class:`AceCycle` — Generator → Reflector
  → Curator outcome value object.
- :class:`MemoryOpKind` / :class:`MemoryOp` — six Memory-R1 op
  classes.
- :class:`ResolvedDecision` — output of one conflict resolution.
- :class:`InteractionProtocol` — the state machine.
- :class:`ConsolidationReport` + :func:`summarise_decisions` —
  nightly summary surface.

Defensibility (Moat #14, Cand. 6): patent claim is on the
*protocol* — who triggers, how conflicts resolve when ACE Curator
proposes ADD vs Memory-R1 votes DELETE, how nightly distillation
merges results.  USPTO Examples-47-49-fit independent claim.
"""

from __future__ import annotations

from brain_engine.cognition_loops.models import (
    AceCycle,
    AceVerdict,
    MemoryOp,
    MemoryOpKind,
    ResolvedDecision,
)
from brain_engine.cognition_loops.critic import (
    Critic,
    CriticEvent,
    CritiqueReport,
    DEFAULT_DISSATISFACTION_SCALE,
    DEFAULT_REFLECTION_TOP_FEATURES,
    ReflexionCritic,
)
from brain_engine.cognition_loops.friction import (
    DEFAULT_FRICTION_ALPHA,
    DEFAULT_FRICTION_EMA_DECAY,
    FrictionRewardSimulator,
    FrictionState,
    FrictionTracker,
    canonical_state_key,
)
from brain_engine.cognition_loops.grpo import (
    DEFAULT_GRPO_LEARNING_RATE,
    GRPOMetrics,
    GRPOTrainer,
    LookupRewardSimulator,
    RewardSimulator,
)
from brain_engine.cognition_loops.policy import (
    DEFAULT_L2_LAMBDA,
    DEFAULT_LEARNING_RATE,
    LogitWeights,
    MultinomialLogitPolicy,
    softmax,
)
from brain_engine.cognition_loops.protocol import (
    InteractionProtocol,
)
from brain_engine.cognition_loops.sleep import (
    MIN_DECISIONS_FOR_PLAYBOOK_BUMP,
    ConsolidationReport,
    summarise_decisions,
)
from brain_engine.cognition_loops.trainer import (
    SGDTrainer,
    TrainingMetrics,
    TrainingSample,
    iter_samples,
)


__all__ = [
    "AceCycle",
    "AceVerdict",
    "ConsolidationReport",
    "Critic",
    "CriticEvent",
    "CritiqueReport",
    "DEFAULT_DISSATISFACTION_SCALE",
    "DEFAULT_FRICTION_ALPHA",
    "DEFAULT_FRICTION_EMA_DECAY",
    "DEFAULT_GRPO_LEARNING_RATE",
    "DEFAULT_L2_LAMBDA",
    "DEFAULT_LEARNING_RATE",
    "DEFAULT_REFLECTION_TOP_FEATURES",
    "FrictionRewardSimulator",
    "FrictionState",
    "FrictionTracker",
    "GRPOMetrics",
    "GRPOTrainer",
    "InteractionProtocol",
    "LogitWeights",
    "LookupRewardSimulator",
    "MIN_DECISIONS_FOR_PLAYBOOK_BUMP",
    "MemoryOp",
    "MemoryOpKind",
    "MultinomialLogitPolicy",
    "ReflexionCritic",
    "ResolvedDecision",
    "RewardSimulator",
    "SGDTrainer",
    "TrainingMetrics",
    "TrainingSample",
    "canonical_state_key",
    "iter_samples",
    "softmax",
    "summarise_decisions",
]
