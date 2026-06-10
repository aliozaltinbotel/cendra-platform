"""Anti-hallucination price rules in the [RESERVATION FACTS] block.

Closes Sandbox UI test C7 (2026-05-19): with Total Price=-3000 in
the UI, the agent fabricated €60 by computing
``base_price × nights + cleaning_fee`` from property knowledge.
A structural failure — the agent rationalised an "unusual" total
away by computing one from list prices, in direct violation of the
authoritative-snapshot contract.

Two surfaces are tightened:

1. ``_format_reservation_context`` — when a snapshot IS attached
   the STRICT RULES section now carries an explicit anti-compute
   rule plus an unusual-value-quote-anyway rule.
2. ``_RESERVATION_NO_DATA_BLOCK`` — when NO snapshot is attached
   the rules forbid computing a total from property base price.

These tests pin the new wording so a future cleanup cannot quietly
remove the guard.
"""

from __future__ import annotations

from brain_engine.conversation.models import ReservationContext
from brain_engine.conversation.service import (
    _RESERVATION_NO_DATA_BLOCK,
    _format_reservation_context,
)

# ── populated snapshot rules ─────────────────────────────────────


def test_populated_block_carries_anti_compute_price_rule() -> None:
    """When total_price is present, the rendered block must include
    the explicit "never compute by multiplying per-night × nights"
    rule that closes the C7 regression."""
    ctx = ReservationContext(
        status="Confirmed",
        check_in="2026-05-18",
        check_out="2026-05-20",
        total_price="-3000",
        currency="EUR",
    )
    rendered = _format_reservation_context(ctx)

    assert "Total price: -3000" in rendered
    assert "Do NOT compute a total by multiplying" in rendered
    assert "per-night base price" in rendered
    assert "number of nights" in rendered


def test_populated_block_quotes_negative_value_verbatim() -> None:
    """Unusual value (negative) must still be quoted verbatim with
    an explicit "do not silently flip the sign" guard."""
    ctx = ReservationContext(status="Confirmed", total_price="-3000")
    rendered = _format_reservation_context(ctx)
    # Normalise newlines + internal spacing so the assertion does
    # not fragile-match on the formatter's exact line wrapping.
    flat = " ".join(rendered.split())

    assert "Total price: -3000" in flat
    assert "never silently flip the sign" in flat
    assert "negative" in flat


def test_populated_block_defer_when_total_price_missing() -> None:
    """When total_price is NOT among the listed fields the rules
    explicitly tell the model to defer instead of improvising from
    property base price."""
    ctx = ReservationContext(
        status="Confirmed",
        check_in="2026-05-18",
        # total_price intentionally omitted
    )
    rendered = _format_reservation_context(ctx)

    assert "Total price:" not in rendered
    assert "If 'Total price' is NOT listed above" in rendered
    assert "Never improvise a total from property base price" in rendered


# ── no-snapshot rules ───────────────────────────────────────────


def test_no_data_block_forbids_price_compute() -> None:
    """The no-snapshot fallback must also forbid the property
    base price × nights compute path — otherwise the agent could
    fall through to the same hallucination when the request lacks
    a snapshot entirely."""
    block = _RESERVATION_NO_DATA_BLOCK
    flat = " ".join(block.split())  # collapse line wraps

    assert "[RESERVATION FACTS]" in flat
    assert "never compute a" in flat.lower()
    assert "base price" in flat
    assert "list price, not a booking total" in flat


def test_no_data_block_keeps_defer_directive() -> None:
    """The pre-existing deferral directive must remain — it is the
    LLM's escape hatch when neither snapshot nor calendar is
    available."""
    block = _RESERVATION_NO_DATA_BLOCK
    assert "kontrol edip size geri döneceğim" in block
    assert "Never improvise" in block


# ── characterization: structure still stable ─────────────────────


def test_populated_block_still_starts_with_header() -> None:
    """Downstream parsers anchor on the [RESERVATION FACTS] header
    being the first line — the new rules must not have shifted it."""
    ctx = ReservationContext(status="Confirmed")
    rendered = _format_reservation_context(ctx)
    assert rendered.startswith("[RESERVATION FACTS]\n")


def test_populated_block_strict_rules_section_present() -> None:
    """The STRICT RULES anchor must remain — we appended new rules,
    the old anchor must not have been clobbered."""
    ctx = ReservationContext(status="Confirmed")
    rendered = _format_reservation_context(ctx)
    assert "STRICT RULES:" in rendered
    assert "Quote dates and times exactly as listed above." in rendered


def test_empty_context_still_falls_back_to_no_data() -> None:
    """An empty ReservationContext must keep producing the no-data
    fallback — no behavioural drift on the empty path."""
    rendered = _format_reservation_context(ReservationContext())
    assert rendered == _RESERVATION_NO_DATA_BLOCK
