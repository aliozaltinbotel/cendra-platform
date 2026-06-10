# ruff: noqa: RUF001
"""Tests for the stale-reservation hard deferral block (R13).

Defensive complement to R12 (explicit Expired status).  R12 fires
only when the PMS / UI label is literally ``"expired"``; many
real-world stale bookings carry a different label (sync lag from
PMS, cancelled-but-not-relabelled, sandbox testing with fixed
dates).  R13 catches those by comparing the booking's
``check_out`` to ``current_time`` — if the message arrives
strictly after the stay ended, the block fires.

Contract this module pins:

1. Past check_out (``check_out < current_time``) → block emitted.
2. Future / same-day check_out → empty string (live booking).
3. Empty / malformed dates → empty string (cannot decide safely;
   the no-data fallbacks in [RESERVATION FACTS] / [CALENDAR
   AVAILABILITY] still defer in that case).
4. Block text carries no-confirm / no-codes / no-modify directives
   plus a Turkish deferral phrase.
5. ``ConversationService._assemble_prompt`` splices the block
   immediately after the base prompt for primacy.
"""

from __future__ import annotations

import pytest

from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
    ReservationContext,
)
from brain_engine.conversation.prompt_formatters import (
    _STALE_RESERVATION_BLOCK,
    _format_stale_reservation_block,
)

# ── trigger / no-trigger contract ──────────────────────────────


@pytest.mark.parametrize(
    "check_out,current_time",
    [
        ("2026-05-17", "2026-05-19"),
        ("2026-05-17", "2026-05-19T08:00:00"),
        ("2026-05-17T11:00:00", "2026-05-19T08:00:00Z"),
        ("2026-01-01", "2026-12-31"),
    ],
)
def test_past_check_out_emits_block(
    check_out: str, current_time: str,
) -> None:
    """When the message arrives strictly after check_out the
    block must fire — every common ISO shape on either side."""
    rendered = _format_stale_reservation_block(check_out, current_time)
    assert rendered == _STALE_RESERVATION_BLOCK


@pytest.mark.parametrize(
    "check_out,current_time",
    [
        ("2026-05-20", "2026-05-19"),  # future check_out
        ("2026-05-19", "2026-05-19"),  # same day = still active
        ("2026-05-19T11:00:00", "2026-05-19T08:00:00"),  # same day, intra-day
        ("2026-12-31", "2026-01-01"),
    ],
)
def test_future_or_same_day_check_out_returns_empty(
    check_out: str, current_time: str,
) -> None:
    """A booking whose check-out has not yet passed keeps the
    block empty — the live snapshot in [RESERVATION FACTS] is
    still authoritative."""
    assert _format_stale_reservation_block(check_out, current_time) == ""


@pytest.mark.parametrize(
    "check_out,current_time",
    [
        ("", "2026-05-19"),  # missing check_out
        ("2026-05-19", ""),  # missing current_time
        ("", ""),  # both missing
        ("next Friday", "2026-05-19"),  # free-text date
        ("18/05/2026", "2026-05-19"),  # non-ISO format
        ("not a date", "also bogus"),
    ],
)
def test_empty_or_malformed_dates_return_empty(
    check_out: str, current_time: str,
) -> None:
    """Unparseable inputs must NOT trigger the block — silent
    defaulting on bad data would over-fire and tag live bookings
    as stale.  Better to skip and let the no-data fallbacks
    handle the prompt deferral."""
    assert _format_stale_reservation_block(check_out, current_time) == ""


# ── block content ──────────────────────────────────────────────


def test_block_contains_hard_directives() -> None:
    """Each directive the LLM must follow is anchored on a stable
    substring — guards against future prose drift."""
    flat = " ".join(_STALE_RESERVATION_BLOCK.split())
    assert "STALE RESERVATION" in flat
    assert "HARD DEFERRAL" in flat
    assert "Do NOT confirm the reservation" in flat
    assert "Do NOT share access codes" in flat
    assert "Do NOT offer to 'modify the reservation'" in flat
    assert "Konaklamanız sona ermiş" in flat  # TR deferral


def test_block_mentions_extension_refusal() -> None:
    """A common guest ask on a stale booking is "extend my stay"
    — the LLM must refuse and require a fresh inquiry."""
    flat = " ".join(_STALE_RESERVATION_BLOCK.split())
    assert "extend the stay" in flat
    assert "fresh inquiry is required" in flat


# ── _assemble_prompt integration ──────────────────────────────


def _state_with_dates(check_out: str, current_time: str) -> PipelineState:
    """Build a minimal PipelineState carrying ``check_out`` and
    ``current_time`` on the reservation context."""
    request = ConversationRequest(
        customer_id="C1",
        property_id="P1",
        reservation_context=ReservationContext(
            status="Confirmed",
            check_out=check_out,
            current_time=current_time,
        ),
    )
    return PipelineState(request=request)


def test_assemble_prompt_injects_stale_block_when_check_out_past(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past check_out ⇒ rendered prompt carries the STALE block
    BEFORE the [RESERVATION FACTS] snapshot (primacy assertion)."""
    from brain_engine.conversation.service import ConversationService
    from brain_engine.customer.models import CustomerSettings

    svc = ConversationService.__new__(ConversationService)
    state = _state_with_dates("2026-05-17", "2026-05-19T08:00:00")
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert "STALE RESERVATION — HARD DEFERRAL" in out.system_prompt
    stale_idx = out.system_prompt.index("STALE RESERVATION")
    reservation_idx = out.system_prompt.index("[RESERVATION FACTS]")
    assert stale_idx < reservation_idx


def test_assemble_prompt_skips_stale_block_when_check_out_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Future check_out keeps the prompt byte-identical to the
    pre-R13 path."""
    from brain_engine.conversation.service import ConversationService
    from brain_engine.customer.models import CustomerSettings

    svc = ConversationService.__new__(ConversationService)
    state = _state_with_dates("2026-06-20", "2026-05-19T08:00:00")
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert "STALE RESERVATION" not in out.system_prompt


def test_assemble_prompt_skips_stale_block_when_dates_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing dates ⇒ no STALE block, no drift on legacy
    requests that ship no reservation context."""
    from brain_engine.conversation.service import ConversationService
    from brain_engine.customer.models import CustomerSettings

    svc = ConversationService.__new__(ConversationService)
    request = ConversationRequest(customer_id="C1", property_id="P1")
    state = PipelineState(request=request)
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert "STALE RESERVATION" not in out.system_prompt
