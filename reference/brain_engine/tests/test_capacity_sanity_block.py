# ruff: noqa: RUF001
"""Tests for the capacity-unknown sanity block (R14).

Defensive brain guard: an active reservation (post-Inquiry)
carrying ``num_guests=0`` AND ``num_children=0`` is a data gap,
not a real "0 guests booked" fact.  Without this block, brain
runs naive math against the property max — "0 + 3 ≤ 4 ⇒ yes
bring 3 friends" — and the guest may believe the snapshot was
authoritative.

Trigger contract this module pins:

* Active status (``Confirmed`` / ``Currently Hosting`` /
  ``Check-in Today`` / etc.) + both counts zero → block emitted.
* Pre-booking status (``Inquiry`` / ``follow_up``) + zero counts
  → empty (legitimate — guest has not decided).
* Either count populated → empty (data is present).
* Status missing / empty → empty (defensive, do not over-fire
  on shapeless requests).
* ``ConversationService._assemble_prompt`` splices the block
  immediately after the base prompt for LLM primacy.
"""

from __future__ import annotations

import pytest

from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
    ReservationContext,
)
from brain_engine.conversation.prompt_formatters import (
    _CAPACITY_UNKNOWN_BLOCK,
    _format_capacity_sanity_block,
)

# ── trigger / no-trigger contract ──────────────────────────────


@pytest.mark.parametrize(
    "status",
    [
        "Confirmed",
        "Currently Hosting",
        "Check-in Today",
        "Check-in Tomorrow",
        "Post Stay",
        "Arriving in 2 Days",
        "Check-out Today",
    ],
)
def test_active_status_with_zero_counts_emits_block(status: str) -> None:
    """Every active status with no guest counts triggers the
    sanity block — the C5 / capacity-question regression guard."""
    assert _format_capacity_sanity_block(status, 0, 0) == _CAPACITY_UNKNOWN_BLOCK


@pytest.mark.parametrize(
    "status",
    [
        "Inquiry",
        "inquiry",
        "follow_up",
        "InquiryPreapproved",
        "inquirynotpossible",
    ],
)
def test_pre_booking_status_with_zero_counts_returns_empty(
    status: str,
) -> None:
    """Pre-booking statuses are allowed to ship zero guests — the
    guest has not committed to a head count yet.  Block must NOT
    fire so the LLM can answer max-occupancy questions normally
    against the property data."""
    assert _format_capacity_sanity_block(status, 0, 0) == ""


@pytest.mark.parametrize(
    "num_guests,num_children",
    [
        (1, 0),
        (2, 1),
        (0, 1),
        (0, 3),
        (4, 0),
    ],
)
def test_non_zero_counts_return_empty(
    num_guests: int, num_children: int,
) -> None:
    """Any populated count means the snapshot is complete enough
    for capacity arithmetic — the block must NOT fire."""
    assert _format_capacity_sanity_block("Confirmed", num_guests, num_children) == ""


@pytest.mark.parametrize(
    "status",
    ["", " ", "   "],
)
def test_empty_status_returns_empty(status: str) -> None:
    """A shapeless / missing status means brain has no booking
    context at all — the no-data fallbacks in [RESERVATION FACTS]
    handle the deferral.  Adding a CAPACITY block on top would be
    redundant noise."""
    assert _format_capacity_sanity_block(status, 0, 0) == ""


def test_block_starts_with_caution_header() -> None:
    """The block leads with a stable anchor so downstream tests
    and parsers can find it deterministically."""
    flat = " ".join(_CAPACITY_UNKNOWN_BLOCK.split())
    assert "CAPACITY UNKNOWN — CAUTION" in flat


def test_block_carries_directives() -> None:
    """Each directive the LLM must follow is anchored on a stable
    substring — guards against future prose drift."""
    flat = " ".join(_CAPACITY_UNKNOWN_BLOCK.split())
    # Do-not-compute-from-zero
    assert "Do NOT compute additions against a zero base" in flat
    # Do-not-promise rule
    assert "Do NOT promise the guest can bring N additional people" in flat
    # Politely-ask / Turkish deferral
    assert "Rezervasyondaki misafir sayısını" in flat
    # Carve-out for non-capacity topics
    assert "non-capacity" in flat


# ── _assemble_prompt integration ──────────────────────────────


def _state_with_capacity(
    status: str,
    num_guests: int,
    num_children: int,
) -> PipelineState:
    """Build a minimal PipelineState carrying capacity fields."""
    request = ConversationRequest(
        customer_id="C1",
        property_id="P1",
        reservation_context=ReservationContext(
            status=status,
            num_guests=num_guests,
            num_children=num_children,
        ),
    )
    return PipelineState(request=request)


def test_assemble_prompt_injects_capacity_block_for_active_zero_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a Confirmed booking with 0 guests / 0 children
    produces a prompt that carries the CAPACITY UNKNOWN block
    BEFORE [RESERVATION FACTS] — primacy assertion."""
    from brain_engine.conversation.service import ConversationService
    from brain_engine.customer.models import CustomerSettings

    svc = ConversationService.__new__(ConversationService)
    state = _state_with_capacity("Confirmed", 0, 0)
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert "CAPACITY UNKNOWN — CAUTION" in out.system_prompt
    capacity_idx = out.system_prompt.index("CAPACITY UNKNOWN")
    reservation_idx = out.system_prompt.index("[RESERVATION FACTS]")
    assert capacity_idx < reservation_idx


def test_assemble_prompt_skips_capacity_block_when_guests_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A populated guest count leaves the prompt byte-identical
    to the pre-R14 path — no CAPACITY block."""
    from brain_engine.conversation.service import ConversationService
    from brain_engine.customer.models import CustomerSettings

    svc = ConversationService.__new__(ConversationService)
    state = _state_with_capacity("Confirmed", 2, 0)
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert "CAPACITY UNKNOWN" not in out.system_prompt


def test_assemble_prompt_skips_capacity_block_for_inquiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-booking status with zero counts is legitimate — block
    must NOT fire even though counts are zero."""
    from brain_engine.conversation.service import ConversationService
    from brain_engine.customer.models import CustomerSettings

    svc = ConversationService.__new__(ConversationService)
    state = _state_with_capacity("Inquiry", 0, 0)
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert "CAPACITY UNKNOWN" not in out.system_prompt
