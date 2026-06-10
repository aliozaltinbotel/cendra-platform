"""Behaviour of :class:`DeterministicMatcher`."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

from brain_engine.identity.graph import IdentityGraph
from brain_engine.identity.hashing import (
    MIN_HMAC_KEY_BYTES,
    hmac_handle,
    normalize_email,
    normalize_phone,
)
from brain_engine.identity.matcher import DeterministicMatcher
from brain_engine.identity.models import (
    ChannelHandle,
    ChannelKind,
    MatchEvidenceKind,
)


@pytest.fixture
def key() -> bytes:
    return secrets.token_bytes(MIN_HMAC_KEY_BYTES)


@pytest.fixture
def matcher() -> DeterministicMatcher:
    return DeterministicMatcher(graph=IdentityGraph())


def _handle(
    *,
    channel: ChannelKind,
    external_id: str,
    email_hash: str | None = None,
    phone_hash: str | None = None,
) -> ChannelHandle:
    return ChannelHandle(
        channel=channel,
        external_id=external_id,
        first_seen_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
        email_hash=email_hash,
        phone_hash=phone_hash,
    )


def test_email_match_returns_email_kind(
    matcher: DeterministicMatcher,
    key: bytes,
) -> None:
    """A handle carrying an email hash reports EMAIL_HASH evidence."""
    proposal = matcher.match(
        handle=_handle(
            channel=ChannelKind.AIRBNB,
            external_id="x",
            email_hash=hmac_handle(
                key=key,
                normalised=normalize_email("a@example.com"),
            ),
        )
    )
    assert proposal.kind is MatchEvidenceKind.EMAIL_HASH
    assert proposal.confidence == 1.0
    assert proposal.merged is False


def test_phone_only_match_returns_phone_kind(
    matcher: DeterministicMatcher,
    key: bytes,
) -> None:
    """A phone-only handle reports PHONE_HASH evidence."""
    proposal = matcher.match(
        handle=_handle(
            channel=ChannelKind.SMS,
            external_id="x",
            phone_hash=hmac_handle(
                key=key,
                normalised=normalize_phone("+15551234567"),
            ),
        )
    )
    assert proposal.kind is MatchEvidenceKind.PHONE_HASH


def test_no_hashes_falls_back_to_behavioural(
    matcher: DeterministicMatcher,
) -> None:
    """A handle with no hashes is recorded as BEHAVIOURAL evidence."""
    proposal = matcher.match(
        handle=_handle(
            channel=ChannelKind.WHATSAPP,
            external_id="anonymous",
        )
    )
    assert proposal.kind is MatchEvidenceKind.BEHAVIOURAL


def test_merge_signalled_in_proposal(
    matcher: DeterministicMatcher,
    key: bytes,
) -> None:
    """Bridging handle reports merged=True."""
    email = hmac_handle(
        key=key, normalised=normalize_email("a@example.com"),
    )
    phone = hmac_handle(
        key=key, normalised=normalize_phone("+15551234567"),
    )
    matcher.match(
        handle=_handle(
            channel=ChannelKind.AIRBNB,
            external_id="x",
            email_hash=email,
        )
    )
    matcher.match(
        handle=_handle(
            channel=ChannelKind.SMS,
            external_id="y",
            phone_hash=phone,
        )
    )
    bridge = matcher.match(
        handle=_handle(
            channel=ChannelKind.DIRECT,
            external_id="z",
            email_hash=email,
            phone_hash=phone,
        )
    )
    assert bridge.merged is True
    assert "merge" in bridge.rationale
