"""ReceiptEmitter semantics at the gate chain's PROCEED seam (CEN-81)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from core.brain.certificates.receipt import canonical_receipt_payload
from core.brain.compliance.art12_audit import InMemoryArt12AuditLogger
from core.brain.compliance.receipt_emitter import (
    EXTRA_GATE_TRACE_KEY,
    EXTRA_MODEL_CONFIDENCE_KEY,
    ReceiptEmitter,
)
from core.brain.gates import GateName, GateOutcome, PipelineRequest

TENANT = "11111111-1111-1111-1111-111111111111"

MOMENT = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)

TRACE = (
    GateOutcome(gate=GateName.COMPLIANCE, verdict="ok", rationale="compliant"),
    GateOutcome(gate=GateName.ABSTENTION, verdict="proceed", rationale="wilson_lb above threshold"),
    GateOutcome(gate=GateName.RISK, verdict="proceed", rationale="cvar within bound"),
)


class _FakeSigner:
    """Custody-shaped signer returning public metadata only."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []

    def sign_receipt(self, tenant_id: str, payload: bytes | bytearray) -> dict[str, str]:
        self.calls.append((tenant_id, bytes(payload)))
        return {
            "key_id": "brk_ed25519_test",
            "algorithm": "Ed25519",
            "signature_hex": "ab" * 64,
        }


class _ExplodingLogger:
    def append(self, record):  # pragma: no cover - unused
        raise RuntimeError("boom")

    def append_envelope(self, envelope):
        raise RuntimeError("db down")

    def get_envelope(self, decision_id):
        return None

    def stitch_outcome(self, decision_id, *, case_id, outcome_status):
        return False

    def last_digest(self):
        return "0" * 64


def _request(**overrides) -> PipelineRequest:
    base = {
        "decision_id": "d-1",
        "property_id": "p-1",
        "owner_id": "o-1",
        "action_kind": "send_message",
        "rationale": "reply to guest",
        "provenance_digest": "ab" * 32,
        "tool_id": "send_message",
        "model_confidence": 0.9,
        "handler_solver": "llm",
    }
    base.update(overrides)
    return PipelineRequest(**base)


def _emitter(logger=None, signer=None) -> ReceiptEmitter:
    return ReceiptEmitter(
        tenant_id=TENANT,
        audit_logger=logger if logger is not None else InMemoryArt12AuditLogger(tenant_id=TENANT),
        signer_provider=(lambda: signer) if signer is not None else None,
    )


def test_build_seals_record_with_trace_and_confidence_in_signed_bytes():
    envelope = _emitter().build(_request(), MOMENT, TRACE)
    assert envelope is not None
    assert envelope.record.decision_id == "d-1"

    payload = canonical_receipt_payload(envelope.record)
    extra = envelope.record.extra
    assert json.loads(extra[EXTRA_GATE_TRACE_KEY]) == [
        {"gate": row.gate.value, "verdict": row.verdict, "rationale": row.rationale} for row in TRACE
    ]
    assert extra[EXTRA_MODEL_CONFIDENCE_KEY] == "0.900000"
    # the context is inside the bytes the signature / digest cover
    assert b"wilson_lb above threshold" in payload
    assert b"0.900000" in payload


def test_unsigned_without_signer_signed_with_signer():
    unsigned = _emitter().build(_request(), MOMENT, TRACE)
    assert unsigned is not None
    assert unsigned.signed is False
    assert unsigned.key_id is None

    signer = _FakeSigner()
    signed = _emitter(signer=signer).build(_request(), MOMENT, TRACE)
    assert signed is not None
    assert signed.signed is True
    assert signed.key_id == "brk_ed25519_test"
    # custody signed exactly the canonical payload
    assert signer.calls == [(TENANT, canonical_receipt_payload(signed.record))]


def test_emission_appends_to_chain_durably():
    logger = InMemoryArt12AuditLogger(tenant_id=TENANT)
    emitter = _emitter(logger=logger)
    first = emitter.build(_request(decision_id="d-1"), MOMENT, TRACE)
    second = emitter.build(_request(decision_id="d-2"), MOMENT, TRACE)
    assert first is not None
    assert second is not None
    assert second.record.prev_digest == first.record_digest
    assert logger.last_digest() == second.record_digest


def test_replay_by_decision_id_is_idempotent():
    logger = InMemoryArt12AuditLogger(tenant_id=TENANT)
    emitter = _emitter(logger=logger)
    first = emitter.build(_request(), MOMENT, TRACE)
    replay = emitter.build(_request(), MOMENT, TRACE)
    assert first is not None
    assert replay is not None
    assert replay.record_digest == first.record_digest
    assert logger.last_digest() == first.record_digest  # chain did not fork


def test_emission_failure_is_fail_open():
    envelope = _emitter(logger=_ExplodingLogger()).build(_request(), MOMENT, TRACE)
    assert envelope is None  # logged, dispatch unaffected


def test_request_extra_travels_but_reserved_keys_win():
    request = _request(extra={"channel": "whatsapp", EXTRA_MODEL_CONFIDENCE_KEY: "tampered"})
    envelope = _emitter().build(request, MOMENT, TRACE)
    assert envelope is not None
    assert envelope.record.extra["channel"] == "whatsapp"
    assert envelope.record.extra[EXTRA_MODEL_CONFIDENCE_KEY] == "0.900000"
