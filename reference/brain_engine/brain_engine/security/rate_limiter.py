"""Token-bucket rate limiter, per (tenant, action).

Two distinct concerns:

* **Pricing tier ceilings** — a Free trial tenant cannot burn 10k
  LLM calls a day; the tier decides the daily ceiling for benign
  actions (``message``, ``llm_call``).
* **Hard global caps for dangerous actions** — independent of tier.
  No tenant ever does more than 10 ``charge_guest`` per hour per
  property; ``skill_evolution`` is capped globally at 100/day to
  defend against LLM-injection attacks that try to rewrite procedural
  rules.

The implementation is in-memory and intentionally synchronous.  A
distributed deployment swaps in a Redis-backed bucket via the same
interface; the test backend is the source of truth for behaviour.

Reference: ``brain_engine_advisory.md`` §9.2.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock


class TenantTier(str, Enum):
    """Pricing tier; drives per-day ceilings for benign actions."""

    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Result of a single ``check_and_consume`` call."""

    allowed: bool
    remaining: int
    retry_after_s: float
    reason: str = ""


# ── Per-tier ceilings (advisory §9.2 table) ────────────────────────
# (action_kind, tier) → tokens per 24h
_TIER_DAILY_CEILINGS: dict[tuple[str, TenantTier], int] = {
    ("message", TenantTier.FREE): 1_000,
    ("message", TenantTier.STARTER): 10_000,
    ("message", TenantTier.PROFESSIONAL): 1_000_000,
    ("message", TenantTier.ENTERPRISE): 1_000_000,
    ("llm_call", TenantTier.FREE): 100,
    ("llm_call", TenantTier.STARTER): 1_000,
    ("llm_call", TenantTier.PROFESSIONAL): 10_000,
    ("llm_call", TenantTier.ENTERPRISE): 100_000,
}

# ── Hard caps for dangerous actions (advisory §9.2) ────────────────
# These ride on top of pricing-tier ceilings.  They never get
# loosened at runtime; tightening requires a deploy.
_HARD_CAPS: dict[str, tuple[int, float]] = {
    # action_kind: (tokens, window_seconds)
    "charge_guest": (10, 3600.0),            # per property per hour
    "release_access_code": (5, 3600.0),
    "skill_evolution": (100, 86400.0),       # global per day
    "send_external_email": (20, 3600.0),
}


@dataclass
class _Bucket:
    """Token bucket with continuous refill."""

    capacity: int
    refill_rate_per_s: float
    tokens: float
    last_refill: float

    def consume(self, cost: int = 1) -> tuple[bool, int, float]:
        """Try to consume ``cost`` tokens; return (ok, remaining, wait)."""
        now = time.monotonic()
        delta = now - self.last_refill
        self.tokens = min(
            self.capacity,
            self.tokens + delta * self.refill_rate_per_s,
        )
        self.last_refill = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True, int(self.tokens), 0.0
        deficit = cost - self.tokens
        wait = deficit / max(self.refill_rate_per_s, 1e-9)
        return False, int(self.tokens), wait


class TenantRateLimiter:
    """Thread-safe per (tenant, action_kind) limiter.

    The bucket key for tier ceilings is ``(tenant_id, action_kind)``;
    for hard caps it is ``(tenant_id, "hard:" + action_kind)`` so the
    two never share state.
    """

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = Lock()

    def check_and_consume(
        self,
        *,
        tenant_id: str,
        action_kind: str,
        tier: TenantTier,
        cost: int = 1,
    ) -> RateLimitDecision:
        """Consume one slot or report why we can't.

        The check is two-phase: first the hard cap (if any), then the
        tier ceiling.  Both must succeed; if the hard cap rejects we
        do not touch the tier bucket (the dangerous-action attempt
        does not count against the benign budget).
        """
        if not tenant_id:
            raise ValueError("tenant_id required")
        if action_kind in _HARD_CAPS:
            verdict = self._consume_hard(tenant_id, action_kind, cost)
            if not verdict.allowed:
                return verdict
        return self._consume_tier(tenant_id, action_kind, tier, cost)

    # ── Internals ───────────────────────────────────────────────────

    def _consume_hard(
        self,
        tenant_id: str,
        action_kind: str,
        cost: int,
    ) -> RateLimitDecision:
        capacity, window = _HARD_CAPS[action_kind]
        key = (tenant_id, f"hard:{action_kind}")
        bucket = self._get_bucket(key, capacity, capacity / window)
        with self._lock:
            ok, remaining, wait = bucket.consume(cost)
        if not ok:
            return RateLimitDecision(
                allowed=False,
                remaining=remaining,
                retry_after_s=wait,
                reason=f"hard cap on {action_kind}",
            )
        return RateLimitDecision(
            allowed=True, remaining=remaining, retry_after_s=0.0,
        )

    def _consume_tier(
        self,
        tenant_id: str,
        action_kind: str,
        tier: TenantTier,
        cost: int,
    ) -> RateLimitDecision:
        ceiling = _TIER_DAILY_CEILINGS.get(
            (action_kind, tier), -1,
        )
        if ceiling < 0:
            # No tier ceiling for this action ⇒ allowed if hard-cap passed.
            return RateLimitDecision(
                allowed=True, remaining=-1, retry_after_s=0.0,
            )
        bucket = self._get_bucket(
            (tenant_id, action_kind),
            ceiling,
            ceiling / 86400.0,
        )
        with self._lock:
            ok, remaining, wait = bucket.consume(cost)
        if not ok:
            return RateLimitDecision(
                allowed=False,
                remaining=remaining,
                retry_after_s=wait,
                reason=f"tier {tier.value} daily ceiling reached",
            )
        return RateLimitDecision(
            allowed=True, remaining=remaining, retry_after_s=0.0,
        )

    def _get_bucket(
        self,
        key: tuple[str, str],
        capacity: int,
        refill_rate: float,
    ) -> _Bucket:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(
                    capacity=capacity,
                    refill_rate_per_s=refill_rate,
                    tokens=float(capacity),
                    last_refill=time.monotonic(),
                )
                self._buckets[key] = bucket
            return bucket
