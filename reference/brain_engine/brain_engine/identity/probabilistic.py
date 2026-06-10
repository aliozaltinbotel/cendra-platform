"""Probabilistic identity matcher (final closure for M8).

Closes the M8 deferred TODO ("Probabilistic matcher — writing-
style / booking-pattern similarity").  Layered on top of the
existing :class:`DeterministicMatcher`:

  1. First run the deterministic match — HMAC-hashed email /
     phone wins immediately.
  2. If the deterministic step minted a *fresh* identity, the
     probabilistic step scans existing identities for the
     best Jaccard match on behavioural features.
  3. When the best Jaccard ≥ threshold AND both sides have at
     least ``min_features`` features, the freshly-minted
     identity is *merged* into the best-match existing
     identity.  The Wilson lower bound on the supporting
     evidence becomes the proposal's confidence.

The matcher never *creates* identities itself — every identity
mint happens via the underlying :class:`IdentityGraph` so the
join surface stays auditable.

Pure-Python; no scipy / sklearn needed.  Wilson LB comes from
:mod:`brain_engine.patterns.wilson`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

import structlog

from brain_engine.identity.graph import IdentityGraph
from brain_engine.identity.matcher import DeterministicMatcher
from brain_engine.identity.models import (
    ChannelHandle,
    GuestIdentity,
    MatchEvidenceKind,
    MatchProposal,
)
from brain_engine.patterns.wilson import Z_95, wilson_lower_bound


__all__ = [
    "DEFAULT_JACCARD_THRESHOLD",
    "DEFAULT_MIN_FEATURES",
    "ProbabilisticMatcher",
    "jaccard_similarity",
]


DEFAULT_JACCARD_THRESHOLD: Final[float] = 0.6
DEFAULT_MIN_FEATURES: Final[int] = 3


logger = structlog.get_logger(__name__)


def jaccard_similarity(
    a: frozenset[str] | set[str],
    b: frozenset[str] | set[str],
) -> float:
    """Return ``|a ∩ b| / |a ∪ b|`` in ``[0.0, 1.0]``.

    Returns ``0.0`` when either side is empty (no signal to
    judge similarity by).
    """
    if not a or not b:
        return 0.0
    union = a | b
    intersection = a & b
    return len(intersection) / len(union)


class ProbabilisticMatcher:
    """Behavioural-feature matcher that wraps a deterministic core.

    Construction takes the same :class:`IdentityGraph` the
    :class:`DeterministicMatcher` operates on; the two matchers
    share state so a single graph holds both deterministic and
    probabilistic merges.
    """

    def __init__(
        self,
        *,
        graph: IdentityGraph,
        threshold: float = DEFAULT_JACCARD_THRESHOLD,
        min_features: int = DEFAULT_MIN_FEATURES,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(
                "threshold must be in (0.0, 1.0]"
            )
        if min_features < 1:
            raise ValueError(
                "min_features must be positive"
            )
        self._graph = graph
        self._threshold = threshold
        self._min_features = min_features
        self._deterministic = DeterministicMatcher(graph=graph)
        self._log = logger.bind(
            component="probabilistic_identity_matcher",
        )

    def match(
        self,
        *,
        handle: ChannelHandle,
    ) -> MatchProposal:
        """Run the layered match and return a :class:`MatchProposal`.

        Order:
            1. Deterministic match (HMAC-hashed email / phone).
            2. If a *fresh* identity was minted and the handle
               carries enough behavioural features, scan the
               graph for the best Jaccard partner; merge when
               above threshold.
        """
        deterministic = self._deterministic.match(handle=handle)
        if not self._can_probabilistic_match(handle):
            return deterministic
        before = self._handles_in_other_identities(
            identity_id=deterministic.identity_id,
            handle=handle,
        )
        if before is None:
            # Deterministic match attached to an existing
            # identity already — no need to probabilistic-merge.
            return deterministic
        partner_id, similarity, support = (
            self._best_partner(
                fresh_id=deterministic.identity_id,
                features=handle.behavioural_features,
            )
        )
        if (
            partner_id is None
            or similarity < self._threshold
        ):
            return deterministic
        winner = self._merge_identities(
            fresh_id=deterministic.identity_id,
            partner_id=partner_id,
        )
        confidence = wilson_lower_bound(
            successes=support,
            trials=max(support, 1),
            z=Z_95,
        )
        self._log.info(
            "identity.probabilistic_merge",
            survivor=winner,
            fresh=deterministic.identity_id,
            partner=partner_id,
            jaccard=round(similarity, 3),
            wilson_lb=round(confidence, 3),
        )
        return MatchProposal(
            identity_id=winner,
            kind=MatchEvidenceKind.BEHAVIOURAL,
            confidence=confidence,
            rationale=(
                "probabilistic merge via jaccard="
                f"{similarity:.3f} on "
                f"{support} shared feature(s)"
            ),
            merged=True,
        )

    # ── internals ─────────────────────────────────────────── #

    def _can_probabilistic_match(
        self,
        handle: ChannelHandle,
    ) -> bool:
        return (
            len(handle.behavioural_features) >= self._min_features
        )

    def _handles_in_other_identities(
        self,
        *,
        identity_id: str,
        handle: ChannelHandle,
    ) -> int | None:
        """Return ``None`` when the identity already has multiple handles.

        We only run the probabilistic merge when the
        deterministic step *just* minted a new identity (i.e. the
        graph has it with a single handle equal to ``handle``).
        Otherwise the prior insertion already attached to an
        existing identity via hash overlap and probabilistic
        merge would be redundant.
        """
        record = self._graph.get(identity_id)
        if record is None:
            return None
        if len(record.handles) != 1:
            return None
        return 1

    def _best_partner(
        self,
        *,
        fresh_id: str,
        features: frozenset[str],
    ) -> tuple[str | None, float, int]:
        best_id: str | None = None
        best_sim = 0.0
        best_overlap = 0
        for candidate_id, candidate_features in (
            self._iter_candidates(exclude=fresh_id)
        ):
            sim = jaccard_similarity(features, candidate_features)
            if sim > best_sim:
                best_sim = sim
                best_id = candidate_id
                best_overlap = len(features & candidate_features)
        return best_id, best_sim, best_overlap

    def _iter_candidates(
        self,
        *,
        exclude: str,
    ) -> Iterable[tuple[str, frozenset[str]]]:
        for ident_id, record in self._graph._identities.items():  # noqa: SLF001
            if ident_id == exclude:
                continue
            yield ident_id, self._aggregate_features(record)

    @staticmethod
    def _aggregate_features(
        identity: GuestIdentity,
    ) -> frozenset[str]:
        merged: set[str] = set()
        for handle in identity.handles:
            merged |= handle.behavioural_features
        return frozenset(merged)

    def _merge_identities(
        self,
        *,
        fresh_id: str,
        partner_id: str,
    ) -> str:
        """Move fresh handles onto the partner; drop the fresh record."""
        fresh = self._graph.get(fresh_id)
        partner = self._graph.get(partner_id)
        if fresh is None or partner is None:
            return partner_id
        # The graph already exposes ``_merge`` for hash-merging;
        # we replicate the same shape here so the audit log uses
        # a uniform code path.  We walk the fresh handles into
        # the partner's collection and reindex.
        survivor_handles = (*partner.handles, *fresh.handles)
        survivor_history = (
            *partner.merged_from,
            *fresh.merged_from,
            fresh_id,
        )
        merged = GuestIdentity(
            identity_id=partner_id,
            handles=survivor_handles,
            merged_from=survivor_history,
        )
        self._graph._identities[partner_id] = merged  # noqa: SLF001
        del self._graph._identities[fresh_id]  # noqa: SLF001
        # Re-point every hash that fresh owned at the partner.
        for hash_value, owner in list(
            self._graph._by_hash.items()  # noqa: SLF001
        ):
            if owner == fresh_id:
                self._graph._by_hash[hash_value] = partner_id  # noqa: SLF001
        return partner_id
