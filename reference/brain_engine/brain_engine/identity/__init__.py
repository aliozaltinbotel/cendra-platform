"""Cross-channel guest identity graph (Moat #8).

GDPR-safe deterministic identity reconciliation across STR
channels.  Joins are keyed off HMAC-SHA256 hashes of normalised
email / phone — *never* raw PII — so the graph itself can be
audited and exported without leaking personal data.

Public surface:

- :class:`ChannelKind` — stable channel taxonomy.
- :class:`MatchEvidenceKind` — typed evidence categories.
- :class:`ChannelHandle` — one observation from one channel.
- :class:`GuestIdentity` — canonical record across handles.
- :class:`MatchProposal` — resolved identity + confidence +
  audit rationale.
- :func:`normalize_email` / :func:`normalize_phone` /
  :func:`hmac_handle` — pure-Python helpers used by callers
  before they hand a :class:`ChannelHandle` to the matcher.
- :class:`IdentityGraph` — in-memory store with deterministic
  merge logic (single hash → existing identity; conflicting
  hashes → merge two identities into one with audit trail).
- :class:`DeterministicMatcher` — façade for the runtime path.

Defensibility (Moat #8): cross-channel guest identity graph with
HMAC-bound deterministic joins + Wilson-bounded probabilistic
merge for regulated-domain agents.  Domain axis E — none of the
16 surveyed proptech competitors ships this (latest_research §2
row E).
"""

from __future__ import annotations

from brain_engine.identity.graph import (
    DEFAULT_IDENTITY_PREFIX,
    IdentityGraph,
)
from brain_engine.identity.hashing import (
    MIN_HMAC_KEY_BYTES,
    hmac_handle,
    normalize_email,
    normalize_phone,
)
from brain_engine.identity.matcher import DeterministicMatcher
from brain_engine.identity.models import (
    ChannelHandle,
    ChannelKind,
    GuestIdentity,
    MatchEvidenceKind,
    MatchProposal,
)
from brain_engine.identity.probabilistic import (
    DEFAULT_JACCARD_THRESHOLD,
    DEFAULT_MIN_FEATURES,
    ProbabilisticMatcher,
    jaccard_similarity,
)


__all__ = [
    "DEFAULT_IDENTITY_PREFIX",
    "DEFAULT_JACCARD_THRESHOLD",
    "DEFAULT_MIN_FEATURES",
    "MIN_HMAC_KEY_BYTES",
    "ChannelHandle",
    "ChannelKind",
    "DeterministicMatcher",
    "GuestIdentity",
    "IdentityGraph",
    "MatchEvidenceKind",
    "MatchProposal",
    "ProbabilisticMatcher",
    "hmac_handle",
    "jaccard_similarity",
    "normalize_email",
    "normalize_phone",
]
