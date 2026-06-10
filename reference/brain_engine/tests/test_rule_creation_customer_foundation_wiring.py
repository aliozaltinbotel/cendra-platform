"""Sprint 6 W9 wiring tests — rule_creation → customer foundation.

Pins:

* :func:`_bundle_to_customer_scenario` builds a
  :class:`FoundationCustomerScenario` from a finalised
  :class:`ConversationState` using the most descriptive available
  title and concatenated trigger text.
* :func:`_persist_finalized_rule_to_customer_foundation` is a
  best-effort no-op when the store is not wired (``None`` or
  pre-W9 default).
* When wired, the finalisation path persists every confirmed rule
  into the customer foundation store.  Cancellation never
  persists.
"""

from __future__ import annotations

import pytest

from brain_engine.patterns.foundation_customer_catalog import (
    FoundationCustomerScenario,
    InMemoryFoundationCustomerCatalogStore,
)
from brain_engine.rule_creation.models import (
    AIRuleComponent,
    ConversationState,
    EscalationComponent,
    LabelComponent,
    LabelCondition,
    LabelOperator,
    RuleBundle,
    RuleType,
    TagComponent,
    WorkflowPhase,
)
from brain_engine.rule_creation.workflow import (
    _bundle_to_customer_scenario,
    _persist_finalized_rule_to_customer_foundation,
    set_customer_foundation_store,
)

# ── fixtures ──────────────────────────────────────────────── #


def _ai_rule_state(
    *,
    customer_id: str = "customer-1",
    workflow_id: str = "rc-abc123",
) -> ConversationState:
    """Build a :class:`ConversationState` with an AI rule bundle."""
    return ConversationState(
        workflow_id=workflow_id,
        customer_id=customer_id,
        phase=WorkflowPhase.FINALIZED,
        rule_type=RuleType.AI_RULE,
        partial_bundle=RuleBundle(
            bundle_name="Early check-in policy",
            rule_type=RuleType.AI_RULE,
            ai_rule_component=AIRuleComponent(
                name="No Availability Confirmation",
                description="Prevent confirming availability without verification",
                expected_behavior=(
                    "When guest asks about availability, always say "
                    "you will check and get back"
                ),
            ),
        ),
    )


def _tag_with_label_state() -> ConversationState:
    """Composite bundle that exercises every trigger-builder branch."""
    return ConversationState(
        workflow_id="rc-tag-001",
        customer_id="customer-2",
        phase=WorkflowPhase.FINALIZED,
        rule_type=RuleType.LABEL_TAG_AI_RULE,
        partial_bundle=RuleBundle(
            bundle_name="VIP complaint flow",
            rule_type=RuleType.LABEL_TAG_AI_RULE,
            label_component=LabelComponent(
                name="VIP Guest",
                icon="star",
                conditions=[
                    LabelCondition(
                        field="totalPrice",
                        operator=LabelOperator.GREATER_THAN,
                        value="500",
                    ),
                ],
            ),
            tag_component=TagComponent(
                name="Cleanliness Complaint",
                description="Guest mentions dirty / unclean areas",
                priority="high",
                keywords=["dirty", "stain", "dust"],
            ),
            ai_rule_component=AIRuleComponent(
                name="Escalate Complaint",
                description="VIP cleanliness complaints go straight to PM",
                expected_behavior="Notify PM within 5 minutes",
            ),
            escalation_component=EscalationComponent(
                escalate_to="pm",
                auto_create_task=True,
                task_priority="High",
                notification_channel="default",
            ),
        ),
    )


# ── _bundle_to_customer_scenario ──────────────────────────── #


def test_bundle_uses_ai_rule_name_as_title() -> None:
    """AI rule name wins when present."""
    scenario = _bundle_to_customer_scenario(_ai_rule_state())
    assert scenario.title == "No Availability Confirmation"


def test_bundle_falls_back_to_bundle_name() -> None:
    """Without any component name, ``bundle_name`` is used."""
    state = ConversationState(
        workflow_id="rc-fallback",
        customer_id="customer-1",
        phase=WorkflowPhase.FINALIZED,
        partial_bundle=RuleBundle(
            bundle_name="Saved as bundle name only",
        ),
    )
    scenario = _bundle_to_customer_scenario(state)
    assert scenario.title == "Saved as bundle name only"


def test_bundle_falls_back_to_workflow_id_when_empty() -> None:
    """Empty bundle but non-empty workflow_id produces a sensible title."""
    state = ConversationState(
        workflow_id="rc-empty",
        customer_id="customer-1",
        phase=WorkflowPhase.FINALIZED,
        partial_bundle=RuleBundle(),
    )
    scenario = _bundle_to_customer_scenario(state)
    assert scenario.title == "Rule rc-empty"


def test_bundle_scenario_id_combines_customer_and_workflow() -> None:
    """``scenario_id`` is deterministic from customer + workflow."""
    scenario = _bundle_to_customer_scenario(_ai_rule_state())
    assert scenario.scenario_id == "c_customer-1_rc-abc123"


def test_bundle_source_rule_id_matches_workflow_id() -> None:
    """``source_rule_id`` preserves the workflow id provenance."""
    scenario = _bundle_to_customer_scenario(_ai_rule_state())
    assert scenario.source_rule_id == "rc-abc123"


def test_bundle_carries_default_safety_flags() -> None:
    """W9 keeps the FL-14 conservative defaults intact."""
    scenario = _bundle_to_customer_scenario(_ai_rule_state())
    assert scenario.risk_level == "Medium"
    assert scenario.should_learn_pattern == "No"


def test_bundle_trigger_concatenates_all_component_text() -> None:
    """Composite bundle ⇒ trigger contains every component's text."""
    scenario = _bundle_to_customer_scenario(_tag_with_label_state())
    assert "VIP cleanliness complaints" in scenario.trigger
    assert "Notify PM within 5 minutes" in scenario.trigger
    assert "Guest mentions dirty" in scenario.trigger
    assert "Keywords: dirty, stain, dust" in scenario.trigger
    assert "totalPrice GreaterThan 500" in scenario.trigger


# ── _persist_finalized_rule_to_customer_foundation ────────── #


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """Reset the module-level store between tests."""
    set_customer_foundation_store(None)
    yield
    set_customer_foundation_store(None)


@pytest.mark.asyncio
async def test_persist_no_op_when_store_unwired() -> None:
    """Without ``set_customer_foundation_store``, persist is a no-op."""
    state = _ai_rule_state()
    # Should not raise / complete silently.
    await _persist_finalized_rule_to_customer_foundation(state)


@pytest.mark.asyncio
async def test_persist_writes_to_wired_store() -> None:
    """A wired store receives the upsert for a finalised bundle."""
    store = InMemoryFoundationCustomerCatalogStore()
    set_customer_foundation_store(store)
    state = _ai_rule_state()
    await _persist_finalized_rule_to_customer_foundation(state)
    rows = await store.list_for_customer("customer-1")
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, FoundationCustomerScenario)
    assert row.title == "No Availability Confirmation"
    assert row.source_rule_id == "rc-abc123"


@pytest.mark.asyncio
async def test_persist_skips_when_customer_id_missing() -> None:
    """An empty customer_id is logged + skipped — never raises."""
    store = InMemoryFoundationCustomerCatalogStore()
    set_customer_foundation_store(store)
    state = _ai_rule_state(customer_id="")
    await _persist_finalized_rule_to_customer_foundation(state)
    # Empty customer_id ⇒ short-circuit; no rows persisted under
    # any customer.
    rows = await store.list_for_customer("")
    assert rows == ()


@pytest.mark.asyncio
async def test_persist_idempotent_on_workflow_id() -> None:
    """Re-running the persist on the same workflow refreshes the row."""
    store = InMemoryFoundationCustomerCatalogStore()
    set_customer_foundation_store(store)
    state = _ai_rule_state()
    await _persist_finalized_rule_to_customer_foundation(state)
    state.partial_bundle.ai_rule_component = AIRuleComponent(
        name="Updated Behaviour",
        description="Revised description",
        expected_behavior="Always check calendar first",
    )
    await _persist_finalized_rule_to_customer_foundation(state)
    rows = await store.list_for_customer("customer-1")
    assert len(rows) == 1
    assert rows[0].title == "Updated Behaviour"


@pytest.mark.asyncio
async def test_persist_failure_does_not_raise() -> None:
    """A flaky store error is logged but never escapes the persist hook.

    Rule-creation latency must stay stable even when the
    second-tier foundation store is degraded.
    """

    class _FailingStore(InMemoryFoundationCustomerCatalogStore):
        async def upsert(
            self,
            scenario: FoundationCustomerScenario,
        ) -> None:
            raise RuntimeError("simulated store failure")

    store = _FailingStore()
    set_customer_foundation_store(store)
    state = _ai_rule_state()
    # Must not raise.
    await _persist_finalized_rule_to_customer_foundation(state)


# ── set_customer_foundation_store ────────────────────────── #


@pytest.mark.asyncio
async def test_set_store_accepts_none_to_disable() -> None:
    """Passing ``None`` clears the previously-set store."""
    store = InMemoryFoundationCustomerCatalogStore()
    set_customer_foundation_store(store)
    set_customer_foundation_store(None)
    # After clearing the store the persist hook becomes a no-op
    # — verify by asserting the store stays empty.
    await _persist_finalized_rule_to_customer_foundation(
        _ai_rule_state(),
    )
    rows = await store.list_for_customer("customer-1")
    assert rows == ()
