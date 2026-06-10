"""Service-level wiring for the PM-facts relevance diagnostic.

Pins that ``ConversationService._append_pm_facts`` emits the
``pm_facts.relevance`` INFO line whenever facts are merged into
``state.property_knowledge``, and stays silent on the code paths
that already short-circuit before merge (no store, no
``customer_id``, no facts, every fact whitespace-only, store
exception).

These tests are deliberately surgical — they exercise the same
runtime path the live SSE handler takes, but never reach Postgres,
Redis or Qdrant.  The ``InMemoryPmFactStore`` from the production
package is wired in directly so any regression in the diagnostic
contract surfaces here long before a tester finds it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from brain_engine.conversation.models import (
    ConversationMessage,
    ConversationRequest,
    PipelineState,
    SenderType,
)
from brain_engine.conversation.pm_facts import (
    InMemoryPmFactStore,
    PmFact,
)
from brain_engine.conversation.service import ConversationService

# ── Fixtures ─────────────────────────────────────────────────────


def _build_request(
    *,
    customer_id: str = "tenant1",
    property_id: str = "prop1",
    message: str = "what is the wifi password?",
) -> ConversationRequest:
    return ConversationRequest(
        customer_id=customer_id,
        property_id=property_id,
        guest_id="guest_a",
        messages=[
            ConversationMessage(
                sender_type=SenderType.GUEST,
                text=message,
            ),
        ],
    )


def _build_state(
    request: ConversationRequest,
    *,
    cleaned_message: str | None = None,
) -> PipelineState:
    state = PipelineState(request=request)
    state.cleaned_message = cleaned_message or request.messages[0].text
    return state


async def _populate(store: InMemoryPmFactStore, fact_texts: list[str]) -> None:
    """Seed the in-memory store with PM facts for one (customer, property)."""
    for index, text in enumerate(fact_texts):
        await store.add_fact(
            PmFact(
                customer_id="tenant1",
                org_id="org1",
                property_channel_id="prop1",
                fact_text=text,
                source_message_id=f"msg-{index}",
                created_at=datetime(2026, 5, 20, 12, index, tzinfo=UTC),
            ),
        )


# ── Diagnostic fires on the merge path ───────────────────────────


async def test_diagnostic_logged_when_facts_merge(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Happy path — facts present, message tokens overlap, the
    diagnostic line surfaces with the expected fields."""
    store = InMemoryPmFactStore()
    await _populate(
        store,
        ["WiFi password is GUEST2026", "Parking on the street"],
    )
    service = ConversationService(pm_fact_store=store)
    state = _build_state(_build_request())

    with caplog.at_level(
        logging.INFO, logger="brain_engine.conversation.service"
    ):
        await service._append_pm_facts(state, "prop1")

    records = [r for r in caplog.records if "pm_facts.relevance" in r.message]
    assert len(records) == 1
    payload = records[0].getMessage()
    assert "count=2" in payload
    assert "message_chars=" in payload
    assert "jaccard_max=" in payload
    # The "wifi" / "password" overlap with the message must surface as
    # non-zero — protects against a regression where tokenisation
    # silently drops Latin words.
    assert "jaccard_max=0.000" not in payload


async def test_diagnostic_counts_match_rendered_bullets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Whitespace-only facts are filtered from the bulleted list, so
    the diagnostic ``count`` must equal the number of bullets the
    LLM actually sees."""
    store = InMemoryPmFactStore()
    await _populate(
        store,
        ["real fact one", "   ", "real fact two"],
    )
    service = ConversationService(pm_fact_store=store)
    state = _build_state(_build_request())

    with caplog.at_level(
        logging.INFO, logger="brain_engine.conversation.service"
    ):
        await service._append_pm_facts(state, "prop1")

    records = [r for r in caplog.records if "pm_facts.relevance" in r.message]
    assert len(records) == 1
    assert "count=2" in records[0].getMessage()


# ── Diagnostic stays silent on the no-merge paths ─────────────────


async def test_diagnostic_silent_when_no_store(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No store injected → early return, no diagnostic to emit."""
    service = ConversationService(pm_fact_store=None)
    state = _build_state(_build_request())

    with caplog.at_level(
        logging.INFO, logger="brain_engine.conversation.service"
    ):
        await service._append_pm_facts(state, "prop1")

    assert not any("pm_facts.relevance" in r.message for r in caplog.records)


async def test_diagnostic_silent_when_no_customer_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No customer_id → store is never queried, no diagnostic."""
    store = InMemoryPmFactStore()
    service = ConversationService(pm_fact_store=store)
    state = _build_state(_build_request(customer_id=""))

    with caplog.at_level(
        logging.INFO, logger="brain_engine.conversation.service"
    ):
        await service._append_pm_facts(state, "prop1")

    assert not any("pm_facts.relevance" in r.message for r in caplog.records)


async def test_diagnostic_silent_when_no_facts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Store empty → nothing to merge, nothing to diagnose."""
    service = ConversationService(pm_fact_store=InMemoryPmFactStore())
    state = _build_state(_build_request())

    with caplog.at_level(
        logging.INFO, logger="brain_engine.conversation.service"
    ):
        await service._append_pm_facts(state, "prop1")

    assert not any("pm_facts.relevance" in r.message for r in caplog.records)


async def test_diagnostic_silent_when_every_fact_whitespace_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The rendered bulleted list is empty → service returns before
    the diagnostic block, so no log line surfaces."""
    store = InMemoryPmFactStore()
    await _populate(store, ["   ", "\n\t"])
    service = ConversationService(pm_fact_store=store)
    state = _build_state(_build_request())

    with caplog.at_level(
        logging.INFO, logger="brain_engine.conversation.service"
    ):
        await service._append_pm_facts(state, "prop1")

    assert not any("pm_facts.relevance" in r.message for r in caplog.records)


# ── No behaviour change for the merge itself ─────────────────────


async def test_property_knowledge_appendix_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The diagnostic is pure observability — the rendered
    ``MANAGER-CONFIRMED KNOWLEDGE`` block must look exactly like
    the pre-diagnostic shape."""
    store = InMemoryPmFactStore()
    await _populate(store, ["WiFi password is GUEST2026"])
    service = ConversationService(pm_fact_store=store)
    state = _build_state(_build_request())
    state.property_knowledge = "## Property Knowledge Base\n"

    await service._append_pm_facts(state, "prop1")

    expected = (
        "## Property Knowledge Base\n"
        "\n\nMANAGER-CONFIRMED KNOWLEDGE "
        "(authoritative — prefer over generic defaults):\n"
        "- WiFi password is GUEST2026"
    )
    assert state.property_knowledge == expected
