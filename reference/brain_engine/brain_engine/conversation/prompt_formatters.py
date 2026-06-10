# ruff: noqa: RUF001
# RUF001 (ambiguous unicode) is suppressed file-wide because the
# strict-rule blocks intentionally include Turkish phrases
# ("müsait", "döneceğim", "Rezervasyon süresi dolmuş",
# "Konaklamanız sona ermiş", "Rezervasyondaki misafir sayısını")
# that the LLM must quote verbatim — the Turkish letters are
# operational fidelity, not drift.
"""Prompt-formatter helpers extracted from :mod:`brain_engine.conversation.service`.

Pure functions that turn pipeline state (calendar windows,
reservation snapshots) into the strict Markdown / bullet-list
blocks the LLM system prompt embeds.  None of them touch the
``ConversationService`` instance, the module-level singletons, or
the environment — they take their inputs as arguments and return a
string.

The split exists to keep ``service.py`` under the per-file size
target documented in ``python_master_guide_2026_may.md`` and to
make these helpers easier to test in isolation.  The original
import path (``from brain_engine.conversation.service import
_format_availability_calendar``) keeps working via re-export so
no caller — internal or external — has to change.
"""

from __future__ import annotations

from brain_engine.conversation.prompt_redaction import is_pre_booking_status

__all__ = [
    "_CALENDAR_NO_DATA_BLOCK",
    "_CAPACITY_UNKNOWN_BLOCK",
    "_CURRENT_STAGE_IN_STAY_BLOCK",
    "_CURRENT_STAGE_PRE_ARRIVAL_BLOCK",
    "_EXPIRED_BOOKING_BLOCK",
    "_RESERVATION_NO_DATA_BLOCK",
    "_STALE_RESERVATION_BLOCK",
    "_format_availability_calendar",
    "_format_capacity_sanity_block",
    "_format_current_stage_block",
    "_format_expired_status_block",
    "_format_reservation_context",
    "_format_stale_reservation_block",
]

import re

_ISO_DATE_PREFIX: re.Pattern[str] = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _date_part(value: str) -> str:
    """Return only the ``YYYY-MM-DD`` prefix of an ISO datetime.

    Mirrors the same extraction logic ``api_server.server._iso_date_only``
    uses for inbound state, kept inline here so this module stays
    self-contained.  Non-ISO inputs return ``""`` — the caller then
    treats the value as "unparseable" and short-circuits.
    """
    match = _ISO_DATE_PREFIX.match(value or "")
    return match.group(1) if match is not None else ""


_STALE_RESERVATION_BLOCK = (
    "## STALE RESERVATION — HARD DEFERRAL\n"
    "The reservation in [RESERVATION FACTS] has a check-out date\n"
    "earlier than the current turn time.  The stay has ENDED and\n"
    "the booking is historical, not active.\n"
    "STRICT RULES (apply BEFORE every other instruction below):\n"
    "- Do NOT confirm the reservation as current.  Refer to it in\n"
    "  the past tense or note that it has ended.\n"
    "- Do NOT share access codes, WiFi password, lock box code,\n"
    "  building entry code, safe code, or property GPS.  The guest\n"
    "  is no longer entitled to property access on a closed stay.\n"
    "- Do NOT offer to 'modify the reservation' or 'extend the\n"
    "  stay' — a closed booking is not modifiable from the chat;\n"
    "  a fresh inquiry is required.\n"
    "- If the guest reports an issue from the stay (refund,\n"
    "  cleaning, lost item), acknowledge politely and escalate to\n"
    "  the property manager — do not promise resolutions.\n"
    "- For escalation defer with a short 'Konaklamanız sona ermiş,\n"
    "  durumu kontrol edip size hemen dönüş yapacağım' (TR) or the\n"
    "  English / Russian equivalent."
)


def _format_stale_reservation_block(
    check_out: str,
    current_time: str,
) -> str:
    """Emit the hard-deferral block when the booking has ended.

    Triggers when both ``check_out`` and ``current_time`` parse to
    ISO date prefixes and ``current_time`` lies strictly after
    ``check_out``.  Defensive complement to the explicit-Expired
    block (R12) — catches cases where the PMS / UI never flipped
    the status label even though the dates make the booking
    historical (sync lag, sandbox testing with fixed dates,
    cancelled-but-not-relabelled bookings).

    Returns:
        :data:`_STALE_RESERVATION_BLOCK` when the comparison
        succeeds, ``""`` otherwise.  Empty inputs, malformed
        dates, or ``current_time`` ≤ ``check_out`` all collapse
        to the empty path so active reservations stay
        byte-identical to the pre-R13 prompt.
    """
    out_date = _date_part(check_out)
    cur_date = _date_part(current_time)
    if not out_date or not cur_date:
        return ""
    if cur_date <= out_date:
        return ""
    return _STALE_RESERVATION_BLOCK


_EXPIRED_BOOKING_BLOCK = (
    "## EXPIRED BOOKING — HARD DEFERRAL\n"
    "The reservation status is ``expired``.  This booking is NO\n"
    "LONGER ACTIVE.  The dates in [RESERVATION FACTS] describe a\n"
    "stay that has ended or was cancelled; they are historical, not\n"
    "current.\n"
    "STRICT RULES (apply BEFORE every other instruction below):\n"
    "- Do NOT confirm the reservation or quote its dates as if they\n"
    "  still apply.  Say the reservation has expired.\n"
    "- Do NOT share access codes, WiFi password, lock box code,\n"
    "  building entry code, safe code, or property GPS / exact\n"
    "  address.  An expired booking has no entitlement to property\n"
    "  access.\n"
    "- Do NOT offer to 'check availability today' or 'modify the\n"
    "  reservation' — an expired booking cannot be modified; the\n"
    "  guest must open a fresh inquiry.\n"
    "- Politely inform the guest the reservation has expired.  If\n"
    "  they want to book again, direct them to start a new inquiry\n"
    "  through the booking channel.\n"
    "- For any escalation defer with a short 'Rezervasyon süresi\n"
    "  dolmuş, durumu kontrol edip size hemen dönüş yapacağım'\n"
    "  (TR) or the English / Russian equivalent."
)


_EXPIRED_STATUS_LABELS: frozenset[str] = frozenset(
    {"expired"},
)


def _format_expired_status_block(status: str) -> str:
    """Render the hard-deferral block when the booking is expired.

    Returns:
        :data:`_EXPIRED_BOOKING_BLOCK` when ``status`` (case-
        insensitive, whitespace-tolerant) is one of the labels
        the UI / PMS uses for an expired booking.  Empty string
        otherwise — the assembled prompt then stays
        byte-identical to the pre-R12 path for every active
        reservation status.
    """
    needle = (status or "").strip().lower()
    if needle in _EXPIRED_STATUS_LABELS:
        return _EXPIRED_BOOKING_BLOCK
    return ""


_CAPACITY_UNKNOWN_BLOCK = (
    "## CAPACITY UNKNOWN — CAUTION\n"
    "The reservation snapshot has no confirmed guest count\n"
    "(Adults=0 and Children=0) even though the booking is in an\n"
    "active stage.  This is suspicious — a real reservation should\n"
    "carry at least one adult.\n"
    "STRICT RULES for any capacity / occupancy / 'bring more\n"
    "people' question on this turn:\n"
    "- Do NOT compute additions against a zero base ('0 + N = N')\n"
    "  — the missing base is a data gap, not a fact.\n"
    "- Do NOT promise the guest can bring N additional people\n"
    "  based on the property max alone — the per-stay headcount\n"
    "  the booking pays for is the ceiling, not just the property.\n"
    "- Politely ask the guest to confirm the original booking\n"
    "  headcount, or defer with a short 'Rezervasyondaki misafir\n"
    "  sayısını kontrol edip size geri döneceğim' (TR) /\n"
    "  equivalent so the property manager can verify.\n"
    "- All other (non-capacity) topics in the message can still\n"
    "  be answered normally."
)


def _format_capacity_sanity_block(
    status: str,
    num_guests: int,
    num_children: int,
) -> str:
    """Emit the capacity-unknown block on an active booking with no guests.

    Triggers when both numeric counts are zero AND the status is
    populated AND not in the pre-booking set — pre-booking
    (``inquiry`` / ``follow_up``) legitimately has no confirmed
    guest count yet, so a "bring 3 friends" question there is
    asking about the property max occupancy, not adding to a real
    booking.  In post-booking states, however, a zero count means
    the snapshot is incomplete and brain must NOT silently treat
    that as "0 guests so far".

    Returns:
        :data:`_CAPACITY_UNKNOWN_BLOCK` when all three conditions
        hold, ``""`` otherwise.  Empty inputs collapse to the
        empty path so legacy callers stay byte-identical.
    """
    if num_guests or num_children:
        return ""
    label = (status or "").strip()
    if not label:
        return ""
    if is_pre_booking_status(label):
        return ""
    return _CAPACITY_UNKNOWN_BLOCK


_CURRENT_STAGE_PRE_ARRIVAL_BLOCK = (
    "## CURRENT STAGE — PRE-ARRIVAL (derived from calendar)\n"
    "The message timestamp on the reservation snapshot is BEFORE\n"
    "the check-in date.  The guest has NOT yet checked in; the\n"
    "stay has not begun.\n"
    "STRICT RULES:\n"
    "- Sensitive access info (door code, lock box code, building\n"
    "  entry code, safe code, WiFi password, exact GPS / address)\n"
    "  must NOT be released on this turn — the release window\n"
    "  opens at check-in.  Defer politely with a short 'check-in\n"
    "  tarihinize yakın bu bilgileri ileteceğim' (TR) /\n"
    "  equivalent.\n"
    "- The literal 'Status' field on the snapshot is a static PMS\n"
    "  label (e.g. ``confirmed``).  This derived stage is the\n"
    "  authoritative signal for sensitive-info gating — do not\n"
    "  refuse based on the literal status when this block is\n"
    "  present, and do not release based on the literal status\n"
    "  when this block says PRE-ARRIVAL.\n"
    "- Non-sensitive questions (check-in time, parking directions,\n"
    "  amenities) can still be answered normally."
)


_CURRENT_STAGE_IN_STAY_BLOCK = (
    "## CURRENT STAGE — IN STAY (derived from calendar)\n"
    "The message timestamp on the reservation snapshot lies\n"
    "BETWEEN check-in and check-out.  The guest IS currently\n"
    "staying at the property right now.\n"
    "STRICT RULES:\n"
    "- Sensitive access info (door code, lock box code, building\n"
    "  entry code, WiFi password) IS allowed to be released on\n"
    "  this turn — the guest is in stay and entitled to property\n"
    "  access.  Quote the value verbatim when available in the\n"
    "  property knowledge / PM facts; defer ('kontrol edip size\n"
    "  geri döneceğim') only when the value is genuinely missing\n"
    "  from the snapshot, never because of a pre-arrival policy.\n"
    "- The literal 'Status' field on the snapshot may be a static\n"
    "  PMS label (``confirmed``) that does not change after\n"
    "  check-in.  This derived stage is the authoritative signal\n"
    "  for sensitive-info gating — do not refuse to release on\n"
    "  the basis that the status reads ``confirmed`` rather than\n"
    "  ``currently_hosting``.\n"
    "- The exact-address field stays guarded only when the\n"
    "  property knowledge marks it as not-yet-shared by the PM;\n"
    "  otherwise it can be quoted to an in-stay guest who has\n"
    "  asked for it."
)


def _format_current_stage_block(
    status: str,
    check_in: str,
    check_out: str,
    current_time: str,
) -> str:
    """Render a derived-stage block from calendar + ``current_time``.

    Closes a Sandbox UI tester report (2026-05-20): brain refused to
    release the door code when ``current_time`` (2026-06-11) was
    already AFTER ``check_in`` (2026-06-10) — the guest was inside
    the property but still got a "we will share closer to check-in"
    deferral.  Root cause: the literal ``Status`` field on the
    snapshot stays ``confirmed`` across the entire booking lifecycle,
    so neither the LLM nor the operational-policy matcher had an
    explicit "currently in stay" signal once the check-in date
    passed.

    This helper bridges that gap by computing the derived stage from
    the three calendar fields and emitting one of two strict-rule
    blocks the LLM reads in the primacy slot of the system prompt:

    * Pre-arrival (``current_time < check_in``) — block sensitive
      access info release; defer.
    * In stay (``check_in <= current_time <= check_out``) — allow
      sensitive access info release.

    The post-checkout case (``current_time > check_out``) is
    intentionally NOT handled here — :func:`_format_stale_reservation_block`
    already emits a stronger hard-deferral block for that path
    (R13).  Likewise, when ``status`` is ``expired``,
    :func:`_format_expired_status_block` already wins and this
    helper short-circuits.

    Args:
        status: Literal reservation status from the snapshot.  Used
            only to short-circuit when an upstream hard-deferral
            block (``expired``) has already handled the turn.
        check_in: ISO date (``YYYY-MM-DD``) or ISO timestamp from
            the snapshot.
        check_out: ISO date or ISO timestamp from the snapshot.
        current_time: ISO timestamp the UI tagged the message with.

    Returns:
        :data:`_CURRENT_STAGE_PRE_ARRIVAL_BLOCK` when the moment is
        before check-in, :data:`_CURRENT_STAGE_IN_STAY_BLOCK` when
        the moment lies within the stay window, ``""`` otherwise
        (any input unparseable, post-checkout case, expired
        status).  Empty inputs collapse to ``""`` so existing
        callers stay byte-identical when no calendar context is
        attached.
    """
    needle = (status or "").strip().lower()
    if needle in _EXPIRED_STATUS_LABELS:
        return ""

    in_date = _date_part(check_in)
    out_date = _date_part(check_out)
    cur_date = _date_part(current_time)
    if not in_date or not out_date or not cur_date:
        return ""

    if cur_date > out_date:
        return ""

    if cur_date < in_date:
        return _CURRENT_STAGE_PRE_ARRIVAL_BLOCK

    return _CURRENT_STAGE_IN_STAY_BLOCK


_CALENDAR_NO_DATA_BLOCK = (
    "[CALENDAR AVAILABILITY]\n"
    "No availability snapshot is attached to this turn.\n"
    "STRICT RULES:\n"
    "- Do NOT claim a date is available, free, müsait, доступно or\n"
    "  bookable. Do NOT claim it is blocked either.\n"
    "- For any availability / extension / new-booking request, defer\n"
    "  with 'müsaitlik durumunu kontrol edip size hemen geri\n"
    "  döneceğim' (TR) or the English/Russian equivalent. Never\n"
    "  improvise an availability answer."
)


def _format_availability_calendar(
    calendar: object,
) -> str:
    """Render the per-day calendar window as a strict prompt block.

    Each day is reported with its derived status (``available`` /
    ``blocked`` / ``unknown``) plus the channel-side stop-sell flag
    and unit count, so the LLM can quote the line back without
    inventing missing context.  When the window is empty (no GraphQL
    data, or the caller did not request one), the function still
    emits a directive that forces a deferral on availability questions
    — this is the failure mode that produced the 2026-04-28 demo bug
    where the model said "müsait" for a date the channel had blocked.

    Args:
        calendar: Iterable of :class:`CalendarDay` (or anything
            exposing ``date`` / ``status`` / ``available_units`` /
            ``stop_sell``).  ``None`` and empty iterables both fall
            through to the "no data" block.

    Returns:
        A multiline ``[CALENDAR AVAILABILITY]`` block.  Always
        non-empty so prompt assembly stays deterministic.
    """
    if not calendar:
        return _CALENDAR_NO_DATA_BLOCK

    rows: list[str] = []
    for day in calendar:
        date_value = getattr(day, "date", "") or ""
        status = getattr(day, "status", "") or "unknown"
        units = getattr(day, "available_units", 0) or 0
        stop_sell = bool(getattr(day, "stop_sell", False))
        if not date_value:
            continue
        flags: list[str] = [f"status={status}"]
        flags.append(f"units={units}")
        if stop_sell:
            flags.append("stopSell=true")
        # Price + currency come straight from the channel calendar so
        # the model can quote the per-night rate without inventing a
        # number.  Empty price means the channel did not publish one
        # for that day — keep the row but omit the field.
        price = (getattr(day, "price", "") or "").strip()
        currency = (getattr(day, "currency", "") or "").strip()
        if price:
            flags.append(
                f"price={price} {currency}".strip()
                if currency
                else f"price={price}"
            )
        note = (getattr(day, "note", "") or "").strip()
        if note:
            flags.append(f"note={note}")
        rows.append(f"- {date_value}: " + ", ".join(flags))

    if not rows:
        return _CALENDAR_NO_DATA_BLOCK

    body = "\n".join(rows)
    return (
        "[CALENDAR AVAILABILITY]\n"
        "These rows come straight from the unified rate-plan calendar\n"
        "(ES `unified_rateplans`) — they are authoritative.\n"
        f"{body}\n"
        "STRICT RULES:\n"
        "- A date with status=blocked or stopSell=true is NOT bookable.\n"
        "  Never tell the guest it is müsait / available / свободно.\n"
        "- A date that is NOT listed above is outside the snapshot —\n"
        "  treat it as unknown and defer with 'kontrol edip size geri\n"
        "  döneceğim'. Never guess.\n"
        "- For a stay extension, every requested night must appear with\n"
        "  status=available AND units>0; otherwise refuse and defer.\n"
        "- Prices: when the guest asks how much an extension / new\n"
        "  booking costs, quote ONLY the per-night ``price`` listed\n"
        "  above. If a night has no ``price`` field, do NOT invent one\n"
        "  — say you will confirm the rate and get back."
    )


_RESERVATION_NO_DATA_BLOCK = (
    "[RESERVATION FACTS]\n"
    "No reservation snapshot is attached to this turn.\n"
    "STRICT RULES (apply even when no snapshot is attached):\n"
    "- Never invent or guess check-in / check-out dates, times,\n"
    "  guest counts, property names, prices, or booking channel.\n"
    "- For total price / total cost questions, never compute a\n"
    "  total by multiplying property base price by number of nights\n"
    "  — defer instead.  The property base price is a list price,\n"
    "  not a booking total, and the agent must never present it as\n"
    "  the guest's reservation total.\n"
    "- If the guest asks for any of these and no snapshot is provided,\n"
    "  defer with a short message such as 'Rezervasyon bilgilerinizi\n"
    "  kontrol edip size geri döneceğim' (TR) or the English/Russian\n"
    "  equivalent. Never improvise."
)


def _format_reservation_context(
    context: object | None,
) -> str:
    """Render a :class:`ReservationContext` as a grounded prompt block.

    The block lists every populated field verbatim and pins the model
    with explicit anti-fabrication rules — empty fields must be
    surfaced as a deferral, never patched with a guess.  When no
    snapshot is attached, the function still emits the strict-rule
    block so the agent defers instead of inventing data on the fly.

    Args:
        context: Either a :class:`ReservationContext` instance or
            ``None`` when the caller did not attach reservation data.

    Returns:
        A multiline ``[RESERVATION FACTS]`` block.  Always non-empty.
    """
    if context is None:
        return _RESERVATION_NO_DATA_BLOCK

    fields: list[tuple[str, str]] = []

    def _add(label: str, value: object) -> None:
        if value in (None, "", 0):
            return
        fields.append((label, str(value)))

    _add("Status", getattr(context, "status", ""))
    _add("Check-in date", getattr(context, "check_in", ""))
    _add("Check-in time", getattr(context, "check_in_time", ""))
    _add("Check-out date", getattr(context, "check_out", ""))
    _add("Check-out time", getattr(context, "check_out_time", ""))
    _add("Guest name", getattr(context, "guest_name", ""))
    _add("Adults", getattr(context, "num_guests", 0))
    _add("Children", getattr(context, "num_children", 0))
    _add("Property name", getattr(context, "property_name", ""))
    _add("Booking channel", getattr(context, "booking_channel", ""))
    _add("Total price", getattr(context, "total_price", ""))
    _add("Currency", getattr(context, "currency", ""))
    _add("Payment status", getattr(context, "payment_status", ""))
    _add("Message sent at", getattr(context, "current_time", ""))

    if not fields:
        return _RESERVATION_NO_DATA_BLOCK

    body = "\n".join(f"- {label}: {value}" for label, value in fields)
    return (
        "[RESERVATION FACTS]\n"
        "These values are authoritative — never substitute, round, or\n"
        "translate them into a different month / year / time:\n"
        f"{body}\n"
        "STRICT RULES:\n"
        "- Quote dates and times exactly as listed above.\n"
        "- The 'Check-in time' value is the ARRIVAL time (when the\n"
        "  guest is allowed to enter the property). The\n"
        "  'Check-out time' value is the DEPARTURE time (when the\n"
        "  guest must leave). Never swap them, never quote one as\n"
        "  if it were the other, and never invent a 'standard'\n"
        "  arrival or departure time that differs from the value\n"
        "  listed above. When a guest asks to change one (e.g.\n"
        "  extend check-out, arrive early), quote that specific\n"
        "  field's value as the baseline — never substitute the\n"
        "  other field's value, the guest's proposed value, or a\n"
        "  guess.\n"
        "- For total price / total cost questions, quote ONLY the\n"
        "  'Total price' value listed above (echo verbatim, including\n"
        "  sign and currency).  Do NOT compute a total by multiplying\n"
        "  a per-night base price by the number of nights — that path\n"
        "  produces a fabricated number that contradicts the\n"
        "  authoritative snapshot.\n"
        "- If 'Total price' is NOT listed above, defer with a short\n"
        "  'kontrol edip size geri döneceğim' (TR) / equivalent.\n"
        "  Never improvise a total from property base price.\n"
        "- If the listed 'Total price' looks unusual (negative, zero,\n"
        "  missing currency), still quote it verbatim and offer to\n"
        "  verify with the property manager — never silently flip the\n"
        "  sign or substitute a 'reasonable' number.\n"
        "- For a field that is NOT listed (missing from the snapshot),\n"
        "  do not invent a value — defer with a short 'kontrol edip\n"
        "  size geri döneceğim' (TR) / equivalent so the property\n"
        "  manager is asked.\n"
        "- Never reformat 2026 as 2025 or April as March; copy the\n"
        "  exact tokens given here."
    )
