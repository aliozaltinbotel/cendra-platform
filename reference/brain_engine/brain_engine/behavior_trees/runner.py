"""Tree runner: tick a BT until terminal and collect the trace.

The runner is intentionally thin — py_trees already exposes a
``tick_once()`` entry point on every behaviour.  What we add is:

  * A bounded tick loop so RUNNING-forever bugs cannot wedge the
    caller.
  * A typed :class:`TreeRunResult` with the audit trail py_trees
    by itself does not emit.
  * Deterministic initialisation of the :class:`TreeContext`
    metadata so the leaves write to the audit log we then read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import py_trees
import structlog

from brain_engine.behavior_trees.models import (
    Status,
    TickRecord,
    TreeContext,
)


__all__ = [
    "DEFAULT_MAX_TICKS",
    "TreeRunResult",
    "TreeRunner",
]


DEFAULT_MAX_TICKS: Final[int] = 64


logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TreeRunResult:
    """Outcome of one :meth:`TreeRunner.run` call.

    Attributes:
        status: Terminal :class:`Status` of the root.
        ticks: Number of ticks consumed.
        records: Audit-log entries (oldest first).
        timed_out: ``True`` when the bounded loop exited before
            the root reached a terminal status.
    """

    status: Status
    ticks: int
    records: tuple[TickRecord, ...]
    timed_out: bool


class TreeRunner:
    """Tick a behaviour tree against a :class:`TreeContext`.

    The runner does not own the context — callers pass it in,
    inspect it after :meth:`run`, and may reuse it across runs.
    Each :meth:`run` resets the metadata audit log so consecutive
    runs do not accumulate trace entries from previous calls.
    """

    def __init__(
        self,
        *,
        max_ticks: int = DEFAULT_MAX_TICKS,
    ) -> None:
        if max_ticks < 1:
            raise ValueError("max_ticks must be positive")
        self._max_ticks = max_ticks
        self._log = logger.bind(component="bt_runner")

    def run(
        self,
        *,
        root: py_trees.behaviour.Behaviour,
        context: TreeContext,
    ) -> TreeRunResult:
        """Tick ``root`` against ``context`` until terminal."""
        context.metadata["audit"] = []
        terminal_status = Status.RUNNING
        ticks = 0
        timed_out = False
        for tick in range(1, self._max_ticks + 1):
            root.tick_once()
            ticks = tick
            terminal_status = root.status
            if terminal_status in (
                Status.SUCCESS,
                Status.FAILURE,
            ):
                break
        else:
            timed_out = True
        audit = context.metadata.get("audit", [])
        if not isinstance(audit, list):
            audit = []
        self._log.info(
            "tree.run",
            ticks=ticks,
            status=str(terminal_status),
            timed_out=timed_out,
        )
        return TreeRunResult(
            status=terminal_status,
            ticks=ticks,
            records=tuple(audit),
            timed_out=timed_out,
        )
