"""Episodic memory de-duplication consolidator.

Reference: ``brain_engine_advisory.md`` §7.3 — episodic memory can
accumulate near-duplicates (e.g. "guest asked about parking" × 100).
The consolidator clusters embeddings, keeps a representative per
cluster, and compresses the rest into an ``EpisodeSummary``.  The
advisory pins the expected storage saving at 30–50% in the first
month of operation.

Design constraints:

* **No external ML deps.**  scikit-learn pulls in BLAS at install
  time and is overkill for the cluster sizes we care about (≤ 10⁴
  episodes per property per night).  We use a deterministic greedy
  centroid algorithm — a simplified DBSCAN variant — written against
  pure stdlib so the module imports on the slim runtime image.
* **Embedding is plug-in.**  The consolidator never embeds text
  itself; callers pass an :class:`EmbeddingProvider` so the same
  cluster pass can run against bge-m3, ada-002, or a deterministic
  test stub.
* **Pure function over a snapshot.**  The consolidator does not
  mutate the input list; it returns a :class:`DedupReport` the
  caller writes back through whichever store owns the episodes
  (Redis episodic, asyncpg history, ...).

The deterministic greedy variant is sufficient for the sizes the
engine encounters per consolidation window; if real production data
ever exceeds that envelope we can swap the algorithm behind the
:class:`Consolidator` Protocol without touching call-sites.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    """One immutable episode awaiting clustering."""

    episode_id: str
    text: str
    embedding: tuple[float, ...]
    occurred_at: datetime
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.episode_id:
            raise ValueError("EpisodeRecord.episode_id required")
        if not self.embedding:
            raise ValueError("EpisodeRecord.embedding must be non-empty")


@dataclass(frozen=True, slots=True)
class EpisodeSummary:
    """Compressed cluster representative + folded siblings."""

    representative_id: str
    member_ids: tuple[str, ...]
    sample_size: int
    earliest_at: datetime
    latest_at: datetime

    @property
    def is_singleton(self) -> bool:
        return self.sample_size == 1


@dataclass(frozen=True, slots=True)
class DedupReport:
    """Output of one consolidation pass."""

    summaries: tuple[EpisodeSummary, ...]
    kept_ids: tuple[str, ...]
    removed_ids: tuple[str, ...]
    total_input: int

    @property
    def reduction_ratio(self) -> float:
        """Fraction of the input that was folded into a representative."""
        if self.total_input == 0:
            return 0.0
        return len(self.removed_ids) / self.total_input


@dataclass(frozen=True, slots=True)
class DedupConfig:
    """Tunables for the greedy clustering pass."""

    similarity_threshold: float = 0.85
    min_cluster_size: int = 1
    batch_size: int = 1000

    def __post_init__(self) -> None:
        if not 0.0 < self.similarity_threshold <= 1.0:
            raise ValueError(
                "similarity_threshold must be in (0, 1]",
            )
        if self.min_cluster_size < 1:
            raise ValueError("min_cluster_size must be ≥ 1")
        if self.batch_size < 1:
            raise ValueError("batch_size must be ≥ 1")


class EmbeddingProvider(Protocol):
    """Resolves text → embedding vector."""

    def embed(self, text: str) -> tuple[float, ...]: ...


class Consolidator(Protocol):
    """Clusters episodes and returns a deduplication report."""

    def consolidate(
        self,
        episodes: Sequence[EpisodeRecord],
    ) -> DedupReport: ...


def cosine_similarity(
    a: Sequence[float],
    b: Sequence[float],
) -> float:
    """Return cosine similarity in [-1, 1].

    Pure stdlib so the module loads without numpy.  Callers should
    L2-normalise once at the embedding layer if they care about
    repeated calls; here we re-compute the norms each call to keep
    the function self-contained for tests.
    """
    if len(a) != len(b):
        raise ValueError("embedding length mismatch")
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


class EpisodicDedupConsolidator:
    """Greedy single-pass clustering against a similarity threshold.

    The first episode in input order seeds a cluster.  Each subsequent
    episode either joins the cluster whose centroid is closest above
    the threshold, or seeds a new cluster.  The centroid is the
    running mean of member embeddings.

    Determinism: input order matters.  Callers should sort by
    ``occurred_at`` before invoking so reruns are reproducible.
    """

    def __init__(self, config: DedupConfig | None = None) -> None:
        self._config = config or DedupConfig()

    @property
    def config(self) -> DedupConfig:
        return self._config

    def consolidate(
        self,
        episodes: Sequence[EpisodeRecord],
    ) -> DedupReport:
        if not episodes:
            return DedupReport(
                summaries=(),
                kept_ids=(),
                removed_ids=(),
                total_input=0,
            )
        clusters: list[_Cluster] = []
        for episode in episodes:
            cluster = self._best_cluster(episode, clusters)
            if cluster is None:
                clusters.append(_Cluster.seed(episode))
            else:
                cluster.add(episode)
        return self._build_report(clusters, total=len(episodes))

    def _best_cluster(
        self,
        episode: EpisodeRecord,
        clusters: Iterable[_Cluster],
    ) -> _Cluster | None:
        threshold = self._config.similarity_threshold
        best: _Cluster | None = None
        best_sim = threshold
        for cluster in clusters:
            sim = cosine_similarity(
                episode.embedding,
                cluster.centroid,
            )
            if sim >= best_sim:
                best_sim = sim
                best = cluster
        return best

    def _build_report(
        self,
        clusters: list[_Cluster],
        *,
        total: int,
    ) -> DedupReport:
        summaries: list[EpisodeSummary] = []
        kept: list[str] = []
        removed: list[str] = []
        min_size = self._config.min_cluster_size
        for cluster in clusters:
            if cluster.size < min_size:
                # Singletons below the threshold survive as-is —
                # nothing to fold.
                kept.extend(cluster.member_ids)
                summaries.append(cluster.to_summary())
                continue
            summary = cluster.to_summary()
            summaries.append(summary)
            kept.append(summary.representative_id)
            removed.extend(m for m in cluster.member_ids if m != summary.representative_id)
        return DedupReport(
            summaries=tuple(summaries),
            kept_ids=tuple(kept),
            removed_ids=tuple(removed),
            total_input=total,
        )


class _Cluster:
    """Mutable accumulator the consolidator threads through the pass."""

    __slots__ = (
        "_centroid",
        "_members",
        "_earliest",
        "_latest",
        "_representative",
    )

    def __init__(
        self,
        *,
        centroid: list[float],
        representative: EpisodeRecord,
    ) -> None:
        self._centroid = centroid
        self._members: list[EpisodeRecord] = [representative]
        self._earliest = representative.occurred_at
        self._latest = representative.occurred_at
        self._representative = representative

    @classmethod
    def seed(cls, episode: EpisodeRecord) -> _Cluster:
        return cls(
            centroid=list(episode.embedding),
            representative=episode,
        )

    @property
    def centroid(self) -> tuple[float, ...]:
        return tuple(self._centroid)

    @property
    def size(self) -> int:
        return len(self._members)

    @property
    def member_ids(self) -> tuple[str, ...]:
        return tuple(m.episode_id for m in self._members)

    def add(self, episode: EpisodeRecord) -> None:
        if len(episode.embedding) != len(self._centroid):
            raise ValueError("embedding dimensionality mismatch")
        # Online mean update — O(d) per add, O(n·d) total.
        n_old = len(self._members)
        n_new = n_old + 1
        for i, x in enumerate(episode.embedding):
            self._centroid[i] = (self._centroid[i] * n_old + x) / n_new
        self._members.append(episode)
        if episode.occurred_at < self._earliest:
            self._earliest = episode.occurred_at
        if episode.occurred_at > self._latest:
            self._latest = episode.occurred_at

    def to_summary(self) -> EpisodeSummary:
        return EpisodeSummary(
            representative_id=self._representative.episode_id,
            member_ids=self.member_ids,
            sample_size=self.size,
            earliest_at=self._earliest,
            latest_at=self._latest,
        )
