"""Read-side guards for Mümin's pre-computed ES fields (Risk 3).

Mümin's onboarding-api lazy-fills five fields on top of the unified
ElasticSearch documents:

- ``UnifiedReservation.previousBookingGapNights``
- ``UnifiedReservation.nextBookingGapNights``
- ``UnifiedGuest.bookingHistoryCount``
- ``UnifiedGuest.avgReviewRating``
- ``UnifiedGuest.pastComplaintsCount``

Older ES documents written before the precompute pipeline shipped do
not carry these fields.  They are populated only on the next write
that touches the doc — until then, the field reads back as ``null``
(or absent).

Brain Engine MUST distinguish three states when consuming these
fields:

- **FRESH** — at least one expected field is populated.  Use the
  pre-computed value, no fallback round-trip.
- **MISSING** — every expected field is ``null`` / absent.  The
  caller MUST NOT treat this as "no neighbor / first-time guest";
  it must either fall back to an explicit query or surface a
  "data warming up" UI state.

Treating ``MISSING`` as an authoritative *zero* would silently
degrade decisions ("first-time guest" when the guest has 30 prior
stays).  This module gives the cascade-consumer (Etap 2) and the
pattern-runtime (Etap 5) a strict, typed entry point so the wrong
shortcut becomes impossible to take by accident.

Pure module — no I/O, no external dependencies.  Easy to unit-test
and safe to import from any layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping


# ─── Field name registries ─────────────────────────────────────────── #


_RESERVATION_PRECOMPUTE_FIELDS: Final[tuple[str, ...]] = (
    "previousBookingGapNights",
    "nextBookingGapNights",
)

_GUEST_PRECOMPUTE_FIELDS: Final[tuple[str, ...]] = (
    "bookingHistoryCount",
    "avgReviewRating",
    "pastComplaintsCount",
)


# ─── Public types ──────────────────────────────────────────────────── #


class PrecomputeFreshness(Enum):
    """Whether a unified ES document carries the precompute fields.

    Two-state intentionally — distinguishing genuine *absence* from
    *not-yet-backfilled* requires a follow-up query the caller must
    decide whether to spend.  ``MISSING`` is the conservative state
    callers must handle without assuming defaults.
    """

    FRESH = "fresh"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class ReservationPrecompute:
    """Validated read of pre-computed reservation neighbor gaps.

    Attributes:
        previous_gap_nights: Nights between this booking's arrival and
            the previous active, non-playground neighbor's departure.
            ``None`` when there is no neighbor (legitimately) or when
            the field has not yet been backfilled — see ``freshness``.
        next_gap_nights: Mirror of ``previous_gap_nights`` for the
            following neighbor.
        freshness: ``FRESH`` when at least one gap is populated;
            ``MISSING`` when both are null / absent.  The caller MUST
            NOT treat ``MISSING`` as "no neighbor".
    """

    previous_gap_nights: int | None
    next_gap_nights: int | None
    freshness: PrecomputeFreshness

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> ReservationPrecompute:
        """Hydrate from a unified-reservation ES document.

        Accepts either the top-level doc or the ``data`` sub-mapping
        Mümin's pipelines emit; the helper looks in both layers so
        callers do not need to remember which adapter they came
        through.
        """
        prev_raw = _read_field(doc, "previousBookingGapNights")
        next_raw = _read_field(doc, "nextBookingGapNights")
        prev_gap = _coerce_int(prev_raw)
        next_gap = _coerce_int(next_raw)
        freshness = (
            PrecomputeFreshness.FRESH
            if prev_gap is not None or next_gap is not None
            else PrecomputeFreshness.MISSING
        )
        return cls(
            previous_gap_nights=prev_gap,
            next_gap_nights=next_gap,
            freshness=freshness,
        )

    @property
    def is_fresh(self) -> bool:
        """Convenience flag mirroring :attr:`freshness`."""
        return self.freshness is PrecomputeFreshness.FRESH


@dataclass(frozen=True, slots=True)
class GuestPrecompute:
    """Validated read of pre-computed guest aggregates.

    Attributes:
        booking_history_count: Distinct non-cancelled, non-playground
            reservations under the same customer matching by email or
            phone.  ``None`` when the field has not been backfilled.
        avg_review_rating: Mean rating across the same guest's
            reviews under the customer.  ``None`` when no reviews
            *or* when the field has not been backfilled.
        past_complaints_count: Count of guest reviews with
            normalized rating below the v1 threshold (< 3).  ``None``
            when not yet backfilled; ``0`` is a legitimate value.
        freshness: ``FRESH`` when at least one of the three fields is
            populated; ``MISSING`` otherwise.
    """

    booking_history_count: int | None
    avg_review_rating: float | None
    past_complaints_count: int | None
    freshness: PrecomputeFreshness

    @classmethod
    def from_doc(cls, doc: Mapping[str, Any]) -> GuestPrecompute:
        """Hydrate from a unified-guest ES document."""
        history_raw = _read_field(doc, "bookingHistoryCount")
        rating_raw = _read_field(doc, "avgReviewRating")
        complaints_raw = _read_field(doc, "pastComplaintsCount")
        history = _coerce_int(history_raw)
        rating = _coerce_float(rating_raw)
        complaints = _coerce_int(complaints_raw)
        freshness = (
            PrecomputeFreshness.FRESH
            if (
                history is not None
                or rating is not None
                or complaints is not None
            )
            else PrecomputeFreshness.MISSING
        )
        return cls(
            booking_history_count=history,
            avg_review_rating=rating,
            past_complaints_count=complaints,
            freshness=freshness,
        )

    @property
    def is_fresh(self) -> bool:
        """Convenience flag mirroring :attr:`freshness`."""
        return self.freshness is PrecomputeFreshness.FRESH


# ─── Field listings (exported for tests / observability) ───────────── #


def reservation_precompute_field_names() -> tuple[str, ...]:
    """Return the canonical list of reservation precompute field names."""
    return _RESERVATION_PRECOMPUTE_FIELDS


def guest_precompute_field_names() -> tuple[str, ...]:
    """Return the canonical list of guest precompute field names."""
    return _GUEST_PRECOMPUTE_FIELDS


# ─── Internals ────────────────────────────────────────────────────── #


def _read_field(doc: Mapping[str, Any], name: str) -> Any:
    """Look up ``name`` at the top level and inside ``data`` sub-doc.

    Mümin's GraphQL surface flattens to top-level fields, but the raw
    ES source is wrapped in a ``data`` envelope.  Accepting both
    shapes here keeps the call sites adapter-agnostic.
    """
    if name in doc:
        return doc[name]
    nested = doc.get("data")
    if isinstance(nested, Mapping):
        return nested.get(name)
    return None


def _coerce_int(value: Any) -> int | None:
    """Coerce a JSON-ish value to ``int`` or ``None``.

    JSON unmarshallers may surface integers as ``int``, ``float`` or
    string-encoded numbers depending on the path.  We accept all three
    shapes; anything else folds to ``None`` so callers never receive
    a non-``int`` past this boundary.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is subclass of int — refuse
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            try:
                return int(float(stripped))
            except ValueError:
                return None
    return None


def _coerce_float(value: Any) -> float | None:
    """Coerce a JSON-ish value to ``float`` or ``None``.

    Mirrors :func:`_coerce_int` for fields like ``avgReviewRating``
    that are stored as ``double`` in ES but may surface as ``Decimal``
    via asyncpg or as a string from a generic adapter.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None
