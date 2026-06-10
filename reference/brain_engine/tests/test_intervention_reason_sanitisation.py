"""Tests for ``_sanitize_intervention_reason``.

Tester complaints #1 and #6 (live captures 2026-05-19 and 2026-05-20
in Sandbox UI tests #71 and #17) flagged the
``"Guest needs <topic> which is not in the knowledge base"``
boilerplate that ends up in PM Chat and in stored ``pm_facts``.
Two failure modes:

#1: the literal boilerplate is noisy — PM Chat already wraps the
    flag in its own UI label, so the inline English suffix is
    redundant and ends up persisted in
    ``MANAGER-CONFIRMED KNOWLEDGE`` for every PM-confirmed fact.
#6: when the topic itself is Turkish (LLM extracted from a TR
    guest message), the English template suffix produces a mixed
    sentence (``"Guest needs ek ücret olup olmadığı bilgisi which
    is not in the knowledge base"``) — language drift the model
    then echoes back to the guest.

This sanitiser is the transitional fix.  Long-term, the
``missing_info_extractor`` SYSTEM_PROMPT will be rewritten so the
LLM no longer emits the boilerplate (tester proposal A1) — at that
point this helper becomes dead code.
"""

from __future__ import annotations

import pytest

from brain_engine.conversation.service import (
    _sanitize_intervention_reason,
)


# ── Live-captured shapes (regression anchors) ─────────────────────


def test_strips_legacy_english_template_with_quoted_topic() -> None:
    """Original catalog-topic shape before this PR fixed the
    upstream emission too."""
    raw = (
        "Guest needs information about 'early check-in' "
        "which is not in the knowledge base"
    )
    assert _sanitize_intervention_reason(raw) == "early check-in"


def test_strips_template_with_unquoted_topic() -> None:
    raw = "Guest needs early check-in which is not in the knowledge base"
    assert _sanitize_intervention_reason(raw) == "early check-in"


def test_strips_template_with_turkish_topic_71_capture() -> None:
    """The live #71 capture: TR topic + EN template suffix.
    Sanitiser must keep the TR topic and drop the EN frame."""
    raw = (
        "Guest needs ek ücret olup olmadığı bilgisi "
        "which is not in the knowledge base"
    )
    assert (
        _sanitize_intervention_reason(raw)
        == "ek ücret olup olmadığı bilgisi"
    )


def test_strips_template_with_early_checkout_17_capture() -> None:
    """The live #17 capture: misclassified topic but boilerplate
    intact.  Sanitiser preserves the topic content; the
    classification bug itself is a separate concern (A1)."""
    raw = "Guest needs early check-out which is not in the knowledge base"
    assert _sanitize_intervention_reason(raw) == "early check-out"


# ── Variant suffix / prefix shapes ────────────────────────────────


def test_strips_alternate_suffix_our_knowledge_base() -> None:
    raw = "Guest needs pet policy which is not in our knowledge base"
    assert _sanitize_intervention_reason(raw) == "pet policy"


def test_strips_that_is_suffix_variant() -> None:
    raw = "Guest needs WiFi password that is not in the knowledge base"
    assert _sanitize_intervention_reason(raw) == "WiFi password"


def test_strips_the_guest_needs_prefix() -> None:
    raw = (
        "The guest needs late checkout pricing "
        "which is not in the knowledge base"
    )
    assert _sanitize_intervention_reason(raw) == "late checkout pricing"


# ── Edge cases ────────────────────────────────────────────────────


def test_preserves_text_without_boilerplate() -> None:
    raw = "guest count mismatch (4 vs 2 adults)"
    assert _sanitize_intervention_reason(raw) == raw


def test_returns_original_when_only_boilerplate_present() -> None:
    """A pathological case where the sanitiser would collapse the
    text to empty.  Fall back to the original (stripped) so PM
    Chat never sees a blank flag."""
    raw = "Guest needs which is not in the knowledge base"
    sanitised = _sanitize_intervention_reason(raw)
    assert sanitised
    assert sanitised == raw.strip()


def test_handles_empty_input() -> None:
    assert _sanitize_intervention_reason("") == ""


def test_handles_whitespace_only_input() -> None:
    assert _sanitize_intervention_reason("   ") == ""


def test_collapses_internal_whitespace() -> None:
    raw = "Guest needs   pool   heating   which is not in the knowledge base"
    assert _sanitize_intervention_reason(raw) == "pool heating"


def test_case_insensitive_match() -> None:
    raw = "GUEST NEEDS early checkin WHICH IS NOT IN THE KNOWLEDGE BASE"
    assert _sanitize_intervention_reason(raw) == "early checkin"


def test_trailing_punctuation_stripped() -> None:
    raw = "Guest needs parking, which is not in the knowledge base."
    assert _sanitize_intervention_reason(raw) == "parking"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("pet policy", "pet policy"),
        ("WiFi password", "WiFi password"),
        ("ek yatak talebi", "ek yatak talebi"),
        ("door code release", "door code release"),
    ],
)
def test_already_clean_topics_pass_through(
    raw: str,
    expected: str,
) -> None:
    """Bare topic strings (the new shape this PR emits in the
    catalog path) must not be mangled by the sanitiser."""
    assert _sanitize_intervention_reason(raw) == expected
