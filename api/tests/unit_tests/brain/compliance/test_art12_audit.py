"""Art. 12 audit backend semantics for durable receipt storage."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from core.brain.compliance.art12_audit import SQLAlchemyArt12AuditLogger
from core.brain.compliance.art12_decision import (
    ART12_GENESIS_DIGEST,
    Art12Decision,
    HandlerSolver,
)
from models.brain_receipt import BrainArt12Receipt

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def session_maker():
    engine = create_engine("sqlite:///:memory:")
    BrainArt12Receipt.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _record(**overrides: object) -> Art12Decision:
    base: dict[str, object] = {
        "decision_id": "d1",
        "occurred_at": datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
        "property_id": "p1",
        "owner_id": "o1",
        "action_kind": "send_message",
        "handler_solver": HandlerSolver.LLM,
        "rationale": "reply to guest",
        "provenance_digest": "ab" * 32,
    }
    base.update(overrides)
    return Art12Decision(**base)  # type: ignore[arg-type]


def test_append_persists_linear_chain_and_recovers_tail_after_restart(session_maker) -> None:
    first_logger = SQLAlchemyArt12AuditLogger(session_maker=session_maker, tenant_id=TENANT)
    first = _record(decision_id="d1")
    first_digest = first_logger.append(first)

    restarted = SQLAlchemyArt12AuditLogger(session_maker=session_maker, tenant_id=TENANT)
    assert restarted.last_digest() == first_digest

    second = _record(
        decision_id="d2",
        occurred_at=datetime(2026, 6, 11, 12, 1, tzinfo=UTC),
        prev_digest=first_digest,
    )
    second_digest = restarted.append(second)
    assert restarted.last_digest() == second_digest

    with session_maker() as session:
        rows = (
            session.execute(
                select(BrainArt12Receipt)
                .where(BrainArt12Receipt.tenant_id == TENANT)
                .order_by(BrainArt12Receipt.created_at.asc(), BrainArt12Receipt.id.asc())
            )
            .scalars()
            .all()
        )

    assert [row.decision_id for row in rows] == ["d1", "d2"]
    assert rows[0].prev_digest == ART12_GENESIS_DIGEST
    assert rows[0].record_digest == first_digest
    assert rows[1].prev_digest == first_digest
    assert rows[1].record_digest == second_digest


def test_identical_retry_is_idempotent_on_decision_id(session_maker) -> None:
    logger = SQLAlchemyArt12AuditLogger(session_maker=session_maker, tenant_id=TENANT)
    record = _record()

    first = logger.append(record)
    second = logger.append(record)

    assert first == second
    assert logger.last_digest() == first
    with session_maker() as session:
        rows = session.execute(
            select(BrainArt12Receipt).where(BrainArt12Receipt.tenant_id == TENANT)
        ).scalars().all()
    assert len(rows) == 1


def test_conflicting_retry_same_decision_id_is_rejected(session_maker) -> None:
    logger = SQLAlchemyArt12AuditLogger(session_maker=session_maker, tenant_id=TENANT)
    logger.append(_record())

    with pytest.raises(ValueError, match="decision_id already recorded"):
        logger.append(_record(rationale="different rationale"))


def test_prev_digest_mismatch_is_rejected(session_maker) -> None:
    logger = SQLAlchemyArt12AuditLogger(session_maker=session_maker, tenant_id=TENANT)
    logger.append(_record(decision_id="d1"))

    with pytest.raises(ValueError, match="prev_digest mismatch"):
        logger.append(
            _record(
                decision_id="d2",
                occurred_at=datetime(2026, 6, 11, 12, 2, tzinfo=UTC),
                prev_digest="ff" * 32,
            )
        )


def test_tenant_tails_are_isolated(session_maker) -> None:
    a = SQLAlchemyArt12AuditLogger(session_maker=session_maker, tenant_id=TENANT)
    b = SQLAlchemyArt12AuditLogger(session_maker=session_maker, tenant_id=OTHER_TENANT)

    a_digest = a.append(_record(decision_id="d1"))
    later = datetime(2026, 6, 11, 12, 5, tzinfo=UTC)
    b_digest = b.append(_record(decision_id="d1", occurred_at=later))

    assert a.last_digest() == a_digest
    assert b.last_digest() == b_digest
    assert a_digest != ART12_GENESIS_DIGEST
    assert b_digest != ART12_GENESIS_DIGEST
