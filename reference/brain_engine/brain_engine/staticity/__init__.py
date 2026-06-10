"""Staticity classification — determines whether data fields are safe to cache.

Four-level taxonomy:

- ``STATIC_SAFE`` — immutable facts (property address, bedroom count).
- ``STATIC_VERIFY_PERIODICALLY`` — slow-moving (wifi password, house rules).
- ``DYNAMIC_FETCH_LIVE`` — frequently changing (calendar, payment status).
- ``SECRET_DYNAMIC_FETCH_ONLY`` — sensitive + volatile (door codes).

The classifier enforces Cendra's "never serve a stale access code" rule:
any SECRET_DYNAMIC field must be fetched live every time, and any field
that has changed more than :pyattr:`StaticityClassifier._PROMOTION_THRESHOLD`
times for a property is automatically promoted to the next volatility
tier.
"""

from __future__ import annotations

from brain_engine.staticity.classifier import (
    FieldStaticity,
    StaticityClassifier,
    StaticityLevel,
)
from brain_engine.staticity.guard import (
    AgeLookup,
    StaticityGuard,
    StaticityVerdict,
    VerdictKind,
    guard_payload,
)

__all__ = [
    "AgeLookup",
    "FieldStaticity",
    "StaticityClassifier",
    "StaticityGuard",
    "StaticityLevel",
    "StaticityVerdict",
    "VerdictKind",
    "guard_payload",
]
