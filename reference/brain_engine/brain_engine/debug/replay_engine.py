"""Conversation replay engine for production debugging.

Reference: ``brain_engine_advisory.md`` §10.1.

The advisory calls out replay as one of the engine's *unique*
capabilities — Brain Engine ships ZFS-style snapshots so we can
reconstruct the exact state of a past conversation, set a breakpoint
at any cascade stage, optionally inject a state modification, and
re-run the rest of the pipeline.

This module owns the **contract**:

* :class:`ReplayBreakpoint` — the cascade stages a developer can
  pin to.  Stable enum so dashboards / docs / runbooks can reference
  symbolic names without coupling to an internal implementation.
* :class:`ReplaySnapshot` — frozen, fully-deterministic state at a
  given breakpoint.
* :class:`StateModifier` — a callable a developer hands the engine
  to describe a what-if mutation between snapshots.
* :class:`ConversationReplayEngine` — Protocol every backend must
  satisfy.  Today's reference implementation
  (:class:`InMemoryReplayEngine`) feeds tests; the production wiring
  to BrainZFS lives under ``brain_engine/zfs/replay.py`` and ships
  in a follow-up branch.

Keeping the contract here means the rest of the engine and the dev
tooling can import a stable surface today, and the BrainZFS-backed
implementation can land without a rename.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol


class ReplayBreakpoint(str, Enum):
    """Cascade stages a developer can pin a replay at."""

    PRE_INTENT = "pre_intent"
    POST_MEMORY = "post_memory"
    PRE_LLM = "pre_llm"
    POST_LLM = "post_llm"
    PRE_RESPONSE = "pre_response"


@dataclass(frozen=True, slots=True)
class ReplaySnapshot:
    """One immutable state observation at a breakpoint."""

    conversation_id: str
    breakpoint: ReplayBreakpoint
    captured_at: datetime
    state: Mapping[str, Any]
    metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}),
    )

    def __post_init__(self) -> None:
        if not self.conversation_id:
            raise ValueError("ReplaySnapshot.conversation_id required")
        if not isinstance(self.state, Mapping):
            raise TypeError("ReplaySnapshot.state must be a Mapping")
        # Freeze the state mapping so callers cannot mutate it in
        # place after the snapshot is captured.
        object.__setattr__(self, "state", MappingProxyType(dict(self.state)))


@dataclass(frozen=True, slots=True)
class ReplayTrace:
    """Ordered sequence of snapshots produced by one replay run."""

    snapshots: tuple[ReplaySnapshot, ...]

    def at(self, breakpoint_: ReplayBreakpoint) -> ReplaySnapshot | None:
        for snapshot in self.snapshots:
            if snapshot.breakpoint is breakpoint_:
                return snapshot
        return None

    def diff(
        self,
        a: ReplayBreakpoint,
        b: ReplayBreakpoint,
    ) -> dict[str, tuple[Any, Any]]:
        """Return key → (before, after) for keys that changed."""
        snap_a = self.at(a)
        snap_b = self.at(b)
        if snap_a is None or snap_b is None:
            return {}
        keys = set(snap_a.state) | set(snap_b.state)
        changes: dict[str, tuple[Any, Any]] = {}
        for key in keys:
            before = snap_a.state.get(key)
            after = snap_b.state.get(key)
            if before != after:
                changes[key] = (before, after)
        return changes


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """What the replay engine returns to the developer."""

    conversation_id: str
    trace: ReplayTrace
    final_state: Mapping[str, Any]
    completed_at: datetime
    modifier_applied: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "final_state",
            MappingProxyType(dict(self.final_state)),
        )


StateModifier = Callable[[ReplaySnapshot], Mapping[str, Any]]
"""Mutates the state at the breakpoint; returns the *new* state.

Convention: pure function, no side effects.  Anything stateful must
live in the caller's closure.  The replay engine never reaches into
the modifier's frame.
"""


class ConversationReplayEngine(Protocol):
    """Contract every replay backend must satisfy."""

    def record(self, snapshot: ReplaySnapshot) -> None:
        """Persist a snapshot for later replay."""

    def replay(
        self,
        *,
        conversation_id: str,
        breakpoint_at: ReplayBreakpoint | None = None,
        modifier: StateModifier | None = None,
    ) -> ReplayResult:
        """Replay ``conversation_id`` end-to-end."""


class InMemoryReplayEngine:
    """Reference implementation backing the test suite.

    The engine threads recorded snapshots in capture order, optionally
    applies a :data:`StateModifier` at the requested breakpoint, and
    returns a :class:`ReplayResult` whose final state reflects the
    modifier.  Because every snapshot is frozen, the engine is safe
    to share across replay calls without copying.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, list[ReplaySnapshot]] = {}

    def record(self, snapshot: ReplaySnapshot) -> None:
        bucket = self._snapshots.setdefault(snapshot.conversation_id, [])
        bucket.append(snapshot)

    def replay(
        self,
        *,
        conversation_id: str,
        breakpoint_at: ReplayBreakpoint | None = None,
        modifier: StateModifier | None = None,
    ) -> ReplayResult:
        snapshots = self._snapshots.get(conversation_id)
        if not snapshots:
            raise KeyError(
                f"no recorded snapshots for {conversation_id!r}",
            )
        sequence = sorted(
            snapshots, key=lambda s: s.captured_at,
        )
        applied = False
        rebuilt: list[ReplaySnapshot] = []
        for snapshot in sequence:
            patched = snapshot
            should_apply = (
                modifier is not None
                and breakpoint_at is not None
                and snapshot.breakpoint is breakpoint_at
                and not applied
            )
            if should_apply:
                # ``modifier`` is non-None inside the guarded branch.
                assert modifier is not None
                new_state = modifier(snapshot)
                patched = ReplaySnapshot(
                    conversation_id=snapshot.conversation_id,
                    breakpoint=snapshot.breakpoint,
                    captured_at=snapshot.captured_at,
                    state=new_state,
                    metadata=snapshot.metadata,
                )
                applied = True
            rebuilt.append(patched)
        return ReplayResult(
            conversation_id=conversation_id,
            trace=ReplayTrace(snapshots=tuple(rebuilt)),
            final_state=rebuilt[-1].state,
            completed_at=datetime.now(tz=timezone.utc),
            modifier_applied=applied,
        )
