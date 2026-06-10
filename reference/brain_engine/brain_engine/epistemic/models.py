"""Observation / Belief value objects (Moat #7).

Brain Engine's seven memory tiers historically mixed *observations*
(immutable evidence — a sensor reading, an inbound message, a
booking event) with *beliefs* (mutable inferred state — "this
guest tends to be late", "noise complaints typically spike on
Saturdays").  This module separates the two as first-class
concepts so the audit pipeline can answer the regulator's
question "is this fact something you saw or something you
inferred?"

The split mirrors the *Hindsight is 20/20* pattern (Latimer et
al. arXiv:2512.12818) extended across every memory tier — Brain
Engine's specific contribution is the bi-temporal provenance hash
on every observation and the Wilson-bounded promotion gate that
converts observations into beliefs.

Three building blocks ship here:

- :class:`Provenance` — *where the observation came from*.  Free-
  form ``source_id`` plus a typed :class:`ProvenanceKind`.
- :class:`Observation` — one immutable datum with a tz-aware
  ``recorded_at`` and a BLAKE2B integrity hash.  Cannot be
  mutated; cannot be deleted; can be marked superseded only via
  a follow-up observation.
- :class:`Belief` — derived state, promoted from a *tuple* of
  supporting observation ids.  Carries the Wilson lower bound on
  the success rate of the supporting observations, the sample
  size, and the promoting actor.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


__all__ = [
    "Belief",
    "Observation",
    "Provenance",
    "ProvenanceKind",
    "canonical_observation_payload",
    "observation_integrity_hash",
]


class ProvenanceKind(StrEnum):
    """Typed source category for an observation."""

    SENSOR = "sensor"
    MESSAGE = "message"
    HUMAN = "human"
    SYSTEM = "system"
    EXTERNAL_API = "external_api"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Source descriptor attached to every observation.

    Attributes:
        kind: Typed source category.
        source_id: Free-form identifier (sensor serial, message
            id, user id, vendor name).  Non-empty.
        correlation_id: Optional cross-component trace id used
            to stitch observations into one logical workflow.
    """

    kind: ProvenanceKind
    source_id: str
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id required")


@dataclass(frozen=True, slots=True)
class Observation:
    """Immutable datum with bi-temporal provenance hash.

    Attributes:
        observation_id: Stable opaque identifier.
        subject: Free-form key the observation describes (e.g.
            ``"property:p1:noise_db"`` /
            ``"guest:g2:late_arrivals"``).  Non-empty.
        value: The observed value.  Numeric, string or bool —
            the canonical encoder json-serialises whatever the
            caller hands in.
        recorded_at: tz-aware UTC timestamp.
        provenance: Source descriptor.
        integrity_hex: 64-char BLAKE2B-256 hex digest over the
            canonical payload.  Tampering with any field after
            construction is detectable in constant time via
            :func:`observation_integrity_hash`.
    """

    observation_id: str
    subject: str
    value: Any
    recorded_at: datetime
    provenance: Provenance
    integrity_hex: str

    def __post_init__(self) -> None:
        if not self.observation_id:
            raise ValueError("observation_id required")
        if not self.subject:
            raise ValueError("subject required")
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be tz-aware")
        if len(self.integrity_hex) != 64:
            raise ValueError(
                "integrity_hex must be 64 hex chars (BLAKE2B-256)"
            )


@dataclass(frozen=True, slots=True)
class Belief:
    """Promoted inferred state derived from observations.

    Attributes:
        belief_id: Stable opaque identifier.
        subject: Same key as the supporting observations.
        promoted_value: The inferred value (often a summary of
            the supporting observations — a mean, a class, a
            label).
        wilson_lb: Wilson-score lower bound on the empirical
            success rate of the supporting observations
            (computed by the promotion gate, not here).
        sample_size: Number of supporting observations at the
            moment of promotion.
        supporting_observation_ids: Ordered tuple of
            :attr:`Observation.observation_id` values that
            justified the promotion.
        promoted_at: tz-aware UTC timestamp.
        promoted_by: Actor that ran the promotion gate
            (``"system"``, ``"agent:nightly"``, ``"pm:42"``).
        extra: Free-form serialisable metadata for downstream
            consumers; immutable through the frozen wrapper.
    """

    belief_id: str
    subject: str
    promoted_value: Any
    wilson_lb: float
    sample_size: int
    supporting_observation_ids: tuple[str, ...]
    promoted_at: datetime
    promoted_by: str
    extra: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.belief_id:
            raise ValueError("belief_id required")
        if not self.subject:
            raise ValueError("subject required")
        if self.promoted_at.tzinfo is None:
            raise ValueError("promoted_at must be tz-aware")
        if not 0.0 <= self.wilson_lb <= 1.0:
            raise ValueError(
                "wilson_lb must be in [0.0, 1.0]"
            )
        if self.sample_size < 0:
            raise ValueError("sample_size must be non-negative")
        if not self.promoted_by:
            raise ValueError("promoted_by required")


def canonical_observation_payload(
    *,
    observation_id: str,
    subject: str,
    value: Any,
    recorded_at: datetime,
    provenance: Provenance,
) -> bytes:
    """Return the deterministic UTF-8 bytes the integrity hash signs.

    The encoding is sorted-key JSON so the hash is reproducible
    across Python versions and dict insertion orders.
    """
    if recorded_at.tzinfo is None:
        raise ValueError("recorded_at must be tz-aware")
    body = {
        "observation_id": observation_id,
        "subject": subject,
        "value": value,
        "recorded_at": recorded_at.isoformat(),
        "provenance": {
            "kind": provenance.kind.value,
            "source_id": provenance.source_id,
            "correlation_id": provenance.correlation_id,
        },
    }
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def observation_integrity_hash(
    *,
    observation_id: str,
    subject: str,
    value: Any,
    recorded_at: datetime,
    provenance: Provenance,
) -> str:
    """Return the 64-char BLAKE2B-256 hex digest for an observation."""
    payload = canonical_observation_payload(
        observation_id=observation_id,
        subject=subject,
        value=value,
        recorded_at=recorded_at,
        provenance=provenance,
    )
    return hashlib.blake2b(payload, digest_size=32).hexdigest()
