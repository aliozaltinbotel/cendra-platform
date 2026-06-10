"""Per-booking LLM/infra cost forecaster.

Reference: ``brain_engine_advisory.md`` §10.3.

The predictor is intentionally a *transparent linear model* rather
than a learned regressor:

* Coefficients are reviewed by humans and pinned in this file;
  there is no "model file" downstream that could drift.
* Input → output is deterministic, so the same booking quote
  always produces the same estimate (a contract the pricing path
  needs).
* The numbers are seeded from advisory §5 SLOs (≤ $0.15 / booking
  baseline) and §10.3 example heuristics; finance can tune them
  through a single PR without touching the engine.

When the assumption stack changes — new model deployment, new
provider, language tier renegotiation — bump :data:`MODEL_VERSION`
and update the coefficients in lock-step.  The version travels with
every :class:`CostEstimate` so audit can join an estimate back to
the coefficient set in force at the time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol


MODEL_VERSION: str = "2026.04-linear-v1"


class PropertyType(str, Enum):
    """Operational shape of the property — drives base load."""

    APARTMENT = "apartment"
    ROOM = "room"
    SHARED_BED = "shared_bed"
    STUDIO = "studio"
    HOTEL = "hotel"


@dataclass(frozen=True, slots=True)
class BookingFeatures:
    """Inputs the predictor consumes; all values must be known up-front."""

    property_type: PropertyType
    expected_messages: int
    guest_history_count: int
    has_complaint_history: bool
    high_complexity_share: float = 0.2
    language_count: int = 1

    def __post_init__(self) -> None:
        if self.expected_messages < 0:
            raise ValueError("expected_messages must be ≥ 0")
        if self.guest_history_count < 0:
            raise ValueError("guest_history_count must be ≥ 0")
        if not 0.0 <= self.high_complexity_share <= 1.0:
            raise ValueError(
                "high_complexity_share must be in [0, 1]",
            )
        if self.language_count < 1:
            raise ValueError("language_count must be ≥ 1")


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """Forecast a caller can act on or audit later."""

    mean_usd: float
    p10_usd: float
    p90_usd: float
    breakdown: Mapping[str, float] = field(default_factory=dict)
    model_version: str = MODEL_VERSION

    def __post_init__(self) -> None:
        if self.mean_usd < 0 or self.p10_usd < 0 or self.p90_usd < 0:
            raise ValueError("CostEstimate values must be ≥ 0")
        if self.p10_usd > self.mean_usd or self.mean_usd > self.p90_usd:
            raise ValueError(
                "expected p10 ≤ mean ≤ p90",
            )


class CostModel(Protocol):
    """Strategy contract — swap in a learned model later if needed."""

    def predict(self, features: BookingFeatures) -> CostEstimate: ...


# ── Coefficients ───────────────────────────────────────────────────
# Linear feature contributions in USD.  Sourced from the advisory's
# $0.15/booking baseline and the costed L1/L3 cascade mix.

_BASE_USD: dict[PropertyType, float] = {
    PropertyType.APARTMENT: 0.05,
    PropertyType.ROOM: 0.04,
    PropertyType.SHARED_BED: 0.03,
    PropertyType.STUDIO: 0.05,
    PropertyType.HOTEL: 0.07,
}

_PER_MESSAGE_USD: float = 0.004              # L1+L2 cascade share
_HIGH_COMPLEXITY_PER_MSG_USD: float = 0.018  # L3 deliberative share
_PER_HISTORY_LOOKUP_USD: float = 0.0008
_COMPLAINT_PREMIUM_USD: float = 0.012        # extra L3 hops
_PER_LANGUAGE_USD: float = 0.006             # i18n + translator hops

# Forecast spread — captured here so finance can move the band
# independently of the central model.
_P10_MULTIPLIER: float = 0.7
_P90_MULTIPLIER: float = 1.6


class LinearCostModel:
    """Reference cost model — pinned coefficients above."""

    def predict(self, features: BookingFeatures) -> CostEstimate:
        breakdown = self._breakdown(features)
        mean_usd = round(sum(breakdown.values()), 4)
        p10_usd = round(mean_usd * _P10_MULTIPLIER, 4)
        p90_usd = round(mean_usd * _P90_MULTIPLIER, 4)
        return CostEstimate(
            mean_usd=mean_usd,
            p10_usd=p10_usd,
            p90_usd=p90_usd,
            breakdown=breakdown,
        )

    @staticmethod
    def _breakdown(features: BookingFeatures) -> dict[str, float]:
        base = _BASE_USD[features.property_type]
        share_high = features.high_complexity_share
        share_low = 1.0 - share_high
        msgs = features.expected_messages
        history = features.guest_history_count
        complaint = (
            _COMPLAINT_PREMIUM_USD if features.has_complaint_history else 0.0
        )
        language = (
            features.language_count - 1
        ) * _PER_LANGUAGE_USD
        return {
            "base": base,
            "messages_l1l2": _PER_MESSAGE_USD * msgs * share_low,
            "messages_l3": (
                _HIGH_COMPLEXITY_PER_MSG_USD * msgs * share_high
            ),
            "history_lookups": _PER_HISTORY_LOOKUP_USD * history,
            "complaint_premium": complaint,
            "multilingual": language,
        }
