"""Persistence Protocol for the A/B experiment registry.

The math layer (:mod:`brain_engine.experiments.ab_test_engine`) is
deliberately stateless — variant assignment is deterministic and
verdicts are computed on demand from per-variant tallies.  What it
*does* require, in production, is durability: the registry must
survive a pod rollout so that ``min_trials_per_arm`` is reached
across deploys instead of resetting on every restart.

This module defines the structural contract the registry expects
from a durable store, plus an in-memory reference implementation
used by tests and by environments without Postgres available.

The Protocol is intentionally small.  Reads return aggregates
(``trials`` / ``successes`` per variant) rather than streaming
every outcome row, because the registry only ever needs the
aggregate to feed the z-test pipeline.  The append-only outcome
ledger lives behind the store as an implementation detail —
tests that want to inspect raw outcomes can reach into the
concrete store directly.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from brain_engine.experiments.ab_test_engine import Experiment

__all__ = [
    "ExperimentStore",
    "InMemoryExperimentStore",
    "VariantTally",
]


VariantTally = tuple[int, int]
"""Aggregated tally for one variant — ``(trials, successes)``."""


@runtime_checkable
class ExperimentStore(Protocol):
    """Durable store for A/B experiment metadata + outcomes.

    Implementations MUST be safe to call concurrently from the
    event loop; the registry relies on that for outcome bursts.
    """

    async def save_experiment(self, experiment: Experiment) -> None:
        """Persist (or upsert) an experiment registration."""

    async def load_experiments(self) -> list[Experiment]:
        """Return every persisted experiment for warm-up on boot."""

    async def record_outcome(
        self,
        experiment_id: str,
        variant_id: str,
        *,
        success: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one outcome row to the durable ledger."""

    async def load_aggregates(
        self,
        experiment_id: str,
    ) -> Mapping[str, VariantTally]:
        """Return ``{variant_id: (trials, successes)}`` aggregates."""

    async def close(self) -> None:
        """Release any resources owned by the store."""


class InMemoryExperimentStore:
    """Process-local store used by tests and Postgres-less envs.

    Thread / event-loop safety is provided by the GIL and the
    single-threaded asyncio model: every mutator runs to
    completion before another coroutine can interleave, so the
    plain ``dict`` / ``list`` containers are sufficient.
    """

    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}
        self._outcomes: defaultdict[
            tuple[str, str], list[bool]
        ] = defaultdict(list)

    async def save_experiment(self, experiment: Experiment) -> None:
        self._experiments[experiment.experiment_id] = experiment

    async def load_experiments(self) -> list[Experiment]:
        return list(self._experiments.values())

    async def record_outcome(
        self,
        experiment_id: str,
        variant_id: str,
        *,
        success: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        del metadata  # in-memory store ignores per-row metadata
        self._outcomes[(experiment_id, variant_id)].append(success)

    async def load_aggregates(
        self,
        experiment_id: str,
    ) -> Mapping[str, VariantTally]:
        result: dict[str, VariantTally] = {}
        for (exp_id, variant_id), rows in self._outcomes.items():
            if exp_id != experiment_id:
                continue
            trials = len(rows)
            successes = sum(1 for r in rows if r)
            result[variant_id] = (trials, successes)
        return result

    async def close(self) -> None:
        return None
