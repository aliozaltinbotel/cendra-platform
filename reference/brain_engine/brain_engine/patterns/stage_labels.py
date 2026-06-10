"""Excel-format Stage / Stage Group labels for foundation scenarios.

The canonical ``FOUNDATION_469_SCENARIOS.xlsx`` reference workbook
expresses the booking lifecycle as two display columns:

* ``Stage Group`` — long form: ``"Stage N — <stage_label>"``
  (e.g. ``"Stage 2 — Booking confirmation"``).
* ``Stage`` — short form chosen by the workbook author per
  ``stage_number`` (e.g. ``"Booking confirmation"``).

Both strings must match the workbook verbatim so the API and the
shared spreadsheet stay readable side by side.  Earlier code lived
inside :mod:`api_server.routers.foundation_audit`; extracting the
data + helpers into this module lets the ``/patterns/rules`` list
projection reuse the same mapping without an import cycle through
the FastAPI router layer.

The mapping is intentionally *data* — not derived from any other
constant — so future workbook refreshes that touch the short
labels can be reflected here with a one-line edit and a test
update.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "STAGE_SHORT_LABEL",
    "format_stage_group",
    "lookup_stage_short",
]


# Excel ``Stage`` column (short label) per ``stage_number``, taken
# verbatim from ``FOUNDATION_469_SCENARIOS.xlsx``.  Unknown stage
# numbers fall back to the empty string; callers decide whether to
# substitute ``stage_label`` from the catalog row.
STAGE_SHORT_LABEL: Final[dict[int, str]] = {
    1: "Pre-booking",
    2: "Booking confirmation",
    3: "Pre-arrival",
    4: "Check-in day",
    5: "During stay",
    6: "Upsell / revenue",
    7: "Check-out",
    8: "Post-stay",
    9: "Internal operations",
}


def format_stage_group(
    stage_number: int | None,
    stage_label: str,
) -> str:
    """Render the Excel ``Stage Group`` column verbatim.

    Returns ``"Stage {N} — {stage_label}"`` when both inputs are
    available, otherwise falls back to ``stage_label`` alone so the
    string never becomes ``"Stage None — …"`` for catalog rows that
    pre-date the ``stage_number`` enrichment.

    Args:
        stage_number: 1-9 stage index from the foundation row, or
            ``None`` when the row was loaded from a legacy schema.
        stage_label: Long label from the foundation row (e.g.
            ``"Booking confirmation"``).  An empty string is a
            valid input — the helper returns ``""`` rather than
            raising so the API surface degrades to a blank field
            instead of an exception.

    Returns:
        The composed ``Stage Group`` string suitable for direct API
        emission.
    """
    if stage_number and stage_label:
        return f"Stage {stage_number} — {stage_label}"
    return stage_label


def lookup_stage_short(
    stage_number: int | None,
    stage_label_fallback: str = "",
) -> str:
    """Return the Excel ``Stage`` short label for ``stage_number``.

    Mirrors :data:`STAGE_SHORT_LABEL` lookup with a deterministic
    fallback: when the number is missing or out of range, the
    caller's ``stage_label_fallback`` is used so the field stays
    informative even for catalog rows that fell outside the 1-9
    bucket.

    Args:
        stage_number: 1-9 stage index from the foundation row, or
            ``None``.  Values outside the dictionary fall through
            to ``stage_label_fallback``.
        stage_label_fallback: String returned when the lookup
            misses.  Defaults to the empty string.

    Returns:
        Short stage label, or ``stage_label_fallback`` when the
        ``stage_number`` is unknown.
    """
    if stage_number is None:
        return stage_label_fallback
    return STAGE_SHORT_LABEL.get(stage_number, stage_label_fallback)
