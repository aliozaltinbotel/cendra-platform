"""Hook → Scenario alias registry for the 91 xlsx rule hooks.

The Botel Guest Journey workbook (sheet ``Hook → Data``) catalogues
91 operational rule hooks (ACCESS-RELEASE, BALANCE-PAY, …) that
the property manager's playbook references.  Brain Engine already
knows about :class:`Scenario` and :class:`BookingStage` taxonomies,
but until this module landed the only mapping in code was a partial
``DEFAULT_SCENARIO_TO_ACTION`` table (``orchestrator/resolvers.py``)
covering 10 entries — far short of the workbook's surface.

This registry closes that gap without duplicating either the
:class:`Scenario` enum or the :class:`BookingStage` lifecycle.
Each hook becomes one :class:`HookEntry` row binding the workbook's
hook code to the closest existing :class:`Scenario` and to the
booking-stage tuple where the hook fires (an empty tuple means the
xlsx flagged the hook as ``Cross-cutting (any stage)``).

Hooks whose semantics do not collapse onto an existing Scenario
fall back to :attr:`Scenario.GENERAL` rather than fabricating new
enum members — adding a Scenario is a higher-friction change that
ripples into validators, the priority chain and the gesture
builder.  When a hook genuinely warrants its own Scenario, promote
it explicitly in :mod:`brain_engine.patterns.models`.

The registry is read-mostly: callers (resolvers, validators, the
case builder) look up a hook code and use the resolved Scenario
plus the stage envelope to select downstream policy.  A
module-load assertion guards against silent xlsx drift; if the
workbook ships a 92nd hook the import will fail with a clear
message instead of resolving to a stale snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from brain_engine.patterns.models import BookingStage, Scenario

__all__ = [
    "EXPECTED_HOOK_COUNT",
    "HOOK_REGISTRY",
    "HookEntry",
    "lookup",
    "scenario_for",
    "stages_for",
]


# Module-private aliases used only inside the registry literal
# below.  They let each hook row read as a single-line table entry
# instead of a 5-line vertical block, which matters when the table
# is the dominant artefact in the file.
_SCN = Scenario
_BST = BookingStage


@dataclass(frozen=True, slots=True)
class HookEntry:
    """One hook row from the xlsx ``Hook → Data`` sheet.

    Attributes:
        hook: Workbook hook code (uppercase, hyphenated).
        scenario: Closest matching :class:`Scenario`.  Hooks
            whose semantics do not collapse onto an existing
            scenario use :attr:`Scenario.GENERAL` — see module
            docstring for the rationale.
        stages: Booking-stage envelope.  Empty tuple means the
            hook fires across every stage (the workbook's
            ``Cross-cutting (any stage)`` marker).
    """

    hook: str
    scenario: Scenario
    stages: tuple[BookingStage, ...]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
#
# The dict literal mirrors the workbook row-for-row.  Order is
# preserved from the xlsx so a manual diff against the source sheet
# stays viable.  Stage tuples deduplicate workbook entries that
# repeat the same Brain Engine stage (e.g. "Pre-arrival (to T-48h);
# Pre-check-in (T-48h→0, excl. day)" both collapse to PRE_ARRIVAL).

_HOOK_TABLE: Final[Mapping[str, tuple[Scenario, tuple[BookingStage, ...]]]] = {
    "ACCESS-RELEASE": (_SCN.ACCESS_CODE_RELEASE, (_BST.PRE_ARRIVAL,)),
    "ACCESSIBILITY-PREF": (_SCN.SPECIAL_REQUEST, ()),
    "ADDON-REQUEST": (_SCN.SPECIAL_REQUEST, (_BST.PRE_ARRIVAL,)),
    "AGE-POLICY": (_SCN.GENERAL, (_BST.PRE_BOOKING,)),
    "ALLERGY-ONSITE": (_SCN.COMPLAINT_COMPENSATION, (_BST.IN_STAY,)),
    "ALLERGY-REQUEST": (_SCN.SPECIAL_REQUEST, (_BST.PRE_BOOKING,)),
    "AMENITY-GAP": (_SCN.AMENITY_EXCEPTION, (_BST.IN_STAY,)),
    "BALANCE-PAY": (_SCN.GENERAL, (_BST.PRE_ARRIVAL,)),
    "BILL-DISPUTE": (_SCN.COMPLAINT_COMPENSATION, (_BST.POST_CHECKOUT,)),
    "CHECKIN-GUIDE-SCHEDULE": (_SCN.GENERAL, (_BST.BOOKING_REVIEW,)),
    "CHILD-SAFETY-GEAR": (_SCN.SPECIAL_REQUEST, (_BST.BOOKING_REVIEW,)),
    "DEPOSIT-REFUND": (_SCN.COMPLAINT_COMPENSATION, (_BST.POST_CHECKOUT,)),
    "DISCOUNT-POLICY": (_SCN.DISCOUNT_REQUEST, (_BST.PRE_BOOKING,)),
    "ECI-DECIDE": (_SCN.EARLY_CHECKIN, (_BST.PRE_ARRIVAL,)),
    "ECI-LCO-ENQUIRE": (_SCN.EARLY_CHECKIN, (_BST.PRE_BOOKING,)),
    "EVENT-PERMIT": (_SCN.SPECIAL_REQUEST, (_BST.PRE_BOOKING,)),
    "EXIT-GRACE": (_SCN.LATE_CHECKOUT, (_BST.CHECKOUT,)),
    "EXTEND-STAY": (_SCN.BOOKING_EXTENSION, (_BST.IN_STAY,)),
    "GROCERY-ADDON": (_SCN.SPECIAL_REQUEST, (_BST.PRE_ARRIVAL,)),
    "GUEST-EDIT": (_SCN.MODIFICATION, (_BST.BOOKING_REVIEW,)),
    "GUEST-NOISE-POLICY": (_SCN.NOISE_COMPLAINT, (_BST.IN_STAY,)),
    "HOUSE-CARE": (_SCN.MAINTENANCE_REQUEST, (_BST.IN_STAY,)),
    "INVOICE-FINAL": (_SCN.GENERAL, (_BST.POST_CHECKOUT,)),
    "INVOICE-ISSUE": (_SCN.GENERAL, (_BST.BOOKING_REVIEW,)),
    "INVOICE-REQ": (_SCN.GENERAL, (_BST.PRE_BOOKING,)),
    "LCO-DECIDE": (_SCN.LATE_CHECKOUT, (_BST.CHECKOUT,)),
    "LOCK-BATTERY": (_SCN.MAINTENANCE_REQUEST, (_BST.CHECKIN,)),
    "LOST-FOUND": (_SCN.LOST_ITEM, (_BST.CHECKOUT,)),
    "LOYALTY-OFFER": (_SCN.GENERAL, (_BST.POST_CHECKOUT,)),
    "LUGGAGE-OPTIONS": (_SCN.SPECIAL_REQUEST, (_BST.PRE_ARRIVAL,)),
    "MEDICAL-NEEDS": (_SCN.SPECIAL_REQUEST, (_BST.PRE_ARRIVAL,)),
    "MICRO-EXTEND": (_SCN.BOOKING_EXTENSION, (_BST.CHECKOUT,)),
    "MID-SERVICE": (_SCN.CLEANER_DISPATCH, (_BST.IN_STAY,)),
    "MODIFY-DATES": (_SCN.MODIFICATION, (_BST.PRE_ARRIVAL,)),
    "NOISE-ISSUE": (_SCN.NOISE_COMPLAINT, (_BST.IN_STAY,)),
    "OBSERVANCE-TIMING": (_SCN.SPECIAL_REQUEST, (_BST.PRE_ARRIVAL,)),
    "OCCUPANCY-CHANGE": (_SCN.GUEST_COUNT_MISMATCH, (_BST.IN_STAY,)),
    "PARCEL-POLICY": (_SCN.GENERAL, (_BST.PRE_ARRIVAL,)),
    "PARTY-RISK": (_SCN.GENERAL, (_BST.PRE_BOOKING,)),
    "POST-STORAGE": (_SCN.SPECIAL_REQUEST, (_BST.CHECKOUT,)),
    "PREFERENCE-NOTE": (_SCN.SPECIAL_REQUEST, (_BST.BOOKING_REVIEW,)),
    "PRICING-QUOTE": (_SCN.PRICE_NEGOTIATION, (_BST.PRE_BOOKING,)),
    "SAME-DAY-STORAGE": (_SCN.SPECIAL_REQUEST, (_BST.CHECKIN,)),
    "SMOKING-POLICY": (_SCN.GENERAL, (_BST.IN_STAY,)),
    "SPLIT-PAYMENT": (_SCN.GENERAL, (_BST.BOOKING_REVIEW,)),
    "STAGGERED-ARRIVAL": (_SCN.SPECIAL_REQUEST, (_BST.PRE_ARRIVAL,)),
    "TRANSFER-BOOK": (
        _SCN.SPECIAL_REQUEST,
        (_BST.PRE_ARRIVAL, _BST.CHECKOUT),
    ),
    "WIFI-ISSUE": (_SCN.MAINTENANCE_REQUEST, (_BST.IN_STAY,)),
    "ACCESS-FAIL": (
        _SCN.MAINTENANCE_REQUEST,
        (_BST.CHECKIN, _BST.CHECKOUT, _BST.IN_STAY),
    ),
    "ACCESS-PARKING-INFO": (
        _SCN.PARKING_REQUEST,
        (_BST.IN_STAY, _BST.PRE_BOOKING),
    ),
    "ACCESSIBILITY-INFO": (_SCN.GENERAL, ()),
    "ARRIVAL-INFO": (_SCN.GENERAL, (_BST.PRE_ARRIVAL,)),
    "AUDIT-LOG": (_SCN.GENERAL, ()),
    "AVAIL-CHECK": (_SCN.GENERAL, (_BST.PRE_BOOKING,)),
    "CAPACITY-GUARD": (_SCN.GUEST_COUNT_MISMATCH, ()),
    "CHECKOUT-STEPS": (_SCN.GENERAL, (_BST.CHECKOUT,)),
    "CONF-SUMMARY": (_SCN.GENERAL, (_BST.BOOKING_REVIEW,)),
    "CURRENCY-INFO": (_SCN.GENERAL, ()),
    "DEPOSIT-TERMS": (_SCN.GENERAL, (_BST.PRE_BOOKING,)),
    "EMERGENCY": (_SCN.COMPLAINT_COMPENSATION, (_BST.IN_STAY,)),
    "GDPR-DSR": (_SCN.GENERAL, (_BST.POST_CHECKOUT,)),
    "HOUSE-RULES": (_SCN.GENERAL, (_BST.PRE_BOOKING,)),
    "HVAC-ISSUE": (_SCN.MAINTENANCE_REQUEST, (_BST.IN_STAY,)),
    "ID-VERIFY": (_SCN.GENERAL, (_BST.PRE_ARRIVAL,)),
    "IDENTITY-CHECK": (_SCN.GENERAL, ()),
    "INCLUSIONS-INFO": (_SCN.GENERAL, (_BST.BOOKING_REVIEW,)),
    "INSURANCE-INFO": (_SCN.GENERAL, ()),
    "INVOICE-RESEND": (_SCN.GENERAL, (_BST.POST_CHECKOUT,)),
    "KEY-RETURN": (_SCN.GENERAL, (_BST.CHECKOUT,)),
    "MEDIA-SEND": (_SCN.GENERAL, (_BST.PRE_BOOKING,)),
    "MNT-MINOR": (_SCN.MAINTENANCE_REQUEST, (_BST.IN_STAY,)),
    "ONSITE-CONTACT": (_SCN.GENERAL, ()),
    "PARKING-ASSIGN": (_SCN.PARKING_REQUEST, (_BST.PRE_ARRIVAL,)),
    "PARKING-SPECS": (
        _SCN.PARKING_REQUEST,
        (_BST.CHECKIN, _BST.PRE_BOOKING),
    ),
    "PAY-METHODS": (_SCN.GENERAL, (_BST.PRE_BOOKING,)),
    "PAYMENT-HYGIENE": (_SCN.GENERAL, ()),
    "POLICY-CXL": (_SCN.CANCELLATION_REQUEST, (_BST.PRE_BOOKING,)),
    "PROFILE-COMPLETE": (_SCN.GENERAL, (_BST.BOOKING_REVIEW,)),
    "QUICK-SETUP": (_SCN.GENERAL, (_BST.CHECKIN, _BST.IN_STAY)),
    "READY-ISSUE": (_SCN.MAINTENANCE_REQUEST, (_BST.CHECKIN,)),
    "RECS-LOCAL": (_SCN.GENERAL, (_BST.IN_STAY,)),
    "REVIEW-NUDGE": (_SCN.GENERAL, (_BST.POST_CHECKOUT,)),
    "SAFETY-ESCALATE": (_SCN.COMPLAINT_COMPENSATION, ()),
    "SERVICE-ANIMAL-POLICY": (
        _SCN.PET_POLICY_EXCEPTION,
        (_BST.PRE_BOOKING,),
    ),
    "SUPPORT-CONTACT": (
        _SCN.GENERAL,
        (_BST.BOOKING_REVIEW, _BST.CHECKIN),
    ),
    "SUPPORT-HOURS": (_SCN.GENERAL, ()),
    "TRASH-INFO": (_SCN.GENERAL, (_BST.IN_STAY,)),
    "UTILITY-OUTAGE": (
        _SCN.MAINTENANCE_REQUEST,
        (_BST.CHECKIN, _BST.IN_STAY),
    ),
    "WAYFIND-HELP": (
        _SCN.GENERAL,
        (_BST.CHECKIN, _BST.PRE_ARRIVAL),
    ),
    "WELCOME-NUDGE": (_SCN.GENERAL, (_BST.IN_STAY,)),
    "WIFI-SPECS": (_SCN.GENERAL, (_BST.PRE_ARRIVAL,)),
}


HOOK_REGISTRY: Final[Mapping[str, HookEntry]] = {
    code: HookEntry(hook=code, scenario=scn, stages=stages)
    for code, (scn, stages) in _HOOK_TABLE.items()
}


# Workbook drift guard.  The xlsx ``Hook → Data`` sheet shipped 91
# rows on 2026-05-04; if the source ever ships more or fewer the
# import fails fast so the registry cannot silently fall behind.
EXPECTED_HOOK_COUNT: Final = 91

if len(HOOK_REGISTRY) != EXPECTED_HOOK_COUNT:
    raise RuntimeError(
        f"hook_registry: expected {EXPECTED_HOOK_COUNT} entries, "
        f"got {len(HOOK_REGISTRY)}; xlsx Hook→Data drift detected"
    )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def lookup(hook: str) -> HookEntry | None:
    """Return the :class:`HookEntry` for ``hook`` or ``None``.

    The lookup is case-sensitive: workbook hook codes are uppercase
    by convention and callers should pass them through unchanged.
    """
    return HOOK_REGISTRY.get(hook)


def scenario_for(hook: str) -> Scenario | None:
    """Resolve a hook to its :class:`Scenario`, or ``None`` if absent."""
    entry = HOOK_REGISTRY.get(hook)
    if entry is None:
        return None
    return entry.scenario


def stages_for(hook: str) -> tuple[BookingStage, ...] | None:
    """Resolve a hook to its booking-stage envelope.

    Returns ``None`` when the hook is unknown and an empty tuple
    when the workbook flagged the hook as cross-cutting (every
    stage applies).  Callers should distinguish ``None`` from
    ``()`` rather than treating both as "no stages".
    """
    entry = HOOK_REGISTRY.get(hook)
    if entry is None:
        return None
    return entry.stages
