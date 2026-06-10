"""Decision-evidence read model (GAP L).

Public surface:

- :class:`EvidenceBundle` / :class:`EvidenceSummary`
- :class:`EvidenceQuery` / :class:`DecisionReference`
- :class:`RulePick` / :class:`CasePick` / :class:`PromptPick` /
  :class:`BlockerPick`
- :class:`EvidenceWeight`
- :class:`EvidenceService`
- :class:`RuleEvidenceSource` / :class:`CaseEvidenceSource` /
  :class:`PromptEvidenceSource` / :class:`BlockerEvidenceSource`
- :class:`EvidenceError` / :class:`EvidenceNotFound` /
  :class:`EvidenceSourceError` / :class:`EvidenceCompositionError`
"""

from __future__ import annotations

from brain_engine.evidence.adapters import (
    BlockerEvidenceAdapter,
    DecisionCaseEvidenceAdapter,
    MemoryPromptEvidenceAdapter,
    PatternRuleEvidenceAdapter,
)
from brain_engine.evidence.errors import (
    EvidenceCompositionError,
    EvidenceError,
    EvidenceNotFound,
    EvidenceSourceError,
)
from brain_engine.evidence.models import (
    BlockerPick,
    CasePick,
    DecisionReference,
    EvidenceBundle,
    EvidenceQuery,
    EvidenceSummary,
    EvidenceWeight,
    PromptPick,
    RulePick,
)
from brain_engine.evidence.service import EvidenceService
from brain_engine.evidence.sources import (
    BlockerEvidenceSource,
    CaseEvidenceSource,
    PromptEvidenceSource,
    RuleEvidenceSource,
)

__all__ = [
    "BlockerEvidenceAdapter",
    "BlockerEvidenceSource",
    "BlockerPick",
    "CaseEvidenceSource",
    "CasePick",
    "DecisionCaseEvidenceAdapter",
    "DecisionReference",
    "EvidenceBundle",
    "EvidenceCompositionError",
    "EvidenceError",
    "EvidenceNotFound",
    "EvidenceQuery",
    "EvidenceService",
    "EvidenceSourceError",
    "EvidenceSummary",
    "EvidenceWeight",
    "MemoryPromptEvidenceAdapter",
    "PatternRuleEvidenceAdapter",
    "PromptEvidenceSource",
    "PromptPick",
    "RuleEvidenceSource",
    "RulePick",
]
