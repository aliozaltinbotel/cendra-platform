"""Versioned wrapper around procedural memory with auto-rollback.

Reference: ``brain_engine_advisory.md`` §7.2 — memory versioning +
rollback for SkillEvolution.

The wrapper sits between :class:`brain_engine.skill_evolution` and
the underlying procedural store.  Every evolve call:

1. Snapshots the current skill state through a pluggable
   :class:`SkillSnapshotStore` (BrainZFS in production; in-memory
   for tests).
2. Applies the new rule.
3. Records a tracking entry the rollback sweeper will read.

A separate sweep — typically a nightly cron — walks the tracking
entries and, for any skill whose observed success rate has fallen
below the threshold within the evaluation window, restores the prior
snapshot.  The classes here are agnostic to *what* a skill is; they
only manipulate ``SkillId`` strings and opaque snapshot ids, so the
same wrapper covers PatternRule, Skill, and any future learnt object.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol

SkillId = str
SnapshotId = str


class EvolutionOutcome(str, Enum):
    """Result of an :meth:`VersionedProceduralMemory.evolve` call."""

    APPLIED = "applied"
    SNAPSHOT_FAILED = "snapshot_failed"
    APPLY_FAILED = "apply_failed"


class RollbackOutcome(str, Enum):
    """Why ``auto_rollback_check`` did or did not roll a skill back."""

    ROLLED_BACK = "rolled_back"
    KEPT = "kept"
    INSUFFICIENT_DATA = "insufficient_data"
    WINDOW_OPEN = "window_open"


@dataclass(frozen=True, slots=True)
class EvolutionRecord:
    """Tracking entry the rollback sweeper consumes."""

    skill_id: SkillId
    snapshot_id: SnapshotId
    evolved_at: datetime
    success_threshold: float
    evaluation_window: timedelta
    parent_snapshot: SnapshotId | None = None

    def __post_init__(self) -> None:
        if not 0.0 < self.success_threshold <= 1.0:
            raise ValueError("success_threshold must be in (0, 1]")
        if self.evaluation_window <= timedelta(0):
            raise ValueError("evaluation_window must be positive")

    def window_closed(self, *, now: datetime) -> bool:
        return now - self.evolved_at >= self.evaluation_window


@dataclass(frozen=True, slots=True)
class SuccessSignal:
    """Per-skill performance reading the sweeper compares to threshold."""

    skill_id: SkillId
    success_rate: float
    sample_count: int


class SkillStore(Protocol):
    """Minimum surface a procedural memory must implement."""

    def upsert(self, skill_id: SkillId, payload: object) -> None: ...

    def restore(self, skill_id: SkillId, payload: object) -> None: ...


class SkillSnapshotStore(Protocol):
    """Snapshot + restore primitives (BrainZFS, pg row dump, …)."""

    def snapshot(self, skill_id: SkillId) -> SnapshotId:
        """Capture current state and return an opaque id."""

    def materialise(self, snapshot_id: SnapshotId) -> object:
        """Return the skill payload as it was at snapshot time."""


class EvolutionTracker(Protocol):
    """Persistence for :class:`EvolutionRecord` entries."""

    def record(self, entry: EvolutionRecord) -> None: ...

    def open_records(self) -> tuple[EvolutionRecord, ...]:
        """Records still inside their evaluation window."""


class SuccessSignalSource(Protocol):
    """Provides recent success metrics per skill."""

    def signal_for(
        self, skill_id: SkillId, *, since: datetime,
    ) -> SuccessSignal:
        """Return the post-evolution success rate."""


class VersionedProceduralMemory:
    """Coordinator for snapshot-before-evolve + auto-rollback.

    The class owns no I/O of its own — every operation is delegated
    to the four Protocols above.  That keeps the policy
    (snapshot → apply → track) testable in isolation and the
    transport (BrainZFS, asyncpg, Service Bus) free to swap.
    """

    def __init__(
        self,
        *,
        skills: SkillStore,
        snapshots: SkillSnapshotStore,
        tracker: EvolutionTracker,
        signals: SuccessSignalSource,
        default_threshold: float = 0.85,
        default_window: timedelta = timedelta(days=7),
        min_samples: int = 30,
    ) -> None:
        if not 0.0 < default_threshold <= 1.0:
            raise ValueError("default_threshold must be in (0, 1]")
        if default_window <= timedelta(0):
            raise ValueError("default_window must be positive")
        if min_samples <= 0:
            raise ValueError("min_samples must be positive")
        self._skills = skills
        self._snapshots = snapshots
        self._tracker = tracker
        self._signals = signals
        self._threshold = default_threshold
        self._window = default_window
        self._min_samples = min_samples

    def evolve(
        self,
        *,
        skill_id: SkillId,
        new_payload: object,
        threshold: float | None = None,
        window: timedelta | None = None,
        now: datetime | None = None,
    ) -> EvolutionOutcome:
        """Snapshot, apply, track."""
        clock = now or datetime.now(tz=timezone.utc)
        try:
            snapshot_id = self._snapshots.snapshot(skill_id)
        except Exception:  # pragma: no cover - defensive
            return EvolutionOutcome.SNAPSHOT_FAILED
        try:
            self._skills.upsert(skill_id, new_payload)
        except Exception:  # pragma: no cover - defensive
            return EvolutionOutcome.APPLY_FAILED
        self._tracker.record(
            EvolutionRecord(
                skill_id=skill_id,
                snapshot_id=snapshot_id,
                evolved_at=clock,
                success_threshold=threshold or self._threshold,
                evaluation_window=window or self._window,
            ),
        )
        return EvolutionOutcome.APPLIED

    def auto_rollback_check(
        self, *, now: datetime | None = None,
    ) -> dict[SkillId, RollbackOutcome]:
        """Walk open records; rollback those that fail the threshold."""
        clock = now or datetime.now(tz=timezone.utc)
        results: dict[SkillId, RollbackOutcome] = {}
        for record in self._tracker.open_records():
            results[record.skill_id] = self._evaluate(record, clock)
        return results

    def _evaluate(
        self, record: EvolutionRecord, clock: datetime,
    ) -> RollbackOutcome:
        if not record.window_closed(now=clock):
            return RollbackOutcome.WINDOW_OPEN
        signal = self._signals.signal_for(
            record.skill_id, since=record.evolved_at,
        )
        if signal.sample_count < self._min_samples:
            return RollbackOutcome.INSUFFICIENT_DATA
        if signal.success_rate >= record.success_threshold:
            return RollbackOutcome.KEPT
        prior = self._snapshots.materialise(record.snapshot_id)
        self._skills.restore(record.skill_id, prior)
        return RollbackOutcome.ROLLED_BACK


def new_snapshot_id(prefix: str = "snap") -> SnapshotId:
    """Stable, sortable id; prefix lets the store fan its keys cheaply."""
    return f"{prefix}-{uuid.uuid4().hex}"
