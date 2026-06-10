"""Tests for the parsed-foundation-catalog persistence (FL-01).

Pins the contract for :class:`FoundationCatalogStore`:

* The Protocol is satisfied by
  :class:`InMemoryFoundationCatalogStore` (verified via
  ``runtime_checkable``) so concrete consumers can depend on the
  Protocol type alone.
* ``upsert_many`` is idempotent — re-upserting the same catalog
  with the same ``doc_hash`` skips the write loop.
* ``upsert_many`` writes when the hash changes.
* ``get`` returns ``None`` for unknown ids instead of raising.
* ``list_all`` returns rows ordered by ``(stage_number,
  scenario_id)`` for deterministic snapshots.
* The serialise/deserialise round-trip on the Postgres helper
  preserves every field that :class:`FoundationScenario` carries —
  no silent data loss between Python and the JSONB payload.
"""

from __future__ import annotations

import json

import pytest

from brain_engine.patterns.foundation_catalog_store import (
    FoundationCatalogStore,
    InMemoryFoundationCatalogStore,
    UpsertResult,
    _payload_to_scenario,
    _scenario_to_payload,
)
from brain_engine.patterns.foundation_registry import FoundationScenario

# ── fixtures ──────────────────────────────────────────────── #


def _make_scenario(
    *,
    scenario_id: str = "s1_1_demo",
    stage_number: int = 1,
    title: str = "Demo scenario",
    risk_level: str = "Low",
    memory_types: tuple[str, ...] = ("Property knowledge",),
) -> FoundationScenario:
    return FoundationScenario(
        scenario_id=scenario_id,
        title=title,
        stage_number=stage_number,
        stage_label="Pre-Booking / Inquiry",
        trigger="Demo trigger body.",
        risk_level=risk_level,
        ai_default_behavior="Handle politely.",
        required_data_checks=("availability", "pricing rule"),
        signals_to_inspect=("urgency", "time of day"),
        should_auto_reply="Yes",
        should_escalate_to_pm="No",
        should_create_task="Conditional",
        should_learn_pattern="Yes",
        pattern_to_learn="PM tone preferences.",
        example_learned_pattern="PM uses warm wording for inquiries.",
        memory_types=memory_types,
        what_not_to_learn="Do not infer guest intent beyond evidence.",
        future_behavior_impact="Cendra responds faster on warm topics.",
    )


# ── Protocol compatibility ────────────────────────────────── #


def test_in_memory_store_satisfies_protocol() -> None:
    """The in-memory implementation satisfies the runtime Protocol."""
    store = InMemoryFoundationCatalogStore()
    assert isinstance(store, FoundationCatalogStore)


# ── upsert_many behaviour ─────────────────────────────────── #


@pytest.mark.asyncio
async def test_upsert_writes_rows_on_first_call() -> None:
    """A fresh store records every scenario passed in."""
    store = InMemoryFoundationCatalogStore()
    scenarios = (
        _make_scenario(scenario_id="s1_1_a"),
        _make_scenario(scenario_id="s1_2_b"),
    )
    result = await store.upsert_many(scenarios, doc_hash="hash1")
    assert isinstance(result, UpsertResult)
    assert result.upserted == 2
    assert result.skipped_reason == ""
    rows = await store.list_all()
    assert {row.scenario_id for row in rows} == {"s1_1_a", "s1_2_b"}


@pytest.mark.asyncio
async def test_upsert_is_idempotent_for_matching_hash() -> None:
    """Re-upserting with the same hash is a no-op."""
    store = InMemoryFoundationCatalogStore()
    scenarios = (_make_scenario(),)
    await store.upsert_many(scenarios, doc_hash="hash1")
    repeat = await store.upsert_many(scenarios, doc_hash="hash1")
    assert repeat.upserted == 0
    assert repeat.skipped_reason == "hash_match"


@pytest.mark.asyncio
async def test_upsert_rewrites_when_hash_changes() -> None:
    """A different ``doc_hash`` always triggers a write."""
    store = InMemoryFoundationCatalogStore()
    await store.upsert_many(
        (_make_scenario(scenario_id="s1_1_a", risk_level="Low"),),
        doc_hash="hash1",
    )
    result = await store.upsert_many(
        (_make_scenario(scenario_id="s1_1_a", risk_level="High"),),
        doc_hash="hash2",
    )
    assert result.upserted == 1
    rows = await store.list_all()
    assert rows[0].risk_level == "High"


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_id() -> None:
    """Unknown ids resolve to ``None``, not raise."""
    store = InMemoryFoundationCatalogStore()
    await store.upsert_many(
        (_make_scenario(scenario_id="s1_1_a"),),
        doc_hash="hash1",
    )
    assert await store.get("nope") is None


@pytest.mark.asyncio
async def test_get_returns_stored_scenario() -> None:
    """``get`` returns the exact row that was upserted."""
    store = InMemoryFoundationCatalogStore()
    scenario = _make_scenario(
        scenario_id="s2_3_c",
        stage_number=2,
        memory_types=("Property knowledge", "PM preference memory"),
    )
    await store.upsert_many((scenario,), doc_hash="hash1")
    found = await store.get("s2_3_c")
    assert found is not None
    assert found.memory_types == (
        "Property knowledge",
        "PM preference memory",
    )


@pytest.mark.asyncio
async def test_list_all_is_ordered_by_stage_then_id() -> None:
    """Deterministic ordering: stage number ASC, scenario id ASC."""
    store = InMemoryFoundationCatalogStore()
    await store.upsert_many(
        (
            _make_scenario(scenario_id="s3_1_z", stage_number=3),
            _make_scenario(scenario_id="s1_5_y", stage_number=1),
            _make_scenario(scenario_id="s1_2_x", stage_number=1),
        ),
        doc_hash="hash1",
    )
    rows = await store.list_all()
    ids = [row.scenario_id for row in rows]
    assert ids == ["s1_2_x", "s1_5_y", "s3_1_z"]


@pytest.mark.asyncio
async def test_get_doc_hash_returns_last_upserted_value() -> None:
    """``get_doc_hash`` reflects the most recent upsert."""
    store = InMemoryFoundationCatalogStore()
    assert await store.get_doc_hash() is None
    await store.upsert_many((_make_scenario(),), doc_hash="hash1")
    assert await store.get_doc_hash() == "hash1"
    await store.upsert_many((_make_scenario(),), doc_hash="hash2")
    assert await store.get_doc_hash() == "hash2"


# ── payload round-trip ────────────────────────────────────── #


def test_payload_round_trip_preserves_every_field() -> None:
    """JSONB serialisation must not lose a single sub-section field."""
    original = _make_scenario(
        scenario_id="s4_209_gas",
        stage_number=4,
        risk_level="Critical",
        memory_types=("Property knowledge", "Reservation context memory"),
    )
    payload = _scenario_to_payload(original)
    # JSON-encode to mirror the asyncpg → Postgres path.
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    rebuilt = _payload_to_scenario(
        scenario_id=original.scenario_id,
        stage_number=original.stage_number,
        stage_label=original.stage_label,
        title=original.title,
        payload=decoded,
    )
    assert rebuilt == original


def test_payload_round_trip_handles_empty_defaults() -> None:
    """A scenario with the minimum 5-field constructor still round-trips."""
    original = FoundationScenario(
        scenario_id="s1_1_min",
        title="Minimum scenario",
        stage_number=1,
        stage_label="Pre-Booking / Inquiry",
        trigger="Minimum trigger.",
    )
    payload = _scenario_to_payload(original)
    rebuilt = _payload_to_scenario(
        scenario_id=original.scenario_id,
        stage_number=original.stage_number,
        stage_label=original.stage_label,
        title=original.title,
        payload=payload,
    )
    assert rebuilt == original
