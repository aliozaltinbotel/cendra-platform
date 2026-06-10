"""Tests for the :class:`BootstrapIntentMessage` wire contract.

The message is the producer↔worker format: the Stage 2 dispatcher
serialises it onto the queue and the worker rebuilds it on the other
side of a process boundary.  A round-trip must be lossless, and any
malformed body must raise ``ValueError`` — the worker turns that into
a dead-letter rather than silently building a half-valid message.
"""

from __future__ import annotations

import json

import pytest

from brain_engine.tenants import BootstrapIntentMessage

_REQUIRED_STRINGS = (
    "property_channel_id",
    "customer_id",
    "provider_type",
    "reason",
    "job_id",
)


def _message(**overrides: object) -> BootstrapIntentMessage:
    fields: dict[str, object] = {
        "property_channel_id": "598808",
        "customer_id": "cust-uuid",
        "provider_type": "HOSTAWAY",
        "window_days": 730,
        "reason": "ui_select",
        "job_id": "job-abc",
        "org_id": "org-uuid",
    }
    fields.update(overrides)
    return BootstrapIntentMessage(**fields)  # type: ignore[arg-type]


def test_round_trip_preserves_all_fields() -> None:
    original = _message()
    restored = BootstrapIntentMessage.from_json(original.to_json())
    assert restored == original


def test_round_trip_with_none_org_id() -> None:
    original = _message(org_id=None)
    restored = BootstrapIntentMessage.from_json(original.to_json())
    assert restored.org_id is None
    assert restored == original


def test_to_json_is_compact_and_key_sorted() -> None:
    body = _message().to_json()
    # Compact separators: no whitespace after ',' or ':'.
    assert ", " not in body
    assert ": " not in body
    # Stable, key-sorted body keeps the dedup hash deterministic.
    keys = list(json.loads(body).keys())
    assert keys == sorted(keys)


def test_from_json_accepts_bytes() -> None:
    restored = BootstrapIntentMessage.from_json(
        _message().to_json().encode("utf-8"),
    )
    assert restored == _message()


def test_from_json_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        BootstrapIntentMessage.from_json("[]")


def test_from_json_rejects_invalid_json() -> None:
    with pytest.raises(ValueError):
        BootstrapIntentMessage.from_json("{not valid")


@pytest.mark.parametrize("field", _REQUIRED_STRINGS)
def test_from_json_rejects_missing_required(field: str) -> None:
    data = json.loads(_message().to_json())
    del data[field]
    with pytest.raises(ValueError):
        BootstrapIntentMessage.from_json(json.dumps(data))


@pytest.mark.parametrize("field", _REQUIRED_STRINGS)
def test_from_json_rejects_blank_required(field: str) -> None:
    data = json.loads(_message().to_json())
    data[field] = "   "
    with pytest.raises(ValueError):
        BootstrapIntentMessage.from_json(json.dumps(data))


@pytest.mark.parametrize("bad", [0, -5, "730", 7.5, True, None])
def test_from_json_rejects_non_positive_window(bad: object) -> None:
    data = json.loads(_message().to_json())
    data["window_days"] = bad
    with pytest.raises(ValueError):
        BootstrapIntentMessage.from_json(json.dumps(data))
