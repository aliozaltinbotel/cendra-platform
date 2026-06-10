"""Tests for the Sprint I foundation store.

Pins three guarantees:

* :class:`ScenarioFoundation` rejects malformed rows at construction
  time so an upstream bug cannot silently land an out-of-range
  importance in storage.
* :class:`InMemoryFoundationStore` upserts on the natural key
  ``(property_id, scenario, feature_name)`` and returns rows sorted
  by importance descending — the order consumers depend on.
* The store implements :class:`FoundationStore` Protocol so the
  Postgres successor can be swapped in without touching call sites.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brain_engine.patterns.foundation_store import (
    FoundationStore,
    InMemoryFoundationStore,
    ScenarioFoundation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    *,
    property_id: str = "p1",
    scenario: str = "access_code_release",
    feature_name: str = "hours_before_checkin",
    importance: float = 0.5,
    sample_count: int = 10,
    computed_at: datetime | None = None,
) -> ScenarioFoundation:
    return ScenarioFoundation(
        property_id=property_id,
        scenario=scenario,
        feature_name=feature_name,
        importance=importance,
        sample_count=sample_count,
        computed_at=computed_at
        or datetime(2026, 5, 7, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# ScenarioFoundation validation
# ---------------------------------------------------------------------------


def test_value_object_is_frozen() -> None:
    row = _row()
    with pytest.raises(Exception):
        row.importance = 0.9  # type: ignore[misc]


@pytest.mark.parametrize("importance", [-0.1, 1.0001, 2.0])
def test_value_object_rejects_out_of_range_importance(
    importance: float,
) -> None:
    with pytest.raises(ValueError, match=r"\[0.0, 1.0\]"):
        _row(importance=importance)


def test_value_object_rejects_negative_sample_count() -> None:
    with pytest.raises(ValueError, match="sample_count"):
        _row(sample_count=-1)


def test_value_object_rejects_empty_property_id() -> None:
    with pytest.raises(ValueError, match="property_id"):
        _row(property_id="")


def test_value_object_rejects_empty_scenario() -> None:
    with pytest.raises(ValueError, match="scenario"):
        _row(scenario="")


def test_value_object_rejects_empty_feature_name() -> None:
    with pytest.raises(ValueError, match="feature_name"):
        _row(feature_name="")


# ---------------------------------------------------------------------------
# InMemoryFoundationStore behaviour
# ---------------------------------------------------------------------------


async def test_get_returns_empty_for_unknown_pair() -> None:
    store = InMemoryFoundationStore()
    rows = await store.get(property_id="ghost", scenario="x")
    assert rows == ()


async def test_upsert_many_writes_rows() -> None:
    store = InMemoryFoundationStore()
    written = await store.upsert_many([_row(), _row(feature_name="adults")])
    assert written == 2
    rows = await store.get(
        property_id="p1", scenario="access_code_release",
    )
    assert len(rows) == 2


async def test_get_returns_rows_sorted_by_importance_desc() -> None:
    store = InMemoryFoundationStore()
    await store.upsert_many(
        [
            _row(feature_name="a", importance=0.10),
            _row(feature_name="b", importance=0.80),
            _row(feature_name="c", importance=0.40),
        ]
    )
    rows = await store.get(
        property_id="p1", scenario="access_code_release",
    )
    assert [r.feature_name for r in rows] == ["b", "c", "a"]


async def test_upsert_replaces_existing_natural_key() -> None:
    """Same (property_id, scenario, feature_name) overwrites in place."""
    store = InMemoryFoundationStore()
    await store.upsert_many([_row(importance=0.10)])
    await store.upsert_many([_row(importance=0.99)])
    rows = await store.get(
        property_id="p1", scenario="access_code_release",
    )
    assert len(rows) == 1
    assert rows[0].importance == pytest.approx(0.99)


async def test_get_isolates_property_id() -> None:
    store = InMemoryFoundationStore()
    await store.upsert_many(
        [
            _row(property_id="p1"),
            _row(property_id="p2"),
        ]
    )
    rows_p1 = await store.get(
        property_id="p1", scenario="access_code_release",
    )
    rows_p2 = await store.get(
        property_id="p2", scenario="access_code_release",
    )
    assert len(rows_p1) == 1
    assert len(rows_p2) == 1
    assert rows_p1[0].property_id == "p1"
    assert rows_p2[0].property_id == "p2"


async def test_get_isolates_scenario() -> None:
    store = InMemoryFoundationStore()
    await store.upsert_many(
        [
            _row(scenario="access_code_release"),
            _row(scenario="early_checkin"),
        ]
    )
    rows = await store.get(
        property_id="p1", scenario="access_code_release",
    )
    assert len(rows) == 1
    assert rows[0].scenario == "access_code_release"


async def test_snapshot_returns_independent_copy() -> None:
    store = InMemoryFoundationStore()
    await store.upsert_many([_row()])
    snap = await store.snapshot()
    assert len(snap) == 1
    # Mutating the snapshot must not leak into the store.
    snap.clear()  # type: ignore[attr-defined]
    rows = await store.get(
        property_id="p1", scenario="access_code_release",
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Protocol recognition
# ---------------------------------------------------------------------------


def test_in_memory_store_satisfies_protocol() -> None:
    assert isinstance(InMemoryFoundationStore(), FoundationStore)
