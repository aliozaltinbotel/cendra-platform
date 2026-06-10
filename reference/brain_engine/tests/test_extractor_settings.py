"""Tests for the missing-info-extractor settings module.

A3 (2026-05-20 round-2) replaces the inline ``content[:300]`` magic
number in ``_last_guest_message`` with a token-budget cap read from
``BRAIN_EXTRACTOR_GUEST_MSG_MAX_TOKENS``.  These tests pin the env
parsing contract so a bad operator value cannot accidentally let an
unbounded message reach the extractor LLM.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import pytest

from brain_engine.conversation.extractor_settings import (
    GUEST_MSG_MAX_TOKENS_DEFAULT,
    GUEST_MSG_MAX_TOKENS_ENV,
    guest_message_token_budget,
)


@pytest.fixture(autouse=True)
def _clear_env_between_tests() -> Iterator[None]:
    """Strip the env var so each test starts from a known baseline."""
    snapshot = os.environ.pop(GUEST_MSG_MAX_TOKENS_ENV, None)
    try:
        yield
    finally:
        os.environ.pop(GUEST_MSG_MAX_TOKENS_ENV, None)
        if snapshot is not None:
            os.environ[GUEST_MSG_MAX_TOKENS_ENV] = snapshot


# ── Default ───────────────────────────────────────────────────────


def test_unset_env_returns_default() -> None:
    """No env var → documented default; matches legacy 300-char cap
    at ~4 chars/token."""
    assert guest_message_token_budget() == GUEST_MSG_MAX_TOKENS_DEFAULT
    assert GUEST_MSG_MAX_TOKENS_DEFAULT == 75


def test_blank_env_returns_default() -> None:
    """Empty / whitespace value behaves the same as unset."""
    os.environ[GUEST_MSG_MAX_TOKENS_ENV] = "   "
    assert guest_message_token_budget() == GUEST_MSG_MAX_TOKENS_DEFAULT


# ── Valid override ────────────────────────────────────────────────


def test_positive_int_override_returned_verbatim() -> None:
    """Operator widens or narrows the budget at deploy time."""
    os.environ[GUEST_MSG_MAX_TOKENS_ENV] = "200"
    assert guest_message_token_budget() == 200


def test_override_read_on_every_call() -> None:
    """No caching — flipping the env at runtime takes effect on the
    next call (matches the project convention for ``BRAIN_*`` flags
    so an SRE can flip values without bouncing the pod)."""
    os.environ[GUEST_MSG_MAX_TOKENS_ENV] = "120"
    assert guest_message_token_budget() == 120
    os.environ[GUEST_MSG_MAX_TOKENS_ENV] = "60"
    assert guest_message_token_budget() == 60


# ── Bad input degrades to default + WARN ──────────────────────────


def test_non_integer_env_logs_warning_and_returns_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A typo like ``BRAIN_EXTRACTOR_GUEST_MSG_MAX_TOKENS=abc`` must
    not crash the pipeline — it falls back to the default and logs
    one WARN line so deploy review catches it."""
    os.environ[GUEST_MSG_MAX_TOKENS_ENV] = "abc"
    with caplog.at_level(
        logging.WARNING,
        logger="brain_engine.conversation.extractor_settings",
    ):
        result = guest_message_token_budget()
    assert result == GUEST_MSG_MAX_TOKENS_DEFAULT
    assert any("not an int" in r.message for r in caplog.records)


def test_non_positive_env_logs_warning_and_returns_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-positive value (``0`` or negative) would feed an empty
    message to the LLM; we degrade to the default instead."""
    os.environ[GUEST_MSG_MAX_TOKENS_ENV] = "0"
    with caplog.at_level(
        logging.WARNING,
        logger="brain_engine.conversation.extractor_settings",
    ):
        zero_result = guest_message_token_budget()

    os.environ[GUEST_MSG_MAX_TOKENS_ENV] = "-5"
    with caplog.at_level(
        logging.WARNING,
        logger="brain_engine.conversation.extractor_settings",
    ):
        negative_result = guest_message_token_budget()

    assert zero_result == GUEST_MSG_MAX_TOKENS_DEFAULT
    assert negative_result == GUEST_MSG_MAX_TOKENS_DEFAULT
    assert sum("non-positive" in r.message for r in caplog.records) >= 2


def test_float_value_falls_back_to_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A float-looking value (e.g. ``75.5``) is not a valid token
    count — degrade to the default instead of silently truncating."""
    os.environ[GUEST_MSG_MAX_TOKENS_ENV] = "75.5"
    with caplog.at_level(
        logging.WARNING,
        logger="brain_engine.conversation.extractor_settings",
    ):
        result = guest_message_token_budget()
    assert result == GUEST_MSG_MAX_TOKENS_DEFAULT
    assert any("not an int" in r.message for r in caplog.records)
