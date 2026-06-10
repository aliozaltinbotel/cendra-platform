"""Decision cards — five-slot V2 UI artefact.

Public surface:

- :class:`DecisionCard` — frozen value object with five slots.
- :class:`ReasoningRow` / :class:`EvidenceKind` — evidence entries.
- :class:`PreparedAction` / :class:`ReversibilityTier` — action
  descriptor + undo tier.
- :class:`DecisionCardBuilder` — stateless composer.
"""

from __future__ import annotations

from brain_engine.cards.action_kinds import (
    ACTION_KIND_DESCRIPTIONS,
    ACTION_KIND_REVERSIBILITY,
    CardActionKind,
    default_reversibility,
    describe_action_kind,
)
from brain_engine.cards.builder import DecisionCardBuilder
from brain_engine.cards.context_tags import (
    CONTEXT_TAG_DESCRIPTIONS,
    ContextTag,
    describe_context_tag,
)
from brain_engine.cards.models import (
    DecisionCard,
    EvidenceKind,
    PreparedAction,
    ReasoningRow,
    ReversibilityTier,
)
from brain_engine.cards.postgres_store import (
    PgCardStore,
    create_cards_pool,
)
from brain_engine.cards.store import (
    CardNotFoundError,
    CardStatus,
    CardStore,
    InMemoryCardStore,
    StoredCard,
)

__all__ = [
    "ACTION_KIND_DESCRIPTIONS",
    "ACTION_KIND_REVERSIBILITY",
    "CONTEXT_TAG_DESCRIPTIONS",
    "CardActionKind",
    "CardNotFoundError",
    "CardStatus",
    "CardStore",
    "ContextTag",
    "DecisionCard",
    "DecisionCardBuilder",
    "EvidenceKind",
    "InMemoryCardStore",
    "PgCardStore",
    "PreparedAction",
    "ReasoningRow",
    "ReversibilityTier",
    "StoredCard",
    "create_cards_pool",
    "default_reversibility",
    "describe_action_kind",
    "describe_context_tag",
]
