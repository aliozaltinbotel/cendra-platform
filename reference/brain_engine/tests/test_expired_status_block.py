"""Tests for the expired-booking hard deferral block (R12 / C8.1).

Sandbox UI test C8 (2026-05-19): with reservation status
``Expired``, the agent replied "Your current reservation is
**confirmed** for check-in on … If you'd like to modify your
reservation to check in today, I can check availability for you."
— direct security/business failure: the booking was no longer
active, but the agent treated it as a live reservation and even
offered to modify it.

This module pins three contracts:

1. ``_format_expired_status_block`` returns the high-priority
   "## EXPIRED BOOKING — HARD DEFERRAL" Markdown block only when
   the status (case-insensitive, whitespace-tolerant) is
   ``"expired"``.  Active statuses (``Confirmed`` /
   ``currently_hosting`` / ``Inquiry``) return empty string so
   the assembled prompt stays byte-identical to pre-R12.

2. ``PRE_BOOKING_STATUSES`` includes ``"expired"`` so the
   sensitive-fields redaction layer also covers the expired
   path — WiFi password / door / lock / GPS lines are stripped
   from property knowledge.

3. ``ConversationService._assemble_prompt`` splices the expired
   block immediately after the base prompt (high LLM-primacy
   slot) so the deferral instruction wins over any later
   property knowledge that might tempt the model to "confirm"
   the booking.
"""

from __future__ import annotations

import pytest

from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
    ReservationContext,
)
from brain_engine.conversation.prompt_formatters import (
    _EXPIRED_BOOKING_BLOCK,
    _format_expired_status_block,
)
from brain_engine.conversation.prompt_redaction import (
    PRE_BOOKING_STATUSES,
    is_pre_booking_status,
    redact_sensitive_for_status,
)

# ── formatter contract ─────────────────────────────────────────


@pytest.mark.parametrize(
    "label",
    ["expired", "Expired", "EXPIRED", "  Expired  "],
)
def test_format_expired_status_block_matches_label(label: str) -> None:
    """The C8.1 trigger — every documented PMS / UI casing of
    "expired" must produce the hard-deferral block."""
    rendered = _format_expired_status_block(label)
    assert rendered == _EXPIRED_BOOKING_BLOCK


@pytest.mark.parametrize(
    "label",
    ["Confirmed", "Currently Hosting", "Inquiry", "Post Stay", "", " "],
)
def test_format_expired_status_block_empty_for_active_statuses(
    label: str,
) -> None:
    """Active / pre-booking / blank statuses must NOT render the
    block — otherwise the deferral would fire on legitimate live
    reservations."""
    assert _format_expired_status_block(label) == ""


def test_expired_block_contains_hard_rules() -> None:
    """The block carries every directive the LLM must follow.
    Pin them explicitly so a future cleanup cannot quietly
    weaken the language."""
    text = _EXPIRED_BOOKING_BLOCK
    flat = " ".join(text.split())  # collapse wrap so phrase matches survive
    assert "EXPIRED BOOKING" in flat
    assert "HARD DEFERRAL" in flat
    # No-confirm directive
    assert "Do NOT confirm the reservation" in flat
    # No-access directive (the C8 scenario where the LLM offered
    # to check availability and modify booking)
    assert "Do NOT offer to 'check availability today'" in flat
    assert "modify the reservation" in flat
    # No-codes directive — security layer
    assert "Do NOT share access codes" in flat
    # TR deferral fallback present
    assert "Rezervasyon süresi dolmuş" in flat


# ── redaction set extension ─────────────────────────────────────


def test_pre_booking_statuses_includes_expired() -> None:
    """``"expired"`` joins the pre-booking redaction set so that
    sensitive-value lines (WiFi password / door code etc.) are
    stripped from property knowledge before the prompt is
    assembled.  Belt-and-suspenders: the EXPIRED block already
    tells the LLM not to share, this just removes the data from
    sight."""
    assert "expired" in PRE_BOOKING_STATUSES
    assert is_pre_booking_status("Expired") is True
    assert is_pre_booking_status("EXPIRED") is True


def test_expired_status_redacts_wifi_password() -> None:
    """End-to-end: with status="Expired" the WiFi password line
    in the rendered property knowledge is replaced by the
    redaction marker.  This is the structural defence even if
    the LLM somehow ignores the HARD DEFERRAL block."""
    kb = "WiFi password: 1234\nDoor code: 5678"
    out = redact_sensitive_for_status(kb, "Expired")
    assert "1234" not in out
    assert "5678" not in out


# ── _assemble_prompt integration ───────────────────────────────


def _make_state_for_status(status: str) -> PipelineState:
    """Build a minimal PipelineState carrying ``status``."""
    request = ConversationRequest(
        customer_id="C1",
        property_id="P1",
        reservation_context=ReservationContext(status=status),
    )
    return PipelineState(request=request)


def test_assemble_prompt_injects_expired_block_for_expired_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: an Expired-status request produces a prompt
    that carries the HARD DEFERRAL block immediately after the
    base system prompt, before property knowledge and reservation
    facts.  Order matters for LLM primacy bias."""
    from brain_engine.conversation.service import ConversationService
    from brain_engine.customer.models import CustomerSettings

    svc = ConversationService.__new__(ConversationService)
    state = _make_state_for_status("Expired")
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert "EXPIRED BOOKING — HARD DEFERRAL" in out.system_prompt
    # The block must come before [RESERVATION FACTS] so the
    # deferral instruction has primacy over the (historical)
    # snapshot the model would otherwise treat as authoritative.
    expired_idx = out.system_prompt.index("EXPIRED BOOKING")
    reservation_idx = out.system_prompt.index("[RESERVATION FACTS]")
    assert expired_idx < reservation_idx


def test_assemble_prompt_skips_expired_block_for_active_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active statuses leave the prompt byte-identical to the
    pre-R12 path — no stray EXPIRED block."""
    from brain_engine.conversation.service import ConversationService
    from brain_engine.customer.models import CustomerSettings

    svc = ConversationService.__new__(ConversationService)
    state = _make_state_for_status("Confirmed")
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert "EXPIRED BOOKING" not in out.system_prompt
