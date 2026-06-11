"""Tests for the enriched DecisionCase read model (CEN-45 / B1).

Covers the pure ``_serialize_case`` / ``_pii_safe`` serializer that backs
``GET /v1/brain/cases`` — the Decision Card and dashboard summary tiles
(CEN-19 PRD §4.1). No DB: the serializer is exercised against in-memory
DecisionCase fixtures, the same way the kernel constructs them.
"""

from datetime import UTC, datetime

from core.brain.compliance import PIIDetector
from core.brain.patterns.models import (
    CaseOutcome,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ResolutionType,
)
from core.brain.patterns.shadow_verdict import SHADOW_KEY
from services.brain_governance_service import _pii_safe, _serialize_case

_DETECTOR = PIIDetector()


def _case(**overrides) -> DecisionCase:
    base = {
        "stage": "inquiry",
        "scenario": "late_checkout",
        "property_id": "prop-1",
        "owner_id": "owner-1",
        "decision": DecisionAction(action_type=DecisionType.APPROVE),
    }
    base.update(overrides)
    return DecisionCase(**base)


class TestGovernanceFieldExposure:
    def test_outcome_governance_fields_are_exposed(self):
        outcome = CaseOutcome(
            human_overrode=True,
            approval_required=True,
            approved=False,
            successful=False,
            resolution_type=ResolutionType.PM_DENIED,
            revenue_impact=-120.5,
        )
        row = _serialize_case(_case(outcome=outcome), _DETECTOR)

        assert row["human_overrode"] is True
        assert row["approval_required"] is True
        assert row["approved"] is False
        assert row["successful"] is False
        assert row["resolution_type"] == "pm_denied"
        assert row["revenue_impact"] == -120.5

    def test_resolution_type_none_serialises_to_none(self):
        # Fresh case with no recorded outcome — resolution_type is None.
        row = _serialize_case(_case(), _DETECTOR)
        assert row["resolution_type"] is None
        assert row["approved"] is None
        assert row["revenue_impact"] is None

    def test_thin_legacy_fields_preserved(self):
        row = _serialize_case(_case(reservation_id="resv-9"), _DETECTOR)
        assert row["case_id"]
        assert row["stage"] == "inquiry"
        assert row["scenario"] == "late_checkout"
        assert row["property_id"] == "prop-1"
        assert row["decision"] == "approve"
        assert row["conversation_id"] == "resv-9"
        assert "created_at" in row


class TestDecisionAtExposure:
    def test_decision_at_isoformatted_when_present(self):
        when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        row = _serialize_case(_case(decision_at=when), _DETECTOR)
        assert row["decision_at"] == when.isoformat()

    def test_decision_at_none_when_absent(self):
        row = _serialize_case(_case(), _DETECTOR)
        assert row["decision_at"] is None
        # created_at is always present (capture time, not decision time).
        assert row["created_at"] is not None


class TestPiiSafeMessageContext:
    def test_email_is_redacted_in_message_and_response(self):
        case = _case(
            message_text="contact me at guest@example.com please",
            response_text="we replied to guest@example.com",
        )
        row = _serialize_case(case, _DETECTOR)
        assert "guest@example.com" not in row["message_text"]
        assert "guest@example.com" not in row["response_text"]
        # Non-PII context survives so the card is still readable.
        assert "contact me at" in row["message_text"]

    def test_empty_text_stays_empty(self):
        row = _serialize_case(_case(), _DETECTOR)
        assert row["message_text"] == ""
        assert row["response_text"] == ""

    def test_pii_safe_helper_passes_through_clean_text(self):
        assert _pii_safe("just a normal sentence", _DETECTOR) == "just a normal sentence"
        assert _pii_safe("", _DETECTOR) == ""


class TestShadowVerdictExposure:
    """confidence + act/abstain verdict (CEN-33's shadow_verdict, now merged)."""

    def test_verdict_and_confidence_from_shadow_block(self):
        case = _case(
            orchestrator_verdict={
                SHADOW_KEY: {
                    "schema": 1,
                    "verdict": "would_act",
                    "pipeline_verdict": "proceed",
                    "refusing_gate": None,
                    "confidence": 0.83,
                }
            }
        )
        row = _serialize_case(case, _DETECTOR)
        assert row["verdict"] == "would_act"
        assert row["confidence"] == 0.83

    def test_would_abstain_verdict_surfaced(self):
        case = _case(
            orchestrator_verdict={
                SHADOW_KEY: {"verdict": "would_abstain", "confidence": 0.41, "refusing_gate": "compliance"}
            }
        )
        row = _serialize_case(case, _DETECTOR)
        assert row["verdict"] == "would_abstain"
        assert row["confidence"] == 0.41

    def test_pre_capture_rows_default_to_unknown_and_none(self):
        # No orchestrator_verdict (legacy / observe-disabled path).
        row = _serialize_case(_case(), _DETECTOR)
        assert row["verdict"] == "unknown"
        assert row["confidence"] is None
