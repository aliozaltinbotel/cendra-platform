"""Invariants of :class:`Art12Decision` and chained digest."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brain_engine.cards.action_kinds import CardActionKind
from brain_engine.compliance.art12_decision import (
    ART12_GENESIS_DIGEST,
    Art12Decision,
    HandlerSolver,
    canonical_record,
    chained_digest,
)


def _record(**overrides: object) -> Art12Decision:
    base: dict[str, object] = {
        "decision_id": "d1",
        "occurred_at": datetime(2026, 5, 10, tzinfo=timezone.utc),
        "property_id": "p1",
        "owner_id": "o1",
        "action_kind": CardActionKind.SEND_MESSAGE,
        "handler_solver": HandlerSolver.LLM,
        "rationale": "quiet hours warning",
        "provenance_digest": "a" * 64,
    }
    base.update(overrides)
    return Art12Decision(**base)  # type: ignore[arg-type]


def test_record_is_immutable() -> None:
    rec = _record()
    with pytest.raises((AttributeError, TypeError)):
        rec.decision_id = "x"  # type: ignore[misc]


def test_naive_occurred_at_rejected() -> None:
    with pytest.raises(ValueError, match="occurred_at"):
        _record(occurred_at=datetime(2026, 5, 10))


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"decision_id": ""}, "decision_id"),
        ({"property_id": ""}, "property_id"),
        ({"owner_id": ""}, "owner_id"),
        ({"rationale": ""}, "rationale"),
        ({"provenance_digest": ""}, "provenance_digest"),
        ({"prev_digest": "abc"}, "prev_digest"),
    ],
    ids=[
        "empty_decision",
        "empty_property",
        "empty_owner",
        "empty_rationale",
        "empty_provenance",
        "short_prev_digest",
    ],
)
def test_required_fields_validated(
    override: dict[str, object],
    match: str,
) -> None:
    """Empty / malformed required fields fail fast."""
    with pytest.raises(ValueError, match=match):
        _record(**override)


def test_default_prev_digest_is_genesis() -> None:
    """A fresh chain opens at the genesis digest."""
    rec = _record()
    assert rec.prev_digest == ART12_GENESIS_DIGEST


def test_canonical_record_is_deterministic() -> None:
    """Same record encodes to the same bytes every call."""
    rec = _record()
    assert canonical_record(rec) == canonical_record(rec)


def test_chained_digest_is_64_hex() -> None:
    """Digest is 64-char hex (BLAKE2B-256)."""
    rec = _record()
    digest = chained_digest(rec)
    assert len(digest) == 64
    int(digest, 16)  # valid hex


def test_changing_field_changes_digest() -> None:
    """Mutating any field produces a different digest."""
    a = chained_digest(_record(decision_id="d1"))
    b = chained_digest(_record(decision_id="d2"))
    assert a != b
