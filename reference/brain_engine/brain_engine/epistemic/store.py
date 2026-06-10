"""Storage Protocols for observations and beliefs.

The Protocols here are *read-mostly*: callers append observations
through :meth:`record`, snapshot a subject's window through
:meth:`observations_for`, and persist beliefs through
:meth:`promote`.  Nothing in the public surface mutates an existing
observation — that is the immutability contract.

Production wiring will provide an asyncpg-backed implementation
keyed off the same canonical payload the integrity hash signs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from brain_engine.epistemic.models import (
    Belief,
    Observation,
)


__all__ = [
    "BeliefStore",
    "InMemoryBeliefStore",
    "InMemoryObservationStore",
    "ObservationStore",
]


class ObservationStore(Protocol):
    """Append-only store for :class:`Observation` records."""

    def record(self, observation: Observation) -> None:
        """Append an observation to its subject's window."""
        ...

    def observations_for(
        self,
        subject: str,
    ) -> Sequence[Observation]:
        """Return the observation window for ``subject``.

        Returns an empty sequence when no observations have been
        recorded for the subject yet.
        """
        ...


class BeliefStore(Protocol):
    """Snapshot store for promoted :class:`Belief` records."""

    def promote(self, belief: Belief) -> None:
        """Persist a freshly promoted belief.

        Successive promotions for the same ``subject`` overwrite —
        the belief is the *current* inferred state.  History lives
        on the underlying observation log; the audit pack chains
        belief snapshots over time.
        """
        ...

    def current(self, subject: str) -> Belief | None:
        """Return the latest belief for ``subject`` or ``None``."""
        ...


class InMemoryObservationStore:
    """Per-process :class:`ObservationStore` backed by a dict."""

    def __init__(self) -> None:
        self._windows: dict[str, list[Observation]] = {}

    def record(self, observation: Observation) -> None:
        """Append ``observation`` to its subject's window."""
        window = self._windows.setdefault(observation.subject, [])
        window.append(observation)

    def observations_for(
        self,
        subject: str,
    ) -> Sequence[Observation]:
        """Return the observation window for ``subject``."""
        return tuple(self._windows.get(subject, ()))

    def known_subjects(self) -> tuple[str, ...]:
        """Return the subjects with at least one recorded observation."""
        return tuple(self._windows.keys())


class InMemoryBeliefStore:
    """Per-process :class:`BeliefStore` backed by a dict."""

    def __init__(self) -> None:
        self._beliefs: dict[str, Belief] = {}

    def promote(self, belief: Belief) -> None:
        """Persist ``belief`` as the current snapshot."""
        self._beliefs[belief.subject] = belief

    def current(self, subject: str) -> Belief | None:
        """Return the latest belief for ``subject`` or ``None``."""
        return self._beliefs.get(subject)

    def known_subjects(self) -> tuple[str, ...]:
        """Return the subjects with a current belief."""
        return tuple(self._beliefs.keys())
