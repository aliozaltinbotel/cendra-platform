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
- :class:`Critic` / :class:`ReflexionCritic` — verbal-RL critique.
- :class:`FrictionTracker` / :class:`FrictionRewardSimulator` —
  MAR friction memory + reward shaping.

Batch 1 port note: ``policy``, ``trainer``, ``grpo`` and ``sleep``
(incl. :class:`ConsolidationReport` / :func:`summarise_decisions`)
stay in the reference until Batch 6 — see PORTING_MAP.md.

Defensibility (Moat #14, Cand. 6): patent claim is on the
*protocol* — who triggers, how conflicts resolve when ACE Curator
proposes ADD vs Memory-R1 votes DELETE, how nightly distillation
merges results.  USPTO Examples-47-49-fit independent claim.
"""

from __future__ import annotations

from core.brain.cognition.critic import (
    DEFAULT_DISSATISFACTION_SCALE,
    DEFAULT_REFLECTION_TOP_FEATURES,
    Critic,
    CriticEvent,
    CritiqueReport,
    ReflexionCritic,
)
from core.brain.cognition.friction import (
    DEFAULT_FRICTION_ALPHA,
    DEFAULT_FRICTION_EMA_DECAY,
    FrictionRewardSimulator,
    FrictionState,
    FrictionTracker,
    RewardSimulator,
    canonical_state_key,
)
from core.brain.cognition.models import (
    AceCycle,
    AceVerdict,
    MemoryOp,
    MemoryOpKind,
    ResolvedDecision,
)
from core.brain.cognition.protocol import InteractionProtocol

__all__ = [
    "DEFAULT_DISSATISFACTION_SCALE",
    "DEFAULT_FRICTION_ALPHA",
    "DEFAULT_FRICTION_EMA_DECAY",
    "DEFAULT_REFLECTION_TOP_FEATURES",
    "AceCycle",
    "AceVerdict",
    "Critic",
    "CriticEvent",
    "CritiqueReport",
    "FrictionRewardSimulator",
    "FrictionState",
    "FrictionTracker",
    "InteractionProtocol",
    "MemoryOp",
    "MemoryOpKind",
    "ReflexionCritic",
    "ResolvedDecision",
    "RewardSimulator",
    "canonical_state_key",
]
