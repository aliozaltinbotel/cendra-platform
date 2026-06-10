"""End-to-end behaviour of :class:`IdentityGraph`."""

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
from brain_engine.identity.models import (
    ChannelHandle,
    ChannelKind,
)


@pytest.fixture
def key() -> bytes:
    return secrets.token_bytes(MIN_HMAC_KEY_BYTES)


@pytest.fixture
def graph() -> IdentityGraph:
    return IdentityGraph()


def _email_hash(key: bytes, value: str) -> str:
    return hmac_handle(
        key=key, normalised=normalize_email(value),
    )


def _phone_hash(key: bytes, value: str) -> str:
    return hmac_handle(
        key=key, normalised=normalize_phone(value),
    )


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


def test_first_handle_mints_identity(
    graph: IdentityGraph,
    key: bytes,
) -> None:
    """A handle with no prior hash mints a fresh identity."""
    handle = _handle(
        channel=ChannelKind.AIRBNB,
        external_id="a-1",
        email_hash=_email_hash(key, "alice@example.com"),
    )
    identity_id, merged = graph.insert(handle=handle)
    assert merged is False
    assert identity_id.startswith("gid_")


def test_same_email_attaches_to_existing(
    graph: IdentityGraph,
    key: bytes,
) -> None:
    """A second handle with the same email lands on same identity."""
    h = _email_hash(key, "alice@example.com")
    a, _ = graph.insert(
        handle=_handle(
            channel=ChannelKind.AIRBNB,
            external_id="a-1",
            email_hash=h,
        )
    )
    b, merged = graph.insert(
        handle=_handle(
            channel=ChannelKind.BOOKING,
            external_id="b-1",
            email_hash=h,
        )
    )
    assert a == b
    assert merged is False


def test_bridging_handle_merges_two_identities(
    graph: IdentityGraph,
    key: bytes,
) -> None:
    """A handle carrying two hashes that map to distinct ids merges."""
    email = _email_hash(key, "alice@example.com")
    phone = _phone_hash(key, "+1 555 123 4567")
    a, _ = graph.insert(
        handle=_handle(
            channel=ChannelKind.AIRBNB,
            external_id="a-1",
            email_hash=email,
        )
    )
    b, _ = graph.insert(
        handle=_handle(
            channel=ChannelKind.SMS,
            external_id="s-1",
            phone_hash=phone,
        )
    )
    assert a != b
    survivor, merged = graph.insert(
        handle=_handle(
            channel=ChannelKind.DIRECT,
            external_id="d-1",
            email_hash=email,
            phone_hash=phone,
        )
    )
    assert merged is True
    assert survivor in {a, b}
    record = graph.get(survivor)
    assert record is not None
    absorbed = {a, b} - {survivor}
    assert absorbed.issubset(set(record.merged_from))


def test_unrelated_handles_remain_separate(
    graph: IdentityGraph,
    key: bytes,
) -> None:
    """Handles with no overlapping hashes stay distinct."""
    a, _ = graph.insert(
        handle=_handle(
            channel=ChannelKind.AIRBNB,
            external_id="a",
            email_hash=_email_hash(key, "alice@example.com"),
        )
    )
    b, _ = graph.insert(
        handle=_handle(
            channel=ChannelKind.WHATSAPP,
            external_id="w",
            email_hash=_email_hash(key, "bob@example.com"),
        )
    )
    assert a != b


def test_lookup_by_hash(
    graph: IdentityGraph,
    key: bytes,
) -> None:
    """Hash → identity_id round-trip exposes the join surface."""
    h = _email_hash(key, "alice@example.com")
    identity_id, _ = graph.insert(
        handle=_handle(
            channel=ChannelKind.AIRBNB,
            external_id="a",
            email_hash=h,
        )
    )
    assert graph.lookup_by_hash(h) == identity_id
    assert graph.lookup_by_hash("0" * 64) is None
