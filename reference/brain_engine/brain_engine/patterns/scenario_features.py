"""Per-scenario feature whitelist for ConditionSynthesizer (Sprint H).

Closes Mümin's 2026-05-06 complaint that the ``access_code_release``
rule on property 323133 mined ``currency``, ``total_price``, ``source``
and ``status`` as conditions despite being domain-irrelevant for an
access-code release decision.  The root cause is the global
``_PMS_KEYS`` whitelist in ``condition_synthesizer.py`` — every
scenario sees the same candidate features regardless of semantics.

Until Sprint I (the foundation analysis pipeline that learns
per-scenario feature importance from 6-month history) replaces this
with a data-driven solution, scenarios listed here opt out of one or
more global defaults and use a hand-curated subset.

The whitelist is gated by ``BRAIN_SCENARIO_FEATURES_ENABLED`` —
default off, so behaviour is bit-for-bit identical to pre-Sprint-H
until the team explicitly opts in.  Scenarios *not* listed in
:data:`SCENARIO_FEATURES` (and any source left as ``None`` on a
partial override) fall back to the global defaults regardless of the
flag state.

Add a new entry only when a real complaint or domain expert has said
specific features are spurious for that scenario.  Never invent
domain knowledge here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from brain_engine.patterns.models import Scenario


@dataclass(frozen=True, slots=True)
class FeatureWhitelist:
    """Per-source feature key lists for a single scenario.

    Each tuple replaces the corresponding global default during
    feature flattening.  ``None`` means "use the default", so partial
    overrides are possible (e.g. only constrain PMS keys, leave
    calendar and guest keys at the global default).

    Attributes:
        pms_keys: Keys copied from ``case.pms_snapshot`` when the
            scenario matches.  ``None`` falls back to ``_PMS_KEYS``.
        calendar_keys: Keys copied from ``case.calendar_snapshot``.
            ``None`` falls back to ``_CALENDAR_KEYS``.
        guest_keys: Keys copied from ``case.guest_snapshot``.
            ``None`` falls back to ``_GUEST_KEYS``.
    """

    pms_keys: tuple[str, ...] | None = None
    calendar_keys: tuple[str, ...] | None = None
    guest_keys: tuple[str, ...] | None = None


# Shared PMS-key set for the "timing-and-occupancy" family of
# scenarios — access code release, late checkout, early check-in.
# Each is gated by *where the guest is in the stay* (``stage``,
# ``hours_before_checkin``, ``lead_time_hours``) and *how many
# people are arriving / departing* (``adults``, ``children``,
# ``infants``), with ``payment_status`` retained because some PMs
# gate these decisions behind confirmed payment.  Money
# (``total_price``, ``currency``), distribution channel
# (``source``) and confirmation status (``status``) are spurious
# correlations of the property's booking mix and are intentionally
# excluded so the synthesiser cannot bake them into a rule.
#
# Mümin's 2026-05-06 access-code complaint and 2026-05-08
# late-checkout screenshot both reduce to the same root cause: the
# rule had ``source eq 'bookingcom'`` AND ``total_price gte XX``
# baked in, so any non-``bookingcom`` or low-price reservation
# silently failed condition matching and the PM saw "rule sometimes
# fires, sometimes doesn't".
_TIMING_OCCUPANCY_PMS_KEYS: Final[tuple[str, ...]] = (
    "stage",
    "hours_before_checkin",
    "lead_time_hours",
    "adults",
    "children",
    "infants",
    "payment_status",
)


_TIMING_OCCUPANCY_WHITELIST: Final[FeatureWhitelist] = FeatureWhitelist(
    pms_keys=_TIMING_OCCUPANCY_PMS_KEYS,
)


SCENARIO_FEATURES: Final[Mapping[Scenario, FeatureWhitelist]] = {
    Scenario.ACCESS_CODE_RELEASE: _TIMING_OCCUPANCY_WHITELIST,
    Scenario.LATE_CHECKOUT: _TIMING_OCCUPANCY_WHITELIST,
    Scenario.EARLY_CHECKIN: _TIMING_OCCUPANCY_WHITELIST,
}


__all__ = ["SCENARIO_FEATURES", "FeatureWhitelist"]
