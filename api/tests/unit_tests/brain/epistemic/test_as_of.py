"""As-of valid-time semantics for External-Knowledge retrieval (CEN-28)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.brain.epistemic.as_of import (
    DocumentValidity,
    bitemporal_provenance,
    document_validity,
    kg_snapshot_ref,
    parse_as_of,
    validity_observation,
    visible_as_of,
)
from core.brain.epistemic.models import ProvenanceKind, observation_integrity_hash

T = datetime(2026, 6, 11, 9, 14, 3, tzinfo=UTC)


# ── parse_as_of ──────────────────────────────────────────────────── #


def test_parse_as_of_accepts_rfc3339_z_suffix():
    parsed = parse_as_of("2026-06-11T09:14:03Z")
    assert parsed == T
    assert parsed.tzinfo is not None


def test_parse_as_of_accepts_offset_and_normalises_to_utc():
    parsed = parse_as_of("2026-06-11T11:14:03+02:00")
    assert parsed == T


def test_parse_as_of_treats_naive_as_utc():
    assert parse_as_of("2026-06-11T09:14:03") == T


@pytest.mark.parametrize("raw", ["", "   ", "not-a-date", "2026-13-45T99:00:00Z", None, 42])
def test_parse_as_of_rejects_junk(raw):
    with pytest.raises(ValueError):
        parse_as_of(raw)  # type: ignore[arg-type]


# ── visible_as_of ────────────────────────────────────────────────── #


def test_window_containing_as_of_is_visible():
    assert visible_as_of(
        as_of=T,
        valid_from=T - timedelta(days=10),
        valid_to=T + timedelta(days=10),
        recorded_at=T - timedelta(days=9),
    )


def test_valid_from_after_as_of_is_hidden():
    assert not visible_as_of(
        as_of=T,
        valid_from=T + timedelta(seconds=1),
        valid_to=None,
        recorded_at=None,
    )


def test_valid_to_boundary_is_exclusive():
    # contract: valid_from <= as_of < valid_to
    assert not visible_as_of(as_of=T, valid_from=None, valid_to=T, recorded_at=None)


def test_valid_from_boundary_is_inclusive():
    assert visible_as_of(as_of=T, valid_from=T, valid_to=None, recorded_at=None)


def test_facts_recorded_after_as_of_are_hidden():
    # transaction-time rule: no facts asserted after T
    assert not visible_as_of(
        as_of=T,
        valid_from=T - timedelta(days=10),
        valid_to=None,
        recorded_at=T + timedelta(minutes=1),
    )


def test_missing_window_degrades_to_visible():
    assert visible_as_of(as_of=T, valid_from=None, valid_to=None, recorded_at=None)


def test_open_ended_window_still_valid():
    assert visible_as_of(
        as_of=T,
        valid_from=T - timedelta(days=1),
        valid_to=None,
        recorded_at=T - timedelta(days=1),
    )


# ── document_validity (ingest ruling) ────────────────────────────── #


def test_operator_asserted_window_is_honoured():
    validity = document_validity(
        document_id="doc-1",
        doc_metadata={"valid_from": "2026-05-01T00:00:00Z", "valid_to": "2026-07-01T00:00:00Z"},
        uploaded_at=T,
    )
    assert validity.valid_from == datetime(2026, 5, 1, tzinfo=UTC)
    assert validity.valid_to == datetime(2026, 7, 1, tzinfo=UTC)
    assert validity.unverified_window is False


def test_migrated_corpus_defaults_to_upload_date_with_unverified_flag():
    # adjudicated CEN-15 ruling: valid_from required only for newly
    # indexed docs; migrated corpora default to upload date + flag
    validity = document_validity(document_id="doc-2", doc_metadata={}, uploaded_at=T)
    assert validity.valid_from == T
    assert validity.valid_to is None
    assert validity.unverified_window is True


def test_naive_uploaded_at_is_rejected():
    with pytest.raises(ValueError, match="tz-aware"):
        document_validity(document_id="doc-3", doc_metadata=None, uploaded_at=T.replace(tzinfo=None))


def test_validity_observation_round_trip():
    validity = DocumentValidity(document_id="doc-4", valid_from=T, valid_to=None, unverified_window=True)
    observation = validity_observation(validity, recorded_at=T, source_id="dify:doc_metadata:doc-4")
    assert observation.subject == "doc:doc-4:validity"
    assert observation.value["valid_from"] == T.isoformat()
    assert observation.value["valid_window_unverified"] is True
    assert observation.provenance.kind is ProvenanceKind.SYSTEM
    # integrity hash signs the canonical payload
    assert observation.integrity_hex == observation_integrity_hash(
        observation_id=observation.observation_id,
        subject=observation.subject,
        value=observation.value,
        recorded_at=observation.recorded_at,
        provenance=observation.provenance,
    )


# ── provenance block ─────────────────────────────────────────────── #


def test_kg_snapshot_ref_is_deterministic():
    assert kg_snapshot_ref("tenant:t1", T) == f"brain:kg:tenant:t1@{T.isoformat()}"
    with pytest.raises(ValueError):
        kg_snapshot_ref("", T)


def test_provenance_block_carries_both_timelines():
    retrieved_at = T + timedelta(seconds=2)
    block = bitemporal_provenance(
        {"valid_from": "2026-05-01T00:00:00Z", "valid_to": None, "recorded_at": "2026-05-02T08:11:09Z"},
        as_of_used=T,
        retrieved_at=retrieved_at,
        snapshot_ref="brain:kg:tenant:t1@x",
    )
    assert block["as_of_used"] == T.isoformat()
    assert block["retrieved_at"] == retrieved_at.isoformat()
    assert block["valid_from"] == datetime(2026, 5, 1, tzinfo=UTC).isoformat()
    assert block["valid_to"] is None
    assert block["kg_snapshot_ref"] == "brain:kg:tenant:t1@x"
    assert "valid_window_unverified" not in block


def test_provenance_block_flags_unverified_window_and_null_as_of():
    block = bitemporal_provenance(
        {"title": "house rules"},
        as_of_used=None,
        retrieved_at=T,
        snapshot_ref="ref",
    )
    assert block["as_of_used"] is None
    assert block["valid_window_unverified"] is True
    assert block["title"] == "house rules"  # original metadata preserved


def test_provenance_block_requires_aware_retrieved_at():
    with pytest.raises(ValueError, match="tz-aware"):
        bitemporal_provenance({}, as_of_used=None, retrieved_at=T.replace(tzinfo=None), snapshot_ref="ref")
