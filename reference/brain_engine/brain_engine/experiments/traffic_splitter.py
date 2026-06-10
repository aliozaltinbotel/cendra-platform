"""Deterministic hash-based traffic splitter.

For an experiment with a fixed roster of variants and weights, a
subject (e.g. a conversation id, a property id, a guest id) must
land in *exactly one* variant — and must keep landing in the
same variant on every replay.  Random sampling fails both
properties; we use ``blake2b`` of ``salt || subject_id`` mapped
into ``[0, _RESOLUTION)`` and walked across the cumulative weight
ranges.

The splitter is sync and pure; it is safe to call from any
thread or async task without coordination.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DeterministicTrafficSplitter",
    "SplitDecision",
    "TrafficSplit",
]


_RESOLUTION: Final[int] = 1_000_000
"""Granularity of the assignment grid.  One in a million is
enough headroom for 0.0001 weight steps without floating-point
drift."""


@dataclass(frozen=True, slots=True)
class TrafficSplit:
    """Variant weights for one experiment.

    Attributes:
        weights: Mapping of variant id to weight share.  All
            weights must be non-negative; at least one must be
            positive.  They are normalised internally so callers
            can pass raw counts or fractions.
    """

    weights: Mapping[str, float]

    def __post_init__(self) -> None:
        if not self.weights:
            raise ValueError("weights must not be empty")
        for variant_id, weight in self.weights.items():
            if not variant_id:
                raise ValueError("variant id must not be empty")
            if weight < 0:
                raise ValueError(
                    f"weight for {variant_id!r} must be >= 0",
                )
        if sum(self.weights.values()) <= 0:
            raise ValueError(
                "at least one weight must be strictly positive",
            )


@dataclass(frozen=True, slots=True)
class SplitDecision:
    """Outcome of one assignment.

    Attributes:
        variant_id: Variant chosen for ``subject_id``.
        bucket: The hash bucket (``[0, _RESOLUTION)``) that drove
            the decision — exposed for unit-tests and audit logs,
            never for routing.
    """

    variant_id: str
    bucket: int


class DeterministicTrafficSplitter:
    """Hash-based splitter with reproducible assignment.

    Args:
        salt: Per-experiment salt — varying it across experiments
            decorrelates assignments so that a subject in the
            ``treatment`` arm of experiment A is no more likely
            to land in any specific arm of experiment B.
    """

    def __init__(self, *, salt: str) -> None:
        if not salt:
            raise ValueError("salt must not be empty")
        self._salt = salt.encode("utf-8")

    def assign(
        self,
        subject_id: str,
        split: TrafficSplit,
    ) -> SplitDecision:
        """Return the variant chosen for ``subject_id``."""
        if not subject_id:
            raise ValueError("subject_id must not be empty")
        bucket = self._bucket(subject_id)
        variant = self._variant_for_bucket(bucket, split)
        return SplitDecision(variant_id=variant, bucket=bucket)

    def bucket_for(self, subject_id: str) -> int:
        """Return the raw bucket id (test/audit hook)."""
        if not subject_id:
            raise ValueError("subject_id must not be empty")
        return self._bucket(subject_id)

    def _bucket(self, subject_id: str) -> int:
        digest = hashlib.blake2b(
            self._salt + b"|" + subject_id.encode("utf-8"),
            digest_size=8,
        ).digest()
        return int.from_bytes(digest, "big") % _RESOLUTION

    @staticmethod
    def _variant_for_bucket(
        bucket: int,
        split: TrafficSplit,
    ) -> str:
        ordered: Sequence[tuple[str, float]] = sorted(
            split.weights.items(),
        )
        total = sum(w for _, w in ordered)
        # Quantise each variant's slice into the resolution grid;
        # tail variant absorbs any rounding leftover.
        cumulative = 0
        last_idx = len(ordered) - 1
        for idx, (variant_id, weight) in enumerate(ordered):
            slice_size = int(round(weight / total * _RESOLUTION))
            if idx == last_idx:
                # Last slice fills the tail to compensate for
                # accumulated rounding error.
                if bucket >= cumulative:
                    return variant_id
            else:
                if bucket < cumulative + slice_size:
                    return variant_id
                cumulative += slice_size
        # Unreachable — last branch above always matches.
        return ordered[-1][0]
