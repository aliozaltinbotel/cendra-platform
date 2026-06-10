"""GR00T P1 Kinematic Planner styles.

A *style* is the high-level behavior envelope the Planner layer
selects before the Foundation (skill handler) layer executes.  Under
the GR00T P1 pattern (NVIDIA GR00T-WholeBodyControl, SONIC paper
arXiv:2511.07820, Nov 2025), the choice of style is what makes the
same action class behave differently across owners, jurisdictions,
and risk regimes.  The DSL of Moat #2 compiles into
:class:`PlannerStyleSpec` records; the runtime selector of
:class:`core.brain.planning.selector.StyleSelector` (Moat #4) picks
one per decision.

Six built-in styles ship with Brain Engine; the owner-policy DSL
extends the registry with custom styles at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from core.brain.autonomy.models import AutonomyState, state_rank


class ReversibilityTier(StrEnum):
    """Undo affordance tier (inlined verbatim from the reference's
    ``cards/models.py`` — ``cards/`` itself stays unported; this is
    vertical-neutral mechanism, not vocabulary).

    - ``GREEN``: fully reversible within 60 s.
    - ``AMBER``: reversible via compensating action within 10 min.
    - ``RED``: effectively irreversible; audit-log only.
    """

    GREEN = "green"
    AMBER = "amber"
    RED = "red"


__all__ = [
    "BUILTIN_STYLE_SPECS",
    "PlannerStyleId",
    "PlannerStyleSpec",
]


class PlannerStyleId(StrEnum):
    """Twelve built-in Planner styles.

    The names mirror NVIDIA GR00T-WholeBodyControl's kinematic
    planner styles (run / stealth / kneeling / boxing) one level
    up: they describe *which behavior envelope* applies to the
    decision, not which skill handler runs.

    The first six values are the *base* styles that ship with
    Moat #4.  The remaining six (Moat #11 — Style Library
    expansion) are *situational* styles owners switch into via
    the DSL (Moat #2) when a context-specific posture is needed:
    seasonal demand, post-incident recovery, regulatory audit
    window, family-orientation, pet-allowance.
    """

    COOPERATIVE = "cooperative"
    DEFENSIVE = "defensive"
    COMPLIANCE_STRICT = "compliance_strict"
    VIP_WHITE_GLOVE = "vip_white_glove"
    BUDGET_NO_COMPROMISE = "budget_no_compromise"
    AGGRESSIVE_REVENUE = "aggressive_revenue"
    # ── Moat #11 — Style Library expansion ──────────────────── #
    SEASONAL_HIGH = "seasonal_high"
    SEASONAL_LOW = "seasonal_low"
    POST_INCIDENT_RECOVERY = "post_incident_recovery"
    REGULATORY_AUDIT_WINDOW = "regulatory_audit_window"
    FAMILY_FRIENDLY_STRICT = "family_friendly_strict"
    PET_FRIENDLY = "pet_friendly"


_REVERSIBILITY_ORDER: Final[tuple[ReversibilityTier, ...]] = (
    ReversibilityTier.GREEN,
    ReversibilityTier.AMBER,
    ReversibilityTier.RED,
)


@dataclass(frozen=True, slots=True)
class PlannerStyleSpec:
    """Constraint envelope a Planner style imposes on Foundation.

    Attributes:
        style_id: Identifier of the style.
        description: One-line plain-English summary used by audit
            logs and the V2 UI.
        denylist: Action kinds the style structurally forbids.
        autonomy_ceiling: Highest autonomy state the style permits;
            ``None`` means no cap (engine state wins).
        reversibility_ceiling: Highest reversibility tier permitted;
            ``None`` means no cap.  Capping picks the *more cautious*
            of (engine_tier, ceiling) — GREEN is the most cautious.
        preference_weights: Free-form weight map handlers may
            consult when ranking candidate actions; not enforced
            here.
    """

    style_id: PlannerStyleId
    description: str
    denylist: frozenset[str] = frozenset()
    autonomy_ceiling: AutonomyState | None = None
    reversibility_ceiling: ReversibilityTier | None = None
    preference_weights: Mapping[str, float] = field(
        default_factory=dict,
    )

    def forbids(self, kind: str) -> bool:
        """Return ``True`` if ``kind`` is denied under this style."""
        return kind in self.denylist

    def cap_autonomy(self, state: AutonomyState) -> AutonomyState:
        """Cap the engine-supplied autonomy state by the ceiling."""
        if self.autonomy_ceiling is None:
            return state
        if state_rank(state) <= state_rank(self.autonomy_ceiling):
            return state
        return self.autonomy_ceiling

    def cap_reversibility(
        self,
        tier: ReversibilityTier,
    ) -> ReversibilityTier:
        """Cap the action reversibility tier by the ceiling.

        ``GREEN < AMBER < RED`` in caution-strictness; capping
        returns the *more cautious* of (engine_tier, ceiling).
        """
        if self.reversibility_ceiling is None:
            return tier
        engine_idx = _REVERSIBILITY_ORDER.index(tier)
        ceiling_idx = _REVERSIBILITY_ORDER.index(self.reversibility_ceiling)
        return _REVERSIBILITY_ORDER[min(engine_idx, ceiling_idx)]


# ── Built-in style specs ──────────────────────────────────────── #

_COOPERATIVE = PlannerStyleSpec(
    style_id=PlannerStyleId.COOPERATIVE,
    description=("Default style.  No structural denials; engine autonomy and reversibility win."),
)


_DEFENSIVE = PlannerStyleSpec(
    style_id=PlannerStyleId.DEFENSIVE,
    description=(
        "Caution-first style for elevated-risk contexts (early "
        "incident, partial evidence).  Denies discretionary "
        "discounts and counter-offers; caps autonomy at SEMI_AUTO; "
        "AMBER reversibility ceiling."
    ),
    denylist=frozenset(
        {
            "apply_discount",
            "counter_offer",
        }
    ),
    autonomy_ceiling=AutonomyState.SEMI_AUTO,
    reversibility_ceiling=ReversibilityTier.AMBER,
)


_COMPLIANCE_STRICT = PlannerStyleSpec(
    style_id=PlannerStyleId.COMPLIANCE_STRICT,
    description=(
        "Regulatory-heavy jurisdictions (BCN / PAR / AMS centre / "
        "BER / NYC LL18).  Denies financial moves and code release "
        "without PM confirmation; pinned to OBSERVE."
    ),
    denylist=frozenset(
        {
            "charge_fee",
            "issue_refund",
            "release_code",
            "confirm_booking",
        }
    ),
    autonomy_ceiling=AutonomyState.OBSERVE,
    reversibility_ceiling=ReversibilityTier.AMBER,
)


_VIP_WHITE_GLOVE = PlannerStyleSpec(
    style_id=PlannerStyleId.VIP_WHITE_GLOVE,
    description=(
        "High-touch hospitality.  No structural denials; preference weights favour review score over occupancy and ADR."
    ),
    preference_weights={
        "review_score": 0.6,
        "occupancy": 0.2,
        "adr": 0.2,
    },
)


_BUDGET_NO_COMPROMISE = PlannerStyleSpec(
    style_id=PlannerStyleId.BUDGET_NO_COMPROMISE,
    description=(
        "Floor-price discipline.  Denies discounts and counter-"
        "offers; caps autonomy at SEMI_AUTO so the PM can spot "
        "any drift."
    ),
    denylist=frozenset(
        {
            "apply_discount",
            "counter_offer",
        }
    ),
    autonomy_ceiling=AutonomyState.SEMI_AUTO,
    preference_weights={
        "adr": 0.7,
        "occupancy": 0.2,
        "review_score": 0.1,
    },
)


_AGGRESSIVE_REVENUE = PlannerStyleSpec(
    style_id=PlannerStyleId.AGGRESSIVE_REVENUE,
    description=(
        "Revenue-first style.  No denials; full autonomy permitted; preference weights favour ADR and occupancy."
    ),
    preference_weights={
        "adr": 0.6,
        "occupancy": 0.3,
        "review_score": 0.1,
    },
)


# ── Moat #11 — situational styles ─────────────────────────────── #

_SEASONAL_HIGH = PlannerStyleSpec(
    style_id=PlannerStyleId.SEASONAL_HIGH,
    description=(
        "Peak-demand window.  No structural denials; preference "
        "weights match aggressive_revenue with a slight ADR push."
    ),
    preference_weights={
        "adr": 0.7,
        "occupancy": 0.2,
        "review_score": 0.1,
    },
)


_SEASONAL_LOW = PlannerStyleSpec(
    style_id=PlannerStyleId.SEASONAL_LOW,
    description=(
        "Off-season window.  Permits discretionary discounts to "
        "preserve occupancy; preference weights tilt toward "
        "occupancy and review score over ADR."
    ),
    preference_weights={
        "occupancy": 0.55,
        "review_score": 0.3,
        "adr": 0.15,
    },
)


_POST_INCIDENT_RECOVERY = PlannerStyleSpec(
    style_id=PlannerStyleId.POST_INCIDENT_RECOVERY,
    description=(
        "Engaged after a damage / noise / refund incident.  "
        "Denies discretionary discounts and counter-offers; caps "
        "autonomy at SEMI_AUTO; AMBER reversibility ceiling so "
        "every action is undoable for a window."
    ),
    denylist=frozenset(
        {
            "apply_discount",
            "counter_offer",
            "release_code",
        }
    ),
    autonomy_ceiling=AutonomyState.SEMI_AUTO,
    reversibility_ceiling=ReversibilityTier.AMBER,
    preference_weights={
        "review_score": 0.7,
        "occupancy": 0.2,
        "adr": 0.1,
    },
)


_REGULATORY_AUDIT_WINDOW = PlannerStyleSpec(
    style_id=PlannerStyleId.REGULATORY_AUDIT_WINDOW,
    description=(
        "Temporary lockdown while a regulator audit is open.  "
        "Denies the same set as compliance_strict plus all "
        "discretionary actions; pinned to OBSERVE so every move "
        "carries a PM signature."
    ),
    denylist=frozenset(
        {
            "charge_fee",
            "issue_refund",
            "release_code",
            "confirm_booking",
            "cancel_booking",
            "apply_discount",
            "counter_offer",
            "dispatch_vendor",
        }
    ),
    autonomy_ceiling=AutonomyState.OBSERVE,
    reversibility_ceiling=ReversibilityTier.AMBER,
)


_FAMILY_FRIENDLY_STRICT = PlannerStyleSpec(
    style_id=PlannerStyleId.FAMILY_FRIENDLY_STRICT,
    description=(
        "Family-oriented property.  Denies discretionary discounts "
        "(prevents party-pricing) and constrains autonomy to "
        "SEMI_AUTO; preference weights favour review score."
    ),
    denylist=frozenset(
        {
            "apply_discount",
            "counter_offer",
        }
    ),
    autonomy_ceiling=AutonomyState.SEMI_AUTO,
    preference_weights={
        "review_score": 0.6,
        "occupancy": 0.25,
        "adr": 0.15,
    },
)


_PET_FRIENDLY = PlannerStyleSpec(
    style_id=PlannerStyleId.PET_FRIENDLY,
    description=(
        "Explicit pet allowance.  No structural denials; "
        "preference weights mirror cooperative but slightly "
        "reduce ADR weight to absorb the longer turnover cleaning "
        "cost."
    ),
    preference_weights={
        "occupancy": 0.4,
        "review_score": 0.4,
        "adr": 0.2,
    },
)


BUILTIN_STYLE_SPECS: Final[Mapping[PlannerStyleId, PlannerStyleSpec]] = {
    PlannerStyleId.COOPERATIVE: _COOPERATIVE,
    PlannerStyleId.DEFENSIVE: _DEFENSIVE,
    PlannerStyleId.COMPLIANCE_STRICT: _COMPLIANCE_STRICT,
    PlannerStyleId.VIP_WHITE_GLOVE: _VIP_WHITE_GLOVE,
    PlannerStyleId.BUDGET_NO_COMPROMISE: _BUDGET_NO_COMPROMISE,
    PlannerStyleId.AGGRESSIVE_REVENUE: _AGGRESSIVE_REVENUE,
    PlannerStyleId.SEASONAL_HIGH: _SEASONAL_HIGH,
    PlannerStyleId.SEASONAL_LOW: _SEASONAL_LOW,
    PlannerStyleId.POST_INCIDENT_RECOVERY: _POST_INCIDENT_RECOVERY,
    PlannerStyleId.REGULATORY_AUDIT_WINDOW: (_REGULATORY_AUDIT_WINDOW),
    PlannerStyleId.FAMILY_FRIENDLY_STRICT: _FAMILY_FRIENDLY_STRICT,
    PlannerStyleId.PET_FRIENDLY: _PET_FRIENDLY,
}
