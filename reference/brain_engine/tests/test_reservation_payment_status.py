"""Tests for the payment_status field on ReservationContext (R9.C / C6).

Sandbox UI test C6 (2026-05-19): the agent answered the same way
for "have I paid for my stay?" regardless of the Payment Status
toggle in the UI (ON vs OFF).  Root cause: ``ReservationContext``
had no ``payment_status`` field, the parser ignored the toggle,
and the formatter never rendered a Payment-status line — the LLM
inferred from reservation status alone ("Check-in Today must be
paid") and reported the same outcome whether the toggle was on
or off.

This module pins:

1. ``ReservationContext.payment_status`` field exists, defaults
   to ``""`` (unknown), participates in ``has_data()``.
2. ``_reservation_context_from_state`` normalises every common
   input shape — ``bool`` / ``"paid"`` / ``"true"`` / aliases /
   missing — to the tri-state ``"paid"`` / ``"unpaid"`` / ``""``.
3. ``_format_reservation_context`` renders a stable
   ``- Payment status: …`` line when the value is non-empty and
   omits it otherwise (so an unknown payment status does not
   put the LLM in a position to guess).
"""

from __future__ import annotations

import pytest

from api_server.server import _reservation_context_from_state
from brain_engine.conversation.models import ReservationContext
from brain_engine.conversation.service import _format_reservation_context

# ── Pydantic model contract ─────────────────────────────────────


def test_payment_status_default_is_empty_string() -> None:
    """A bare ReservationContext must default to the unknown
    tri-state value so existing callers stay byte-identical."""
    ctx = ReservationContext()
    assert ctx.payment_status == ""


def test_payment_status_accepts_paid_unpaid_strings() -> None:
    """Construction with the canonical values must succeed."""
    paid = ReservationContext(payment_status="paid")
    unpaid = ReservationContext(payment_status="unpaid")
    assert paid.payment_status == "paid"
    assert unpaid.payment_status == "unpaid"


def test_has_data_true_when_only_payment_status_set() -> None:
    """A snapshot carrying only a payment-status value still
    counts as "has data" so the AG-UI handler does not collapse
    it to ``None``."""
    ctx = ReservationContext(payment_status="paid")
    assert ctx.has_data() is True


# ── parser normalisation ────────────────────────────────────────


@pytest.mark.parametrize(
    "input_value,expected",
    [
        (True, "paid"),
        (False, "unpaid"),
        ("paid", "paid"),
        ("unpaid", "unpaid"),
        ("true", "paid"),
        ("false", "unpaid"),
        ("yes", "paid"),
        ("no", "unpaid"),
        ("1", "paid"),
        ("0", "unpaid"),
        ("TRUE", "paid"),  # case-insensitive
        ("PAID", "paid"),
    ],
)
def test_payment_status_parser_normalises_known_inputs(
    input_value: object, expected: str,
) -> None:
    """Every documented UI-side shape collapses to the canonical
    tri-state.  The UI may flip between sending a JSON bool and a
    string literal during testing — both paths must yield the
    same brain-side value."""
    raw = {"status": "Check-in Today", "payment_status": input_value}
    ctx = _reservation_context_from_state(raw)
    assert ctx is not None
    assert ctx.payment_status == expected


def test_payment_status_parser_accepts_guest_has_paid_alias() -> None:
    """The UI label reads "Guest has paid".  If a future
    frontend ships the key verbatim (``guest_has_paid``) the
    parser must still pick it up."""
    raw = {"status": "Check-in Today", "guest_has_paid": True}
    ctx = _reservation_context_from_state(raw)
    assert ctx is not None
    assert ctx.payment_status == "paid"


def test_payment_status_parser_accepts_is_paid_alias() -> None:
    """``is_paid`` / ``paid`` as alternative key names are common
    in PMS payloads — both must resolve."""
    a = _reservation_context_from_state(
        {"status": "X", "is_paid": True},
    )
    b = _reservation_context_from_state({"status": "X", "paid": True})
    assert a is not None and a.payment_status == "paid"
    assert b is not None and b.payment_status == "paid"


def test_payment_status_parser_missing_returns_empty_string() -> None:
    """A snapshot that does not carry any payment key keeps the
    tri-state as empty ("unknown") — the LLM must not see a
    Payment-status line at all in that case."""
    raw = {"status": "Check-in Today"}
    ctx = _reservation_context_from_state(raw)
    assert ctx is not None
    assert ctx.payment_status == ""


def test_payment_status_parser_unknown_string_passes_through() -> None:
    """A literal the UI invents that we don't recognise must NOT
    be silently coerced to paid/unpaid — pass it through so the
    formatter renders it verbatim and we can flag the drift in
    post-mortem inspection."""
    raw = {"status": "X", "payment_status": "partial-refund"}
    ctx = _reservation_context_from_state(raw)
    assert ctx is not None
    assert ctx.payment_status == "partial-refund"


def test_payment_status_parser_reads_nested_reservation_context() -> None:
    """When the client wraps the snapshot inside ``state.reservation_context``
    instead of top-level keys, the parser must read it from there."""
    raw = {
        "reservation_context": {
            "status": "Check-in Today",
            "payment_status": True,
        }
    }
    ctx = _reservation_context_from_state(raw)
    assert ctx is not None
    assert ctx.payment_status == "paid"


# ── formatter rendering ─────────────────────────────────────────


def test_format_renders_payment_status_when_paid() -> None:
    """When the toggle is ON, the LLM-facing block must surface a
    stable ``- Payment status: paid`` line so the model quotes it
    instead of inferring from reservation status."""
    ctx = ReservationContext(
        status="Check-in Today",
        payment_status="paid",
    )
    rendered = _format_reservation_context(ctx)

    assert "- Payment status: paid" in rendered


def test_format_renders_payment_status_when_unpaid() -> None:
    """The unpaid path is equally important — without this line
    the LLM cannot distinguish toggle-OFF from toggle-missing."""
    ctx = ReservationContext(
        status="Check-in Today",
        payment_status="unpaid",
    )
    rendered = _format_reservation_context(ctx)

    assert "- Payment status: unpaid" in rendered


def test_format_omits_payment_status_when_empty() -> None:
    """An unknown payment status (empty string) must NOT render a
    line — otherwise the LLM might see ``Payment status:`` with a
    missing value and treat it as a hint."""
    ctx = ReservationContext(status="Check-in Today")
    rendered = _format_reservation_context(ctx)

    assert "Payment status" not in rendered


def test_format_preserves_existing_anchors() -> None:
    """The new field must not have shifted the surrounding block
    anchors — header line first, STRICT RULES section present."""
    ctx = ReservationContext(
        status="Check-in Today",
        payment_status="paid",
    )
    rendered = _format_reservation_context(ctx)

    assert rendered.startswith("[RESERVATION FACTS]\n")
    assert "STRICT RULES:" in rendered
