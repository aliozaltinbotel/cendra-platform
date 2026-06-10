"""Provider tier indirection for cognitive levels.

Reference: ``brain_engine_advisory.md`` §3.

The advisory recommends a multi-provider routing layer (Azure +
Anthropic + Google + EU residency).  The current Brain Engine policy
is Azure-only (ADR-0016): every LLM call goes through Azure OpenAI.
We still ship the *shape* of the provider tier today so that:

* the routing call site (``ComplexityRouter``) is decoupled from the
  concrete model name;
* swapping a deployment (e.g. ``gpt-4o-mini`` → ``gpt-4o-mini-eu``)
  is a single-row change in the table below, not a refactor;
* if business need emerges to add Anthropic or Google, only this
  table changes and `_PROVIDER_REGISTRY` in
  ``brain_engine/models/factory.py`` gains entries — no caller code
  is touched.

Today every slot resolves to an Azure deployment.  The slot names
(``primary`` / ``fallback`` / ``emergency`` / ``eu_resident``) are
preserved verbatim from advisory §3 so a future multi-provider
expansion has a place to drop alternatives without renaming.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class CognitiveLevel(str, Enum):
    """Mirrors ``brain_engine.memory.hierarchical_index``.

    Defined here too to avoid a circular import — the routing layer
    needs both modules.
    """

    L1_INSTINCT = "L1"
    L2_REFLEX = "L2"
    L3_EXPERIENCE = "L3"
    L4_DELIBERATIVE = "L4"


@dataclass(frozen=True, slots=True)
class ProviderTier:
    """Ordered fallback chain for one cognitive level.

    Each slot is a ``provider:deployment`` string compatible with
    ``brain_engine.models.factory.init_chat_model``.  Today every
    slot starts with ``azure_openai:`` (ADR-0016).
    """

    primary: str
    fallback: str
    emergency: str
    eu_resident: str

    def chain(self) -> tuple[str, str, str, str]:
        """Iteration order used by the failover loop."""
        return (
            self.primary,
            self.fallback,
            self.emergency,
            self.eu_resident,
        )


# ── Tier table ─────────────────────────────────────────────────────
# Naming convention: ``azure_openai:<deployment>``.  The deployment
# name must match a deployment configured in the Azure OpenAI
# resource (see deploy/brain-engine-*.yaml).

_LEVEL_PROVIDERS: Final[dict[CognitiveLevel, ProviderTier]] = {
    CognitiveLevel.L1_INSTINCT: ProviderTier(
        primary="azure_openai:gpt-4o-mini",
        fallback="azure_openai:gpt-4o-mini",
        emergency="azure_openai:gpt-4o-mini",
        eu_resident="azure_openai:gpt-4o-mini",
    ),
    CognitiveLevel.L2_REFLEX: ProviderTier(
        primary="azure_openai:gpt-4o-mini",
        fallback="azure_openai:gpt-4o",
        emergency="azure_openai:gpt-4o",
        eu_resident="azure_openai:gpt-4o-mini",
    ),
    CognitiveLevel.L3_EXPERIENCE: ProviderTier(
        primary="azure_openai:gpt-4o",
        fallback="azure_openai:gpt-4o",
        emergency="azure_openai:gpt-4o-mini",
        eu_resident="azure_openai:gpt-4o",
    ),
    CognitiveLevel.L4_DELIBERATIVE: ProviderTier(
        primary="azure_openai:gpt-4o",
        fallback="azure_openai:gpt-4o",
        emergency="azure_openai:gpt-4o",
        eu_resident="azure_openai:gpt-4o",
    ),
}


def tier_for(level: CognitiveLevel) -> ProviderTier:
    """Return the ``ProviderTier`` configured for a cognitive level."""
    return _LEVEL_PROVIDERS[level]


def all_tiers() -> dict[CognitiveLevel, ProviderTier]:
    """Snapshot of the tier table — used by the runbook export."""
    return dict(_LEVEL_PROVIDERS)
