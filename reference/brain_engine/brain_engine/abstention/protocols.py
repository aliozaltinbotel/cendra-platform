"""Storage Protocols for the abstention layer.

Production wiring will add a Postgres-backed implementation that
persists across pod restarts; the in-memory variant here is the
default and the test double.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from typing import Final, Protocol

from brain_engine.abstention.models import CalibrationSample


__all__ = [
    "CalibrationStore",
    "InMemoryCalibrationStore",
]


DEFAULT_WINDOW_SIZE: Final[int] = 200


class CalibrationStore(Protocol):
    """Per-tool sliding-window calibration store.

    Implementations must keep a bounded window per tool — older
    samples drop off as new ones arrive.  Reads return samples in
    insertion order (oldest → newest) so the calibrator can compute
    quantiles deterministically.
    """

    def record(self, sample: CalibrationSample) -> None:
        """Append a calibration sample to its tool's window."""
        ...

    def samples_for(
        self,
        tool_id: str,
    ) -> Sequence[CalibrationSample]:
        """Return the current window for ``tool_id``.

        Returns an empty sequence when the tool has never been
        recorded against.
        """
        ...

    def clear(self, tool_id: str | None = None) -> None:
        """Drop the window for ``tool_id`` or every window."""
        ...


class InMemoryCalibrationStore:
    """Per-process bounded :class:`CalibrationStore`.

    Each tool gets its own :class:`collections.deque` capped at
    ``window_size``.  No locking is provided — callers that share
    the store across asyncio tasks must serialise writes.
    """

    def __init__(
        self,
        *,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        self._window_size = window_size
        self._windows: dict[str, deque[CalibrationSample]] = {}

    def record(self, sample: CalibrationSample) -> None:
        """Append ``sample`` to its tool's window."""
        window = self._windows.get(sample.tool_id)
        if window is None:
            window = deque(maxlen=self._window_size)
            self._windows[sample.tool_id] = window
        window.append(sample)

    def samples_for(
        self,
        tool_id: str,
    ) -> Sequence[CalibrationSample]:
        """Return the recorded window for ``tool_id``."""
        window = self._windows.get(tool_id)
        if window is None:
            return ()
        return tuple(window)

    def clear(self, tool_id: str | None = None) -> None:
        """Drop one tool's window or every tool's window."""
        if tool_id is None:
            self._windows.clear()
            return
        self._windows.pop(tool_id, None)

    def known_tools(self) -> Iterable[str]:
        """Iterate the tool ids with at least one recorded sample."""
        return tuple(self._windows.keys())
