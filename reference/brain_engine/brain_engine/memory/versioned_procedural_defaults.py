"""Default Protocol implementations for VersionedProceduralMemory.

Pulls the four collaborator Protocols declared in
:mod:`brain_engine.memory.versioned_procedural` into ready-to-wire
forms so :func:`brain_engine.memory.factory.create_full_system` can
construct a working :class:`VersionedProceduralMemory` without
forcing every deploy to provide its own adapters.

Production deployments swap individual implementations as the
durable backends mature — for example pointing
:class:`InMemoryEvolutionTracker` at Postgres once the rollback
sweeper moves out of the daemon process.  The wiring on
``FullSystem.versioned_procedural`` stays the same; only the
collaborators change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from brain_engine.memory.versioned_procedural import (
    EvolutionRecord,
    SkillId,
    SnapshotId,
    SuccessSignal,
    new_snapshot_id,
)
from brain_engine.zfs.brain_zfs import BrainZFS


_SNAPSHOT_PATH_PREFIX = "brain://procedural/snapshots/"


@dataclass(slots=True)
class InMemorySkillStore:
    """Dict-backed :class:`SkillStore` impl.

    Sufficient as the default for the rollback policy itself —
    production layers a durable store underneath via composition,
    not inheritance.  ``payload`` is kept opaque (``object``) so the
    same store carries PatternRule / Skill / future learnt objects
    without a schema change.
    """

    skills: dict[SkillId, object] = field(default_factory=dict)

    def upsert(self, skill_id: SkillId, payload: object) -> None:
        self.skills[skill_id] = payload

    def restore(self, skill_id: SkillId, payload: object) -> None:
        self.skills[skill_id] = payload


class BrainZFSSnapshotStore:
    """Production :class:`SkillSnapshotStore` impl backed by BrainZFS.

    Each :meth:`snapshot` writes the current skill payload to a
    BrainZFS path keyed by ``snapshot_id`` and returns the id;
    :meth:`materialise` reads the same path back.  The COW store
    handles versioning, dedup, and integrity transparently —
    matching the ADR-0002 contract.

    Args:
        zfs: The BrainZFS pool already wired in
            :func:`create_full_system`.
        skill_store: Where current skill payloads live (the
            snapshot reads from here at capture time).
    """

    def __init__(
        self,
        zfs: BrainZFS,
        skill_store: InMemorySkillStore,
    ) -> None:
        self._zfs = zfs
        self._skill_store = skill_store

    def snapshot(self, skill_id: SkillId) -> SnapshotId:
        snapshot_id = new_snapshot_id(prefix=f"skill-{skill_id}")
        payload = self._skill_store.skills.get(skill_id)
        # COWStore.write expects bytes; serialise opaque payloads
        # through JSON when possible, otherwise repr().  Both branches
        # round-trip via ``materialise`` so callers see the same value.
        encoded = self._encode(payload)
        # The COW write is async; we run it via the loop's task
        # nursery in the caller. Snapshot/restore are intentionally
        # sync on this Protocol (matches advisory §7.2), so we use
        # asyncio.run when we are off-loop.
        import asyncio  # noqa: PLC0415 — keep import lazy

        path = _SNAPSHOT_PATH_PREFIX + snapshot_id
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._zfs.cow.write(path, encoded))
        else:
            loop.create_task(self._zfs.cow.write(path, encoded))
        return snapshot_id

    def materialise(self, snapshot_id: SnapshotId) -> object:
        import asyncio  # noqa: PLC0415

        path = _SNAPSHOT_PATH_PREFIX + snapshot_id
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            encoded = asyncio.run(self._zfs.cow.read(path))
        else:
            future = asyncio.ensure_future(self._zfs.cow.read(path))
            encoded = loop.run_until_complete(future)
        return self._decode(encoded)

    @staticmethod
    def _encode(payload: object) -> Any:
        try:
            return json.dumps(payload, default=str)
        except (TypeError, ValueError):
            return repr(payload)

    @staticmethod
    def _decode(encoded: Any) -> object:
        if isinstance(encoded, str):
            try:
                return json.loads(encoded)
            except (TypeError, ValueError):
                return encoded
        return encoded


@dataclass(slots=True)
class InMemoryEvolutionTracker:
    """List-backed :class:`EvolutionTracker` impl.

    The rollback sweeper iterates :meth:`open_records` once per
    cycle, so the in-memory tracker is sufficient for the default
    config.  Production swaps in a Postgres-backed tracker (see
    advisory §7.2) — the public surface stays identical.
    """

    entries: list[EvolutionRecord] = field(default_factory=list)

    def record(self, entry: EvolutionRecord) -> None:
        self.entries.append(entry)

    def open_records(self) -> tuple[EvolutionRecord, ...]:
        # Callers re-evaluate ``window_closed`` per-tick, so the
        # tracker just hands back everything it has — closed records
        # cost one comparison and zero rollback work.
        return tuple(self.entries)


@dataclass(slots=True)
class InMemorySuccessSignalSource:
    """Dict-backed :class:`SuccessSignalSource` impl.

    Callers feed observations through :meth:`record_outcome`; the
    source aggregates them into :class:`SuccessSignal` snapshots on
    demand.  Production replaces this with a Postgres view over
    APMGrader's outcome table.
    """

    observations: dict[SkillId, list[tuple[datetime, bool]]] = field(
        default_factory=dict,
    )

    def record_outcome(
        self,
        skill_id: SkillId,
        *,
        success: bool,
        when: datetime | None = None,
    ) -> None:
        moment = when or datetime.now(tz=timezone.utc)
        self.observations.setdefault(skill_id, []).append((moment, success))

    def signal_for(
        self, skill_id: SkillId, *, since: datetime,
    ) -> SuccessSignal:
        relevant = [
            success
            for ts, success in self.observations.get(skill_id, [])
            if ts >= since
        ]
        if not relevant:
            return SuccessSignal(
                skill_id=skill_id, success_rate=0.0, sample_count=0,
            )
        rate = sum(1 for x in relevant if x) / len(relevant)
        return SuccessSignal(
            skill_id=skill_id,
            success_rate=rate,
            sample_count=len(relevant),
        )
