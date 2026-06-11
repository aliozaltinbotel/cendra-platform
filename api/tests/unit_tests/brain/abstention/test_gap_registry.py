"""Knowledge Gap registry — emission semantics, store, read-time dedup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.brain.abstention.gap_registry import (
    AGGREGATE_RUN_ID_CAP,
    GapRecord,
    GapStatus,
    InMemoryGapStore,
    aggregate_gaps,
    build_gap_record,
    serialize_gap,
)
from core.brain.abstention.models import AbstentionDecision, AbstentionVerdict

T = datetime(2026, 6, 10, 22, 3, 11, tzinfo=UTC)
DISPATCH = T + timedelta(seconds=1)


def _decision(verdict: AbstentionVerdict, *, rationale: str = "wilson_lb=0.41 < threshold=0.60") -> AbstentionDecision:
    return AbstentionDecision(
        tool_id="send_message",
        verdict=verdict,
        model_confidence=0.41,
        wilson_lb=0.41,
        sample_size=44,
        conformal_threshold=0.75,
        rationale=rationale,
    )


def _record(
    *,
    missing_predicate: str = "quiet_hours",
    as_of: datetime = T,
    run_id: str = "run-1",
    status: GapStatus = GapStatus.OPEN,
) -> GapRecord:
    return GapRecord(
        gap_id=f"gap-{run_id}-{as_of.isoformat()}",
        subject_ref="prop-1",
        run_id=run_id,
        query="what are the quiet hours?",
        missing_predicate=missing_predicate,
        confidence=0.41,
        threshold=0.75,
        wilson_lb=0.41,
        as_of=as_of,
        dispatched_at=as_of + timedelta(seconds=1),
        kg_snapshot_ref=f"brain:kg:prop-1@{as_of.isoformat()}",
        status=status,
    )


# ── build_gap_record ─────────────────────────────────────────────── #


def test_abstain_builds_record_with_decision_time_provenance():
    gap = build_gap_record(
        _decision(AbstentionVerdict.ABSTAIN),
        subject_ref="prop-1",
        run_id="run-1",
        query="what are the quiet hours?",
        as_of=T,
        dispatched_at=DISPATCH,
    )
    assert gap is not None
    assert gap.subject_ref == "prop-1"
    assert gap.as_of == T
    assert gap.dispatched_at == DISPATCH
    assert gap.kg_snapshot_ref == f"brain:kg:prop-1@{T.isoformat()}"
    assert gap.status is GapStatus.OPEN
    # no structured predicate supplied → falls back to the rationale
    assert gap.missing_predicate == "wilson_lb=0.41 < threshold=0.60"
    assert gap.confidence == pytest.approx(0.41)
    assert gap.threshold == pytest.approx(0.75)


def test_structured_predicate_wins_over_rationale():
    gap = build_gap_record(
        _decision(AbstentionVerdict.ABSTAIN),
        subject_ref="prop-1",
        run_id="run-1",
        query="q",
        as_of=T,
        dispatched_at=DISPATCH,
        missing_predicate="quiet_hours",
    )
    assert gap is not None
    assert gap.missing_predicate == "quiet_hours"


@pytest.mark.parametrize("verdict", [AbstentionVerdict.PROCEED, AbstentionVerdict.INSUFFICIENT_DATA])
def test_non_abstain_verdicts_emit_nothing(verdict):
    # INSUFFICIENT_DATA is a calibration shortfall, not a knowledge gap
    assert (
        build_gap_record(
            _decision(verdict, rationale="only 3 sample(s); min_samples=30"),
            subject_ref="prop-1",
            run_id="run-1",
            query="q",
            as_of=T,
            dispatched_at=DISPATCH,
        )
        is None
    )


def test_record_validation():
    with pytest.raises(ValueError, match="subject_ref"):
        GapRecord(
            gap_id="g",
            subject_ref="",
            run_id="r",
            query="q",
            missing_predicate="p",
            confidence=0.5,
            threshold=None,
            wilson_lb=0.4,
            as_of=T,
            dispatched_at=DISPATCH,
            kg_snapshot_ref="ref",
        )
    with pytest.raises(ValueError, match="tz-aware"):
        GapRecord(
            gap_id="g",
            subject_ref="s",
            run_id="r",
            query="q",
            missing_predicate="p",
            confidence=0.5,
            threshold=None,
            wilson_lb=0.4,
            as_of=T.replace(tzinfo=None),
            dispatched_at=DISPATCH,
            kg_snapshot_ref="ref",
        )


# ── InMemoryGapStore ─────────────────────────────────────────────── #


def test_store_lists_newest_first_and_filters_status():
    store = InMemoryGapStore()
    older = _record(as_of=T - timedelta(days=1), run_id="run-0")
    newer = _record(run_id="run-1")
    store.record(older)
    store.record(newer)
    rows = store.list_for("prop-1")
    assert [r.run_id for r in rows] == ["run-1", "run-0"]
    assert store.list_for("prop-1", status=GapStatus.ANSWERED) == ()
    assert store.list_for("other") == ()


def test_store_lifecycle_is_predicate_grained():
    store = InMemoryGapStore()
    store.record(_record(run_id="run-0", as_of=T - timedelta(days=1)))
    store.record(_record(run_id="run-1"))
    store.record(_record(run_id="run-2", missing_predicate="parking_rules"))
    changed = store.mark_status(subject_ref="prop-1", missing_predicate="quiet_hours", status=GapStatus.ANSWERED)
    assert changed == 2
    answered = store.list_for("prop-1", status=GapStatus.ANSWERED)
    assert {r.run_id for r in answered} == {"run-0", "run-1"}
    still_open = store.list_for("prop-1", status=GapStatus.OPEN)
    assert {r.run_id for r in still_open} == {"run-2"}
    # idempotent: nothing left to transition
    assert store.mark_status(subject_ref="prop-1", missing_predicate="quiet_hours", status=GapStatus.ANSWERED) == 0


# ── aggregate_gaps (read-API dedup, ruling §E2) ──────────────────── #


def test_aggregate_dedups_per_predicate_with_history():
    rows = [
        _record(run_id="run-0", as_of=T - timedelta(days=2)),
        _record(run_id="run-1", as_of=T - timedelta(days=1)),
        _record(run_id="run-2", as_of=T),
        _record(run_id="run-3", missing_predicate="parking_rules", as_of=T - timedelta(hours=1)),
    ]
    cards = aggregate_gaps(rows)
    assert len(cards) == 2
    quiet = cards[0]  # newest last_seen_at first
    assert quiet["missing_predicate"] == "quiet_hours"
    assert quiet["occurrences"] == 3
    assert quiet["first_seen_at"] == (T - timedelta(days=2)).isoformat()
    assert quiet["last_seen_at"] == T.isoformat()
    # scalar fields come from the latest occurrence
    assert quiet["run_id"] == "run-2"
    assert quiet["run_ids"] == ["run-2", "run-1", "run-0"]
    parking = cards[1]
    assert parking["occurrences"] == 1
    assert parking["run_ids"] == ["run-3"]


def test_aggregate_caps_run_id_sample():
    rows = [_record(run_id=f"run-{i}", as_of=T + timedelta(seconds=i)) for i in range(AGGREGATE_RUN_ID_CAP + 5)]
    (card,) = aggregate_gaps(rows)
    assert card["occurrences"] == AGGREGATE_RUN_ID_CAP + 5
    assert len(card["run_ids"]) == AGGREGATE_RUN_ID_CAP
    assert card["run_ids"][0] == f"run-{AGGREGATE_RUN_ID_CAP + 4}"


def test_serialize_gap_wire_shape():
    payload = serialize_gap(_record())
    assert payload["subject_ref"] == "prop-1"
    assert payload["status"] == "open"
    assert payload["as_of"] == T.isoformat()
    assert set(payload) == {
        "gap_id",
        "subject_ref",
        "run_id",
        "query",
        "missing_predicate",
        "confidence",
        "threshold",
        "wilson_lb",
        "as_of",
        "dispatched_at",
        "kg_snapshot_ref",
        "status",
    }
