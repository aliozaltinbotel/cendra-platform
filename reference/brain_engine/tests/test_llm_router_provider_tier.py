"""Tests for LLMRouter.tier_for_level wiring (advisory §3, ADR-0016).

Pins the bridge between the live :class:`ComplexityRouter`
``CognitiveLevel`` enum (values ``instinct`` / ``situation`` /
``experience`` / ``strategy``) and the
:mod:`brain_engine.reasoning.provider_tier` lookup table that keys
on ``L1`` / ``L2`` / ``L3`` / ``L4``.  A regression that drifts
either enum's member names will surface here before the failover
chain points at the wrong deployment in production.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

import pytest

from brain_engine.reasoning.complexity_router import CognitiveLevel
from brain_engine.reasoning.llm_router import LLMRouter
from brain_engine.reasoning.provider_tier import (
    ProviderTier,
    tier_for,
)
from brain_engine.reasoning.provider_tier import (
    CognitiveLevel as ProviderTierLevel,
)


# Sanity table — every cognitive level the production router emits
# must resolve through ``tier_for_level``. Ordered to match
# advisory §3 escalation steps L1 → L4.
_LEVEL_PREFIX_MAP: Final[dict[CognitiveLevel, ProviderTierLevel]] = {
    CognitiveLevel.L1_INSTINCT: ProviderTierLevel.L1_INSTINCT,
    CognitiveLevel.L2_SITUATION: ProviderTierLevel.L2_REFLEX,
    CognitiveLevel.L3_EXPERIENCE: ProviderTierLevel.L3_EXPERIENCE,
    CognitiveLevel.L4_STRATEGY: ProviderTierLevel.L4_DELIBERATIVE,
}


@pytest.mark.parametrize(
    "level,expected_pt_level", list(_LEVEL_PREFIX_MAP.items()),
)
def test_tier_for_level_resolves_each_complexity_level(
    level: CognitiveLevel,
    expected_pt_level: ProviderTierLevel,
) -> None:
    expected_tier = tier_for(expected_pt_level)
    actual_tier = LLMRouter.tier_for_level(level)

    assert actual_tier == expected_tier


def test_tier_for_level_returns_provider_tier_instance() -> None:
    tier = LLMRouter.tier_for_level(CognitiveLevel.L1_INSTINCT)

    assert isinstance(tier, ProviderTier)
    assert tier.primary  # non-empty deployment string
    # ADR-0016: every slot is Azure-only today.
    for slot in tier.chain():
        assert slot.startswith("azure_openai:"), slot


def test_tier_for_level_rejects_unknown_prefix() -> None:
    """A new enum member without an L<n> prefix surfaces loudly."""

    class _Drift(str, Enum):
        ROGUE = "rogue"

    with pytest.raises(ValueError):
        LLMRouter.tier_for_level(_Drift.ROGUE)  # type: ignore[arg-type]


def test_tier_for_level_is_static() -> None:
    """Method does not need an LLMRouter instance — keeps callers cheap."""
    tier_via_class = LLMRouter.tier_for_level(CognitiveLevel.L4_STRATEGY)
    tier_via_instance = LLMRouter().tier_for_level(CognitiveLevel.L4_STRATEGY)

    assert tier_via_class == tier_via_instance
