"""Pack loader behaviour over the real hospitality pack."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.brain.certificates.tier import AutonomyTier
from core.brain.packs import load_pack, seed_workflow_kinds
from core.brain.patterns.blockers import BlockerSeverity
from models.brain_autonomy import BrainWorkflowKind

PACK_DIR = Path(__file__).resolve().parents[4] / "packs" / "hospitality"
TENANT = "11111111-1111-1111-1111-111111111111"


def test_hospitality_pack_loads_every_surface():
    pack = load_pack(PACK_DIR)
    assert pack.name == "hospitality"
    assert pack.tier_policy.ceiling_for("issue_refund") is AutonomyTier.APPROVER
    assert "send_welcome_message" in pack.approval_policy.auto_approve_actions
    assert pack.blocker_severity["ops_unresolved"] is BlockerSeverity.SOFT
    assert pack.blocker_actions["payment_incomplete"] == ("send_access_code", "late_checkout")
    assert "noise_complaint" in pack.incident_event_types
    assert pack.scenario_features["late_checkout"].pms_keys is not None
    assert "discount_request" in pack.scenarios
    registry = pack.workflow_kind_registry()
    assert registry.resolve_event("send_access_code") == "code_release"


def test_hospitality_pack_parses_workflow_kind_labels():
    pack = load_pack(PACK_DIR)
    # operator-facing copy ratified against the journey vocabulary (CEN-51)
    assert pack.workflow_kind_labels["code_release"] == "Access Code Delivery"
    assert pack.workflow_kind_labels["early_checkin"] == "Early Check-in"
    assert pack.workflow_kind_labels["orphan_night"] == "Orphan Night"
    assert pack.workflow_kind_labels["pattern_promotion"] == "Autonomy Review"
    assert pack.workflow_kind_labels["inquiry_reply"] == "Inquiry Reply"
    # every kind in the hospitality pack ships a label
    assert set(pack.workflow_kind_labels) == set(pack.workflow_kind_aliases)
    # the in-memory registry exposes them, defaulting to the kind when absent
    labels = pack.workflow_kind_registry().labels()
    assert labels["code_release"] == "Access Code Delivery"


def test_seed_workflow_kinds_idempotent():
    engine = create_engine("sqlite:///:memory:")
    BrainWorkflowKind.__table__.create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    pack = load_pack(PACK_DIR)
    first = seed_workflow_kinds(pack, session_maker=sessions, tenant_id=TENANT)
    second = seed_workflow_kinds(pack, session_maker=sessions, tenant_id=TENANT)
    assert first == len(pack.workflow_kind_aliases)
    assert second == 0
    with sessions() as session:
        assert session.query(BrainWorkflowKind).count() == first
        row = session.query(BrainWorkflowKind).filter_by(kind="code_release").one()
        assert row.label == "Access Code Delivery"
    engine.dispose()


def test_missing_pack_dir_rejected():
    with pytest.raises(ValueError, match="pack directory"):
        load_pack("/nonexistent/pack")
