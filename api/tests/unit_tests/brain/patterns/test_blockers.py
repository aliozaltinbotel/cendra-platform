"""Blocker engine + store behaviour.

Written at port time — the reference has no blocker tests.  Pins the
engine lifecycle (check / create / resolve / auto-detect), the injected
pack-default mappings, and the persistent store contract.  The
``_hospitality_detector`` below preserves the reference's
``BlockerEngine._detect_violations`` logic verbatim (it is vertical
content and left the kernel; it lives here as the executable example
until the Batch 6 pack-behaviour design).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.brain.patterns.blocker_store import SQLAlchemyBlockerStore
from core.brain.patterns.blockers import (
    Blocker,
    BlockerEngine,
    BlockerSeverity,
    InMemoryBlockerStore,
)
from models.brain_blockers import BrainBlocker

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"

# Pack-equivalent defaults (mirrors packs/hospitality/blockers.yaml)
PACK_SEVERITY: Mapping[str, BlockerSeverity] = {
    "guest_count_unconfirmed": BlockerSeverity.HARD,
    "payment_incomplete": BlockerSeverity.HARD,
    "id_unverified": BlockerSeverity.HARD,
    "ops_unresolved": BlockerSeverity.SOFT,
    "cleaning_incomplete": BlockerSeverity.HARD,
    "damage_uninspected": BlockerSeverity.SOFT,
}
PACK_ACTIONS: Mapping[str, tuple[str, ...]] = {
    "guest_count_unconfirmed": ("send_access_code",),
    "payment_incomplete": ("send_access_code", "late_checkout"),
    "id_unverified": ("send_access_code",),
    "approval_pending": ("charge_guest", "submit_damage_claim", "offer_discount"),
    "cleaning_incomplete": ("send_access_code",),
}


def _hospitality_detector(
    pms_data: Mapping[str, Any],
    ops_data: Mapping[str, Any],
    existing_types: set[str],
) -> list[tuple[str, str]]:
    """Reference ``_detect_violations`` logic, preserved verbatim."""
    results: list[tuple[str, str]] = []

    guests = int(pms_data.get("adults", 0) or 0)
    if guests == 0 and "guest_count_unconfirmed" not in existing_types:
        results.append(
            (
                "guest_count_unconfirmed",
                "Guest count is 0 or missing — must be confirmed before releasing access codes.",
            )
        )

    payment = str(pms_data.get("payment_status", "")).lower()
    if payment not in {"paid", "completed"} and "payment_incomplete" not in existing_types:
        results.append(
            (
                "payment_incomplete",
                f"Payment status is '{payment}' — must be completed before access code release.",
            )
        )

    id_verified = pms_data.get("id_verified", False)
    if not id_verified and "id_unverified" not in existing_types:
        results.append(
            (
                "id_unverified",
                "Guest identity not verified — required before access code release.",
            )
        )

    cleaning = str(ops_data.get("cleaning_status", "")).lower()
    not_clean = cleaning and cleaning not in {"completed", "done", "clean"}
    if not_clean and "cleaning_incomplete" not in existing_types:
        results.append(
            (
                "cleaning_incomplete",
                f"Cleaning status is '{cleaning}' — property not ready for guest entry.",
            )
        )

    return results


@pytest.fixture
def engine() -> BlockerEngine:
    return BlockerEngine(
        InMemoryBlockerStore(),
        default_severity=PACK_SEVERITY,
        default_actions=PACK_ACTIONS,
        detector=_hospitality_detector,
    )


class TestBlockerModel:
    def test_resolution_state(self):
        blocker = Blocker(blocker_type="payment_incomplete", property_id="p1", description="x")
        assert blocker.is_active
        assert not blocker.is_resolved
        assert blocker.is_hard
        assert blocker.blocks_action("send_access_code") is False  # no actions configured

    def test_empty_type_rejected(self):
        with pytest.raises(ValueError, match="blocker_type"):
            Blocker(blocker_type="", property_id="p1", description="x")


class TestEngineLifecycle:
    def test_create_uses_pack_defaults(self, engine):
        blocker = engine.create_blocker(
            blocker_type="payment_incomplete",
            property_id="p1",
            description="unpaid",
        )
        assert blocker.severity is BlockerSeverity.HARD
        assert blocker.blocks_actions == ("send_access_code", "late_checkout")

    def test_create_unknown_type_falls_back_hard_no_actions(self, engine):
        blocker = engine.create_blocker(
            blocker_type="anything_else",
            property_id="p1",
            description="x",
        )
        assert blocker.severity is BlockerSeverity.HARD
        assert blocker.blocks_actions == ()

    def test_check_and_hard_blocker(self, engine):
        engine.create_blocker(
            blocker_type="payment_incomplete",
            property_id="p1",
            reservation_id="r1",
            description="unpaid",
        )
        engine.create_blocker(
            blocker_type="damage_uninspected",
            property_id="p1",
            reservation_id="r1",
            description="soft one",
            blocks_actions=("send_access_code",),
        )
        blocking = engine.check_blockers("p1", "r1", "send_access_code")
        assert len(blocking) == 2
        assert engine.has_hard_blocker("p1", "r1", "send_access_code") is True
        assert engine.has_hard_blocker("p1", "r1", "charge_guest") is False
        assert engine.check_blockers("p2", "r1", "send_access_code") == []

    def test_resolve_blocker(self, engine):
        blocker = engine.create_blocker(
            blocker_type="id_unverified",
            property_id="p1",
            description="no id",
        )
        assert engine.resolve_blocker(blocker.blocker_id, resolved_by="pm:7") is True
        # idempotent on already-resolved, False on missing
        assert engine.resolve_blocker(blocker.blocker_id, resolved_by="pm:7") is True
        assert engine.resolve_blocker("missing", resolved_by="pm:7") is False
        assert engine.get_active_blockers("p1") == []


class TestAutoDetect:
    def test_detects_all_four_violations(self, engine):
        created = engine.auto_detect_blockers(
            property_id="p1",
            reservation_id="r1",
            pms_data={"adults": 0, "payment_status": "pending", "id_verified": False},
            ops_data={"cleaning_status": "in_progress"},
        )
        types = {b.blocker_type for b in created}
        assert types == {
            "guest_count_unconfirmed",
            "payment_incomplete",
            "id_unverified",
            "cleaning_incomplete",
        }
        # detection is idempotent against existing active blockers
        again = engine.auto_detect_blockers(
            property_id="p1",
            reservation_id="r1",
            pms_data={"adults": 0, "payment_status": "pending", "id_verified": False},
            ops_data={"cleaning_status": "in_progress"},
        )
        assert again == []

    def test_clean_reservation_creates_nothing(self, engine):
        created = engine.auto_detect_blockers(
            property_id="p1",
            pms_data={"adults": 2, "payment_status": "paid", "id_verified": True},
            ops_data={"cleaning_status": "completed"},
        )
        assert created == []

    def test_no_detector_is_a_noop(self):
        engine = BlockerEngine(InMemoryBlockerStore())
        assert (
            engine.auto_detect_blockers(
                property_id="p1",
                pms_data={"adults": 0},
            )
            == []
        )


class TestSQLAlchemyStore:
    @pytest.fixture
    def session_maker(self):
        engine = create_engine("sqlite:///:memory:")
        BrainBlocker.__table__.create(engine)
        yield sessionmaker(bind=engine, expire_on_commit=False)
        engine.dispose()

    @pytest.fixture
    def store(self, session_maker) -> SQLAlchemyBlockerStore:
        return SQLAlchemyBlockerStore(session_maker=session_maker, tenant_id=TENANT)

    def test_round_trip_and_resolution_update(self, store):
        blocker = Blocker(
            blocker_type="payment_incomplete",
            property_id="p1",
            reservation_id="r1",
            description="unpaid balance",
            severity=BlockerSeverity.SOFT,
            blocks_actions=("send_access_code",),
            metadata={"amount_due": 120},
        )
        store.save(blocker)
        loaded = store.get(blocker.blocker_id)
        assert loaded is not None
        assert loaded.blocker_type == "payment_incomplete"
        assert loaded.severity is BlockerSeverity.SOFT
        assert loaded.blocks_actions == ("send_access_code",)
        assert loaded.metadata == {"amount_due": 120}
        assert loaded.created_at.tzinfo is not None
        # engine path: resolution rewrites the row
        engine = BlockerEngine(store)
        assert engine.resolve_blocker(blocker.blocker_id, resolved_by="system") is True
        resolved = store.get(blocker.blocker_id)
        assert resolved.is_resolved
        assert resolved.resolved_by == "system"
        assert store.get_active("p1") == []

    def test_get_active_strict_reservation_match(self, store):
        prop_wide = Blocker(blocker_type="t1", property_id="p1", description="x")
        for_res = Blocker(blocker_type="t2", property_id="p1", reservation_id="r1", description="y")
        store.save(prop_wide)
        store.save(for_res)
        # reference parity: reservation filter is a strict equality match
        active = store.get_active("p1", "r1")
        assert [b.blocker_id for b in active] == [for_res.blocker_id]
        assert len(store.get_active("p1")) == 2

    def test_tenant_isolation(self, session_maker):
        a = SQLAlchemyBlockerStore(session_maker=session_maker, tenant_id=TENANT)
        b = SQLAlchemyBlockerStore(session_maker=session_maker, tenant_id=OTHER_TENANT)
        blocker = Blocker(blocker_type="t1", property_id="p1", description="x")
        a.save(blocker)
        assert b.get(blocker.blocker_id) is None
        assert b.get_active("p1") == []

    def test_empty_tenant_rejected(self, session_maker):
        with pytest.raises(ValueError, match="tenant_id"):
            SQLAlchemyBlockerStore(session_maker=session_maker, tenant_id="")
