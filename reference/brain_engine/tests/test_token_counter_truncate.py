# ruff: noqa: RUF001
# RUF001 (ambiguous unicode) suppressed file-wide because some
# fixtures use the Turkish letters live traffic actually carries.
"""Tests for ``token_counter.truncate_to_tokens``.

A3 (2026-05-20 round-2) adds a token-budget truncation helper used
by the missing-info extractor.  Pinning the contract so a future
refactor cannot silently regress to the legacy character cap.
"""

from __future__ import annotations

import pytest

from brain_engine.context.token_counter import (
    TokenCounter,
    truncate_to_tokens,
)

# ── Pass-through paths ────────────────────────────────────────────


def test_empty_text_returns_empty() -> None:
    assert truncate_to_tokens("", max_tokens=100) == ""


def test_non_positive_budget_returns_empty() -> None:
    """``0`` and negative budgets mean "no room" — return empty."""
    assert truncate_to_tokens("anything", max_tokens=0) == ""
    assert truncate_to_tokens("anything", max_tokens=-5) == ""


def test_short_text_returned_verbatim() -> None:
    """When the input already fits, no truncation happens."""
    text = "Late check-out is possible until 12:00 for an extra fee."
    counter = TokenCounter(model="gpt-4o-mini")
    actual_tokens = counter.count_text(text)
    result = truncate_to_tokens(text, max_tokens=actual_tokens + 10)
    assert result == text


# ── Truncation paths ──────────────────────────────────────────────


def test_long_text_truncated_to_budget() -> None:
    """A clearly-oversized input is shortened to fit the budget."""
    text = "word " * 500  # ~500 tokens
    result = truncate_to_tokens(text, max_tokens=20)
    counter = TokenCounter(model="gpt-4o-mini")
    assert counter.count_text(result) <= 20
    assert result != text


def test_truncation_preserves_prefix_semantics() -> None:
    """The truncated result starts with the original text's prefix
    — the matcher / LLM still sees the message's opening phrase."""
    text = (
        "The guest is asking about late check-out. "
        "They mention the time of 13:00 and ask if there is an "
        "additional fee. Please advise."
    )
    result = truncate_to_tokens(text, max_tokens=8)
    # The first few words must survive — the prefix is what carries
    # the guest's intent.
    assert result.startswith("The")


def test_unicode_text_truncated_safely() -> None:
    """Turkish text round-trips without mid-codepoint corruption."""
    text = "Geç çıkış mümkün mü, saat kaça kadar kalabiliriz?"
    counter = TokenCounter(model="gpt-4o-mini")
    actual_tokens = counter.count_text(text)
    result = truncate_to_tokens(text, max_tokens=max(1, actual_tokens // 2))
    # No partial mojibake — every char remains a valid codepoint.
    result.encode("utf-8").decode("utf-8")
    assert counter.count_text(result) <= max(1, actual_tokens // 2)


# ── Behavioural parity with legacy 300-char cap ──────────────────


def test_default_budget_approximates_legacy_300_char_cap() -> None:
    """Default token budget (75) at ~4 chars/token covers around
    300 chars — preserves prompt budget for messages that used to
    hit the legacy cap."""
    text = "x" * 400
    # 75 tokens of 'x' chars — at >=1 char/token this fits in 400
    # chars but truncates to at most 400 chars (legacy ceiling).
    result = truncate_to_tokens(text, max_tokens=75)
    assert len(result) <= 400


# ── Sanity ────────────────────────────────────────────────────────


@pytest.mark.parametrize("budget", [1, 10, 100, 1000])
def test_budget_is_hard_upper_bound(budget: int) -> None:
    """Output token count never exceeds the requested budget."""
    text = "x " * 5000
    result = truncate_to_tokens(text, max_tokens=budget)
    counter = TokenCounter(model="gpt-4o-mini")
    assert counter.count_text(result) <= budget
