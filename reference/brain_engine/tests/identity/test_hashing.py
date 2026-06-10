"""Behaviour of normalisation + HMAC helpers."""

from __future__ import annotations

import secrets

import pytest

from brain_engine.identity.hashing import (
    MIN_HMAC_KEY_BYTES,
    hmac_handle,
    normalize_email,
    normalize_phone,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alice@Example.com", "alice@example.com"),
        ("  bob@TEST.io  ", "bob@test.io"),
    ],
    ids=["mixed_case", "whitespace"],
)
def test_normalize_email_canonicalises(
    raw: str,
    expected: str,
) -> None:
    """Email normalisation is lower-case + trim."""
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "bad",
    ["no-at-sign", "two@@signs", "@example.com", "alice@nodomain"],
    ids=["no_at", "double_at", "empty_local", "no_dot"],
)
def test_normalize_email_rejects_garbage(bad: str) -> None:
    """Malformed inputs raise :class:`ValueError`."""
    with pytest.raises(ValueError):
        normalize_email(bad)


def test_normalize_phone_strips_non_digits() -> None:
    """Phone normalisation keeps only ``+`` and digits."""
    assert (
        normalize_phone("+1 (555) 123-4567")
        == "+15551234567"
    )


def test_normalize_phone_rejects_too_short() -> None:
    """Short numbers (<7 digits) are rejected."""
    with pytest.raises(ValueError, match="7 digits"):
        normalize_phone("12345")


def test_hmac_handle_short_key_rejected() -> None:
    """Keys shorter than the floor raise."""
    with pytest.raises(ValueError, match="key must be at least"):
        hmac_handle(key=b"short", normalised="alice@example.com")


def test_hmac_handle_empty_input_rejected() -> None:
    """Empty normalised string is rejected."""
    with pytest.raises(ValueError, match="non-empty"):
        hmac_handle(
            key=secrets.token_bytes(MIN_HMAC_KEY_BYTES),
            normalised="",
        )


def test_hmac_handle_is_deterministic() -> None:
    """Same key + input produce identical hex digest."""
    key = secrets.token_bytes(MIN_HMAC_KEY_BYTES)
    a = hmac_handle(key=key, normalised="alice@example.com")
    b = hmac_handle(key=key, normalised="alice@example.com")
    assert a == b
    assert len(a) == 64


def test_hmac_handle_different_keys_produce_different_hashes() -> None:
    """Rotating the key forces a re-build (different output)."""
    a = hmac_handle(
        key=secrets.token_bytes(MIN_HMAC_KEY_BYTES),
        normalised="alice@example.com",
    )
    b = hmac_handle(
        key=secrets.token_bytes(MIN_HMAC_KEY_BYTES),
        normalised="alice@example.com",
    )
    assert a != b
