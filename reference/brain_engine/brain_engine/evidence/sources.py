"""Evidence-source Protocols.

Each source is a narrow async port that knows how to pull one kind of
:class:`~brain_engine.evidence.models.*Pick` from its upstream store.
The composer calls every source concurrently via
``asyncio.gather(..., return_exceptions=True)`` so a single bad
source cannot block the bundle.

Four Protocols live here:

- :class:`RuleEvidenceSource`
- :class:`CaseEvidenceSource`
- :class:`PromptEvidenceSource`
- :class:`BlockerEvidenceSource`

All are ``@runtime_checkable`` so production adapters can be verified
with :func:`isinstance` at wiring time.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from brain_engine.evidence.models import (
    BlockerPick,
    CasePick,
    EvidenceQuery,
    PromptPick,
    RulePick,
)


@runtime_checkable
class RuleEvidenceSource(Protocol):
    """Pulls pattern-rule picks for a query."""

    async def fetch_rules(
        self,
        query: EvidenceQuery,
    ) -> tuple[RulePick, ...]:
        ...


@runtime_checkable
class CaseEvidenceSource(Protocol):
    """Pulls prior decision-case picks for a query."""

    async def fetch_cases(
        self,
        query: EvidenceQuery,
    ) -> tuple[CasePick, ...]:
        ...


@runtime_checkable
class PromptEvidenceSource(Protocol):
    """Pulls memory-prompt picks for a query."""

    async def fetch_prompts(
        self,
        query: EvidenceQuery,
    ) -> tuple[PromptPick, ...]:
        ...


@runtime_checkable
class BlockerEvidenceSource(Protocol):
    """Pulls live-blocker picks for a query."""

    async def fetch_blockers(
        self,
        query: EvidenceQuery,
    ) -> tuple[BlockerPick, ...]:
        ...
