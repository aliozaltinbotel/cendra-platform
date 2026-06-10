"""Tests for the confirmation-offer rule in the extractor prompt.

Aybüke 2026-05-18 follow-up: even after PR #303 fixed the
topic-hallucination half, the screenshot showed a second bug —
the AI replied to "Can I late checkout?" with concrete info
("until 12:00, additional €20") plus a closing offer ("Let me
know if you'd like me to arrange this for you"), and the
extractor still flagged it as a deferral.

Two layers protect against this now:

1. ``response_has_deferral`` (heuristic) — substring-matches
   well-known deferral phrases.  The late-checkout response
   does NOT contain any of them ("let me know" is NOT a
   deferral phrase; "get back to you" / "i'll check" / etc.
   are the real signals).  This test pins the heuristic so a
   well-meaning future PR cannot add "let me know" to the
   list and re-introduce the false positive.
2. ``_SYSTEM_PROMPT`` confirmation-offer rule — the LLM is
   explicitly told that a closing offer at the end of a
   concrete answer is NOT a deferral.  Test asserts the rule
   text is present (regression guard against accidental
   removal by a future prompt rewrite).
"""

from __future__ import annotations

from brain_engine.conversation.missing_info_extractor import (
    _SYSTEM_PROMPT,
    response_has_deferral,
)

# ── response_has_deferral heuristic (the fast path gate) ── #


def test_late_checkout_with_confirmation_offer_is_not_a_deferral() -> None:
    """Aybüke's exact case: concrete answer + closing offer ⇒ NOT deferred."""
    ai_response = (
        "A late check-out until 12:00 is possible for an additional "
        "fee of €20. Let me know if you'd like me to arrange this "
        "for you!"
    )
    assert response_has_deferral(ai_response) is False


def test_yes_parking_with_let_me_know_is_not_a_deferral() -> None:
    """'Yes, X is Y. Let me know if you need …' ⇒ NOT deferred."""
    assert (
        response_has_deferral(
            "Yes, parking is free. Let me know if you need directions.",
        )
        is False
    )


def test_real_deferral_promise_still_triggers() -> None:
    """No substantive answer + 'I'll check' ⇒ STILL deferred (regression)."""
    assert (
        response_has_deferral(
            "I'll check the late-checkout availability and get back to you.",
        )
        is True
    )


def test_turkish_deferral_still_triggers() -> None:
    """TR deferral language must keep firing (regression)."""
    assert (
        response_has_deferral(
            "Kontrol edip size geri döneceğim.",
        )
        is True
    )


def test_concrete_price_without_offer_phrase_is_not_a_deferral() -> None:
    """'X is €20.' alone ⇒ NOT deferred (sanity)."""
    assert response_has_deferral("Late check-out is €20 until 12:00.") is False


# ── _SYSTEM_PROMPT confirmation-offer rule (the LLM contract) ── #


def test_prompt_contains_confirmation_offer_rule() -> None:
    """LLM prompt must explicitly carry the 2026-05-18 rule.

    Regression guard: a future prompt rewrite that drops this
    section would silently re-introduce the false-positive
    Aybüke reported.  The text is matched verbatim because that
    is the exact contract the LLM is trained against.
    """
    assert "Confirmation-offer rule" in _SYSTEM_PROMPT
    assert "Let me know if you'd like me to arrange" in _SYSTEM_PROMPT
    assert "ANSWERED" in _SYSTEM_PROMPT


def test_prompt_includes_late_checkout_example() -> None:
    """The Aybüke-specific example must be in the prompt examples list."""
    assert "A late check-out until 12:00 is possible" in _SYSTEM_PROMPT
    assert "fee of €20" in _SYSTEM_PROMPT


def test_prompt_includes_real_deferral_counter_example() -> None:
    """A real deferral example must coexist to anchor the LLM."""
    # The "get back to you" example shows what real deferral
    # looks like, so the LLM does not over-correct after the
    # confirmation-offer carve-out.
    assert (
        "get back to you" in _SYSTEM_PROMPT
        or "get back to \nyou" in _SYSTEM_PROMPT
    )
    assert "DEFERRED" in _SYSTEM_PROMPT


def test_prompt_keeps_topic_rule_from_pr_303() -> None:
    """PR #303 Topic rule must still be in the prompt (regression)."""
    assert "Topic rule" in _SYSTEM_PROMPT
    assert "early check-in" in _SYSTEM_PROMPT
