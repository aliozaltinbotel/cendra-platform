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


# Vertical vocabulary note (genericised at port time, golden rule 4):
# scenario keys are opaque str kinds and the kernel ships an EMPTY
# mapping — with the flag on but no entries, behaviour equals the
# global defaults.  Vertical packs supply curated whitelists (see
# packs/hospitality/scenario_features.yaml for the reference's
# timing-and-occupancy entries); the pack loader (Batch 6) populates a
# per-tenant mapping that callers pass to the synthesiser.
SCENARIO_FEATURES: Final[Mapping[str, FeatureWhitelist]] = {}


__all__ = ["SCENARIO_FEATURES", "FeatureWhitelist"]
