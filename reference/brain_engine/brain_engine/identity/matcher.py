"""Deterministic + probabilistic match composer.

The matcher is a thin facade over :class:`IdentityGraph`: it
accepts a fresh :class:`ChannelHandle`, runs the deterministic
merge through the graph, and returns a :class:`MatchProposal`
carrying the identity_id, confidence, and rationale.

Probabilistic matching (writing-style overlap, booking-pattern
similarity) is staged for v1.0 — the v0.1 façade ships only the
HMAC-deterministic path so the runtime moats can be merged
without waiting on an ML feature pipeline.  The
:class:`MatchEvidenceKind.BEHAVIOURAL` slot already exists in
``models.py`` so v1.0 wiring is purely additive.
"""

from __future__ import annotations

import structlog

from brain_engine.identity.graph import IdentityGraph
from brain_engine.identity.models import (
    ChannelHandle,
    MatchEvidenceKind,
    MatchProposal,
)


__all__ = ["DeterministicMatcher"]


logger = structlog.get_logger(__name__)


class DeterministicMatcher:
    """HMAC-hash-driven identity matcher (v0.1)."""

    def __init__(self, *, graph: IdentityGraph) -> None:
        self._graph = graph
        self._log = logger.bind(component="identity_matcher")

    def match(
        self,
        *,
        handle: ChannelHandle,
    ) -> MatchProposal:
        """Insert ``handle`` and return the resolved proposal."""
        identity_id, merged = self._graph.insert(handle=handle)
        evidence_kind = self._evidence_kind(handle)
        rationale = self._rationale(
            handle=handle,
            evidence_kind=evidence_kind,
            merged=merged,
        )
        proposal = MatchProposal(
            identity_id=identity_id,
            kind=evidence_kind,
            confidence=1.0,
            rationale=rationale,
            merged=merged,
        )
        self._log.info(
            "identity.matched",
            identity_id=identity_id,
            channel=handle.channel.value,
            kind=evidence_kind.value,
            merged=merged,
        )
        return proposal

    @staticmethod
    def _evidence_kind(
        handle: ChannelHandle,
    ) -> MatchEvidenceKind:
        if handle.email_hash is not None:
            return MatchEvidenceKind.EMAIL_HASH
        if handle.phone_hash is not None:
            return MatchEvidenceKind.PHONE_HASH
        return MatchEvidenceKind.BEHAVIOURAL

    @staticmethod
    def _rationale(
        *,
        handle: ChannelHandle,
        evidence_kind: MatchEvidenceKind,
        merged: bool,
    ) -> str:
        if merged:
            return (
                f"merge via {evidence_kind.value} on channel "
                f"{handle.channel.value}"
            )
        return (
            f"{evidence_kind.value} match on channel "
            f"{handle.channel.value}"
        )
