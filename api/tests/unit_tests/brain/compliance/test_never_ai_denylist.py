"""Behaviour of the structurally-enforced never-AI denylist."""

from __future__ import annotations

import pytest

from core.brain.compliance.never_ai_denylist import (
    NEVER_AI_REASONS,
    NeverAICategory,
    StructuralDenyError,
    is_never_ai,
    reason_for,
)


def test_four_categories_are_defined() -> None:
    """Exactly four structural-deny categories ship."""
    assert len(NeverAICategory) == 4


def test_every_category_has_a_reason() -> None:
    """Each enum member has a non-empty rationale string."""
    for category in NeverAICategory:
        assert category in NEVER_AI_REASONS
        assert NEVER_AI_REASONS[category]


@pytest.mark.parametrize(
    "category",
    list(NeverAICategory),
    ids=lambda c: c.value,
)
def test_is_never_ai_recognises_enum(
    category: NeverAICategory,
) -> None:
    """``is_never_ai`` returns ``True`` for every enum member."""
    assert is_never_ai(category) is True


def test_is_never_ai_recognises_string_value() -> None:
    """``is_never_ai`` accepts the underlying string value."""
    assert is_never_ai("screen_by_protected_class") is True


def test_is_never_ai_rejects_unknown_string() -> None:
    """Unknown strings are not in the never-AI set."""
    assert is_never_ai("random_thing") is False


def test_structural_deny_error_message_carries_category() -> None:
    """The error name + category are visible in str()."""
    error = StructuralDenyError(
        NeverAICategory.GDPR_ART22_AUTONOMOUS_DENY,
    )
    text = str(error)
    assert "structural deny" in text
    assert "gdpr_art22_autonomous_deny" in text
    assert error.category is (NeverAICategory.GDPR_ART22_AUTONOMOUS_DENY)


def test_reason_for_returns_string() -> None:
    """``reason_for`` returns the canonical rationale."""
    text = reason_for(NeverAICategory.SCREEN_BY_PROTECTED_CLASS)
    assert "protected class" in text
