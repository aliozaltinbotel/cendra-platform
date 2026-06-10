"""Invariants of identity value objects."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brain_engine.identity.models import (
    ChannelHandle,
    ChannelKind,
    GuestIdentity,
    MatchEvidenceKind,
    MatchProposal,
)


def _now() -> datetime:
    return datetime(2026, 5, 10, tzinfo=timezone.utc)


def test_channel_handle_immutable() -> None:
    handle = ChannelHandle(
        channel=ChannelKind.AIRBNB,
        external_id="x",
        first_seen_at=_now(),
    )
    with pytest.raises((AttributeError, TypeError)):
        handle.external_id = "y"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"external_id": ""}, "external_id"),
        ({"first_seen_at": datetime(2026, 5, 10)}, "first_seen_at"),
        ({"email_hash": "abc"}, "email_hash"),
        ({"phone_hash": "abc"}, "phone_hash"),
    ],
    ids=[
        "empty_external",
        "naive_first_seen",
        "short_email",
        "short_phone",
    ],
)
def test_channel_handle_validation(
    override: dict[str, object],
    match: str,
) -> None:
    base: dict[str, object] = {
        "channel": ChannelKind.AIRBNB,
        "external_id": "id-1",
        "first_seen_at": _now(),
    }
    base.update(override)
    with pytest.raises(ValueError, match=match):
        ChannelHandle(**base)  # type: ignore[arg-type]


def test_guest_identity_requires_handles() -> None:
    with pytest.raises(ValueError, match="handles"):
        GuestIdentity(identity_id="x", handles=())


def test_match_proposal_validation() -> None:
    base: dict[str, object] = {
        "identity_id": "gid",
        "kind": MatchEvidenceKind.EMAIL_HASH,
        "confidence": 0.5,
        "rationale": "ok",
    }
    MatchProposal(**base)  # baseline ok
    with pytest.raises(ValueError, match="confidence"):
        MatchProposal(**{**base, "confidence": 1.5})
    with pytest.raises(ValueError, match="rationale"):
        MatchProposal(**{**base, "rationale": ""})
    with pytest.raises(ValueError, match="identity_id"):
        MatchProposal(**{**base, "identity_id": ""})
