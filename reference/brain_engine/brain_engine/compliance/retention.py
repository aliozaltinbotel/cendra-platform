"""Retention policies by data class.

Every byte the engine persists belongs to one of the seven
``DataClass`` categories below.  Each category has a TTL drawn from
the strictest applicable regulator (GDPR for EU, KVKK for Türkiye,
internal policy for the rest).  The ``RetentionManager`` is a pure
in-memory lookup; the actual deletion is performed by per-store
sweepers (Redis TTL, Postgres ``deleted_at`` partitioning, Qdrant
TTL filter).

Reference: ``brain_engine_advisory.md`` §4 (2) — GDPR right-to-
erasure pipeline; this module owns the *policy*, not the *plumbing*.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum


class DataClass(str, Enum):
    """Category drives both retention and audit severity."""

    EPHEMERAL = "ephemeral"        # working memory, session caches
    OPERATIONAL = "operational"    # metrics, traces (no PII)
    GUEST_MESSAGE = "guest_message"
    GUEST_PROFILE = "guest_profile"
    DECISION_CASE = "decision_case"
    LEARNED_RULE = "learned_rule"
    AUDIT = "audit"                # immutable, regulator-facing


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """TTL contract for a data class."""

    data_class: DataClass
    ttl: timedelta
    legal_basis: str
    erasure_supported: bool

    def __post_init__(self) -> None:
        if self.ttl.total_seconds() <= 0:
            raise ValueError("RetentionPolicy ttl must be positive")


# ── Policy table ───────────────────────────────────────────────────
# Values are conservative (shorter retention than the legal ceiling)
# so a regulator's "right to be forgotten" request always succeeds
# inside the contract.

_POLICIES: dict[DataClass, RetentionPolicy] = {
    DataClass.EPHEMERAL: RetentionPolicy(
        data_class=DataClass.EPHEMERAL,
        ttl=timedelta(hours=24),
        legal_basis="legitimate-interest:operational",
        erasure_supported=True,
    ),
    DataClass.OPERATIONAL: RetentionPolicy(
        data_class=DataClass.OPERATIONAL,
        ttl=timedelta(days=90),
        legal_basis="legitimate-interest:operational",
        erasure_supported=True,
    ),
    DataClass.GUEST_MESSAGE: RetentionPolicy(
        data_class=DataClass.GUEST_MESSAGE,
        ttl=timedelta(days=180),
        legal_basis="contract-performance",
        erasure_supported=True,
    ),
    DataClass.GUEST_PROFILE: RetentionPolicy(
        data_class=DataClass.GUEST_PROFILE,
        ttl=timedelta(days=365),
        legal_basis="contract-performance",
        erasure_supported=True,
    ),
    DataClass.DECISION_CASE: RetentionPolicy(
        data_class=DataClass.DECISION_CASE,
        ttl=timedelta(days=730),
        legal_basis="legitimate-interest:operational",
        erasure_supported=True,
    ),
    DataClass.LEARNED_RULE: RetentionPolicy(
        data_class=DataClass.LEARNED_RULE,
        ttl=timedelta(days=730),
        legal_basis="legitimate-interest:operational",
        erasure_supported=False,  # rules carry no direct PII
    ),
    DataClass.AUDIT: RetentionPolicy(
        data_class=DataClass.AUDIT,
        ttl=timedelta(days=2555),  # 7 years for regulator
        legal_basis="legal-obligation",
        erasure_supported=False,
    ),
}


class RetentionManager:
    """Read-only registry of retention policies.

    Mutating policies at runtime is intentionally not supported; a
    policy change is a deploy-time decision (it crosses the legal
    boundary and must be reviewed).
    """

    def policy_for(self, data_class: DataClass) -> RetentionPolicy:
        """Return the policy for ``data_class``."""
        return _POLICIES[data_class]

    def ttl_seconds(self, data_class: DataClass) -> int:
        """Convenience for store backends that take seconds."""
        return int(self.policy_for(data_class).ttl.total_seconds())

    def supports_erasure(self, data_class: DataClass) -> bool:
        """Whether right-to-erasure applies to this class."""
        return self.policy_for(data_class).erasure_supported

    def all_policies(self) -> dict[DataClass, RetentionPolicy]:
        """Snapshot of the policy table — useful for the runbook."""
        return dict(_POLICIES)
