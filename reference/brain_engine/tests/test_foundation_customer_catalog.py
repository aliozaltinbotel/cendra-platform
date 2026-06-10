"""Tests for the customer-facing foundation tier (FL-14).

Pins:

* :class:`FoundationCustomerScenario` constructor invariants
  (``customer_id`` / ``scenario_id`` / ``title`` must be non-empty,
  defaults match the FL-14 design).
* :class:`InMemoryFoundationCustomerCatalogStore` Protocol
  satisfaction + CRUD round-trip + per-customer listing +
  idempotent upsert on the natural key + delete behaviour.
* :func:`upsert_batch` helper count semantics.
"""

from __future__ import annotations

import pytest

from brain_engine.patterns.foundation_customer_catalog import (
    FoundationCustomerCatalogStore,
    FoundationCustomerScenario,
    InMemoryFoundationCustomerCatalogStore,
    upsert_batch,
)

# ── FoundationCustomerScenario invariants ─────────────────── #


def test_requires_customer_id() -> None:
    """Empty ``customer_id`` raises."""
    with pytest.raises(ValueError, match="customer_id"):
        FoundationCustomerScenario(
            customer_id="",
            scenario_id="c1_rule_42_early_checkin",
            title="Early check-in policy",
        )


def test_requires_scenario_id() -> None:
    """Empty ``scenario_id`` raises."""
    with pytest.raises(ValueError, match="scenario_id"):
        FoundationCustomerScenario(
            customer_id="customer-1",
            scenario_id="",
            title="Early check-in policy",
        )


def test_requires_title() -> None:
    """Empty ``title`` raises."""
    with pytest.raises(ValueError, match="title"):
        FoundationCustomerScenario(
            customer_id="customer-1",
            scenario_id="c1_rule_42",
            title="",
        )


def test_defaults_match_fl14_design() -> None:
    """Customer-authored entries default to safe values.

    The defaults reflect the FL-14 design: a PM-authored rule
    rarely warrants learning a global pattern (so
    ``should_learn_pattern`` defaults to ``"No"``) and never
    outranks a core ``Critical`` scenario (so ``risk_level``
    defaults to ``"Medium"``).
    """
    scenario = FoundationCustomerScenario(
        customer_id="customer-1",
        scenario_id="c1_rule_42",
        title="Early check-in policy",
    )
    assert scenario.risk_level == "Medium"
    assert scenario.should_learn_pattern == "No"
    assert scenario.memory_types == ()
    assert scenario.required_data_checks == ()
    assert scenario.signals_to_inspect == ()
    assert scenario.source_rule_id == ""


# ── store Protocol + CRUD ─────────────────────────────────── #


def test_in_memory_store_satisfies_protocol() -> None:
    """The in-memory implementation satisfies the runtime Protocol."""
    store = InMemoryFoundationCustomerCatalogStore()
    assert isinstance(store, FoundationCustomerCatalogStore)


@pytest.mark.asyncio
async def test_upsert_round_trip() -> None:
    """``upsert`` followed by ``get`` returns the original value."""
    store = InMemoryFoundationCustomerCatalogStore()
    scenario = FoundationCustomerScenario(
        customer_id="customer-1",
        scenario_id="c1_rule_42",
        title="Early check-in policy",
        trigger="PM allows early check-in when cleaning ready by 13:00",
        ai_default_behavior="Offer early check-in once housekeeping confirms ready",
        memory_types=("PM preference memory",),
        should_auto_reply="Conditional",
        should_escalate_to_pm="No",
        source_rule_id="workflow-abc-123",
    )
    await store.upsert(scenario)
    fetched = await store.get("customer-1", "c1_rule_42")
    assert fetched == scenario


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_pair() -> None:
    """Unknown ``(customer_id, scenario_id)`` resolves to ``None``."""
    store = InMemoryFoundationCustomerCatalogStore()
    assert await store.get("customer-1", "unknown") is None


@pytest.mark.asyncio
async def test_upsert_idempotent_on_natural_key() -> None:
    """Re-upserting the same key refreshes the row in place."""
    store = InMemoryFoundationCustomerCatalogStore()
    initial = FoundationCustomerScenario(
        customer_id="customer-1",
        scenario_id="c1_rule_42",
        title="Initial title",
        trigger="initial trigger",
    )
    updated = FoundationCustomerScenario(
        customer_id="customer-1",
        scenario_id="c1_rule_42",
        title="Updated title",
        trigger="updated trigger",
    )
    await store.upsert(initial)
    await store.upsert(updated)
    fetched = await store.get("customer-1", "c1_rule_42")
    assert fetched is not None
    assert fetched.title == "Updated title"
    assert fetched.trigger == "updated trigger"


@pytest.mark.asyncio
async def test_list_for_customer_scopes_properly() -> None:
    """``list_for_customer`` returns only the requested customer's rows."""
    store = InMemoryFoundationCustomerCatalogStore()
    await store.upsert(
        FoundationCustomerScenario(
            customer_id="customer-1",
            scenario_id="c1_rule_a",
            title="A",
        ),
    )
    await store.upsert(
        FoundationCustomerScenario(
            customer_id="customer-1",
            scenario_id="c1_rule_b",
            title="B",
        ),
    )
    await store.upsert(
        FoundationCustomerScenario(
            customer_id="customer-2",
            scenario_id="c2_rule_c",
            title="C",
        ),
    )
    rows = await store.list_for_customer("customer-1")
    assert {row.scenario_id for row in rows} == {
        "c1_rule_a",
        "c1_rule_b",
    }


@pytest.mark.asyncio
async def test_list_for_customer_ordering_is_deterministic() -> None:
    """``list_for_customer`` orders by ``scenario_id`` ASC."""
    store = InMemoryFoundationCustomerCatalogStore()
    await store.upsert(
        FoundationCustomerScenario(
            customer_id="c",
            scenario_id="c_rule_z",
            title="Z",
        ),
    )
    await store.upsert(
        FoundationCustomerScenario(
            customer_id="c",
            scenario_id="c_rule_a",
            title="A",
        ),
    )
    await store.upsert(
        FoundationCustomerScenario(
            customer_id="c",
            scenario_id="c_rule_m",
            title="M",
        ),
    )
    rows = await store.list_for_customer("c")
    assert [row.scenario_id for row in rows] == [
        "c_rule_a",
        "c_rule_m",
        "c_rule_z",
    ]


@pytest.mark.asyncio
async def test_delete_returns_true_when_row_existed() -> None:
    """``delete`` returns ``True`` only when something was removed."""
    store = InMemoryFoundationCustomerCatalogStore()
    await store.upsert(
        FoundationCustomerScenario(
            customer_id="c",
            scenario_id="c_rule_a",
            title="A",
        ),
    )
    assert await store.delete("c", "c_rule_a") is True
    assert await store.get("c", "c_rule_a") is None


@pytest.mark.asyncio
async def test_delete_returns_false_when_row_missing() -> None:
    """``delete`` returns ``False`` for unknown keys."""
    store = InMemoryFoundationCustomerCatalogStore()
    assert await store.delete("c", "unknown") is False


# ── batch helper ──────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_upsert_batch_writes_every_scenario() -> None:
    """``upsert_batch`` persists every supplied scenario."""
    store = InMemoryFoundationCustomerCatalogStore()
    scenarios = [
        FoundationCustomerScenario(
            customer_id="c",
            scenario_id=f"c_rule_{i}",
            title=f"Rule {i}",
        )
        for i in range(4)
    ]
    written = await upsert_batch(store, scenarios)
    assert written == 4
    listed = await store.list_for_customer("c")
    assert len(listed) == 4


# ── customer isolation ────────────────────────────────────── #


@pytest.mark.asyncio
async def test_customers_are_isolated() -> None:
    """Two customers can use the same ``scenario_id`` without collision."""
    store = InMemoryFoundationCustomerCatalogStore()
    await store.upsert(
        FoundationCustomerScenario(
            customer_id="customer-1",
            scenario_id="shared_id",
            title="One",
        ),
    )
    await store.upsert(
        FoundationCustomerScenario(
            customer_id="customer-2",
            scenario_id="shared_id",
            title="Two",
        ),
    )
    one = await store.get("customer-1", "shared_id")
    two = await store.get("customer-2", "shared_id")
    assert one is not None and one.title == "One"
    assert two is not None and two.title == "Two"
