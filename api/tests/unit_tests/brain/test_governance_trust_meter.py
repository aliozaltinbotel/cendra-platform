"""Trust-meter read emits operator-facing workflow labels (CEN-50).

Covers ``BrainGovernanceService.trust_meter``: each band carries a
never-null ``label`` (kind fallback when none is set) and the response
carries a top-level ``labels`` map over all enabled kinds. The service is
exercised against SQLite with ``_session_maker`` patched to the test
engine, so the full SQLAlchemy registry/store path runs (no mocks).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import services.brain_governance_service as gov
from models.brain_autonomy import BrainWorkflowAutonomy, BrainWorkflowKind

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainWorkflowAutonomy.__table__.create(engine)
    BrainWorkflowKind.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def service(session_maker, monkeypatch):
    # one kind with a seeded label, one without (must fall back to kind)
    with session_maker() as session:
        session.add(
            BrainWorkflowKind(
                tenant_id=TENANT,
                kind="code_release",
                event_aliases=["send_access_code"],
                label="Access Code Release",
            )
        )
        session.add(BrainWorkflowKind(tenant_id=TENANT, kind="late_checkout"))
        session.commit()
    monkeypatch.setattr(gov, "_session_maker", lambda: session_maker)
    return gov.BrainGovernanceService(tenant_id=TENANT)


class TestTrustMeterLabels:
    def test_bands_carry_label_with_kind_fallback(self, service):
        payload = service.trust_meter("p-123")
        bands = {b["workflow"]: b for b in payload["bands"]}
        # every enabled kind collapses into an OBSERVE band even with no runs
        assert set(bands) == {"code_release", "late_checkout"}
        # wire value stays the stable kind id
        assert bands["code_release"]["workflow"] == "code_release"
        # NEW: operator-facing label, never null
        assert bands["code_release"]["label"] == "Access Code Release"
        # no label set -> label equals the kind string (never null)
        assert bands["late_checkout"]["label"] == "late_checkout"

    def test_top_level_labels_map_covers_all_kinds(self, service):
        payload = service.trust_meter("p-123")
        assert payload["labels"] == {
            "code_release": "Access Code Release",
            "late_checkout": "late_checkout",
        }
