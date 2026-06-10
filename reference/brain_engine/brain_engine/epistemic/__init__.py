"""Observation / Belief schema with bi-temporal provenance (Moat #7).

Brain Engine's seven memory tiers historically mixed *observations*
(immutable evidence) with *beliefs* (mutable inferred state).  This
module separates the two as first-class concepts so the audit
pipeline can answer the regulator's question "is this fact
something you saw or something you inferred?".

Public surface:

- :class:`ProvenanceKind` — typed source category enum.
- :class:`Provenance` — source descriptor for an observation.
- :class:`Observation` — immutable datum with BLAKE2B-256
  integrity hash, tz-aware ``recorded_at``, and provenance.
- :class:`Belief` — promoted inferred state with Wilson LB and
  supporting observation ids.
- :class:`ObservationStore` / :class:`BeliefStore` Protocols +
  in-memory defaults for tests.
- :class:`BeliefPromotionGate` — sample-size + Wilson LB gate
  that converts an observation window into a :class:`Belief`
  or raises :class:`PromotionRefusal`.
- :func:`canonical_observation_payload` /
  :func:`observation_integrity_hash` — pure helpers used by both
  the runtime and external verifiers.

Defensibility (Moat #7): multi-tier observation-belief
separation with bi-temporal provenance + Wilson-bounded belief
promotion.  Extends *Hindsight is 20/20* (Latimer et al.
arXiv:2512.12818) — the paper anchors the pattern at one memory
level; Brain Engine applies it across every tier with a
cryptographic provenance hash and a calibrated promotion gate.
"""

from __future__ import annotations

from brain_engine.epistemic.models import (
    Belief,
    Observation,
    Provenance,
    ProvenanceKind,
    canonical_observation_payload,
    observation_integrity_hash,
)
from brain_engine.epistemic.promotion import (
    DEFAULT_MIN_SAMPLES,
    DEFAULT_WILSON_THRESHOLD,
    BeliefPromotionGate,
    PromotionRefusal,
    SuccessPredicate,
    predicate_truthy,
)
from brain_engine.epistemic.store import (
    BeliefStore,
    InMemoryBeliefStore,
    InMemoryObservationStore,
    ObservationStore,
)


__all__ = [
    "Belief",
    "BeliefPromotionGate",
    "BeliefStore",
    "DEFAULT_MIN_SAMPLES",
    "DEFAULT_WILSON_THRESHOLD",
    "InMemoryBeliefStore",
    "InMemoryObservationStore",
    "Observation",
    "ObservationStore",
    "Provenance",
    "ProvenanceKind",
    "PromotionRefusal",
    "SuccessPredicate",
    "canonical_observation_payload",
    "observation_integrity_hash",
    "predicate_truthy",
]
