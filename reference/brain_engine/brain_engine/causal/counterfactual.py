"""Counterfactual reasoning over scenario state.

The existing :mod:`brain_engine.causal` graph answers *"why did X
happen?"*.  The counterfactual layer answers the dual question:
*"what would have happened if Y had been true instead?"*.  It
runs a small, deterministic forward simulation over a registered
set of :class:`CausalLink` rules — no LLM, no async, no I/O.

Use cases (advisory §12):

* PM coaching: "If you had answered within 30 minutes, would the
  guest still have left a 2★ review?"
* Skill rollback diagnostics: "If the new parking rule had not
  fired, would this conversation have escalated?"
* What-if replays piped from
  :class:`brain_engine.debug.replay_engine.InMemoryReplayEngine`.

A :class:`CounterfactualReasoner` owns a tuple of immutable
:class:`CausalLink` rules.  Each rule decides *whether* it
applies to the current state, and if so, returns an updated
state plus a one-line explanation.  The reasoner runs links in
priority order with cycle detection and emits a
:class:`CounterfactualOutcome` with the full reasoning trace.

Determinism: identical inputs always yield identical outputs;
links are sorted by ``priority`` (descending), then ``name``
(ascending) for tie-breaking.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

__all__ = [
    "DEFAULT_MAX_HOPS",
    "CausalLink",
    "Counterfactual",
    "CounterfactualOutcome",
    "CounterfactualReasoner",
    "LinkResult",
]


DEFAULT_MAX_HOPS: Final[int] = 16
"""Cycle-safety cap.  A reasoner that fires more than this many
times on a single counterfactual is almost certainly looping;
``MaxHopsExceededError`` is raised in that case."""


@dataclass(frozen=True, slots=True)
class Counterfactual:
    """An intervention applied to a base scenario state.

    Attributes:
        scenario_id: Identifier of the base scenario the
            counterfactual reasons about.
        intervention: Mapping of state keys to override values.
            Keys not present here keep their base value.
    """

    scenario_id: str
    intervention: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario_id must not be empty")
        if not isinstance(self.intervention, Mapping):
            raise TypeError("intervention must be a Mapping")
        # Freeze the mapping defensively so the reasoner cannot
        # mutate the caller's dict via `state.update()`.
        object.__setattr__(
            self,
            "intervention",
            MappingProxyType(dict(self.intervention)),
        )


@dataclass(frozen=True, slots=True)
class LinkResult:
    """Outcome of a single :class:`CausalLink` firing.

    Attributes:
        new_state: Updated state mapping.  Must include every key
            from the input state — missing keys are interpreted
            as "removed" and rejected by the reasoner.
        explanation: One-line human-readable reason.  Used as the
            i-th entry of :attr:`CounterfactualOutcome.reasoning_trace`.
    """

    new_state: Mapping[str, object]
    explanation: str

    def __post_init__(self) -> None:
        if not isinstance(self.new_state, Mapping):
            raise TypeError("new_state must be a Mapping")
        if not self.explanation:
            raise ValueError("explanation must not be empty")


@runtime_checkable
class CausalLink(Protocol):
    """A reusable forward-simulation rule.

    Implementations must be deterministic and side-effect-free.
    """

    @property
    def name(self) -> str:
        ...

    @property
    def priority(self) -> int:
        """Higher values fire earlier.  Negative values are
        permitted for explicit deprioritisation."""

    def applies(self, state: Mapping[str, object]) -> bool:
        ...

    def apply(self, state: Mapping[str, object]) -> LinkResult:
        ...


@dataclass(frozen=True, slots=True)
class CounterfactualOutcome:
    """Result of running a :class:`CounterfactualReasoner`.

    Attributes:
        scenario_id: Carries the input ``scenario_id`` through.
        final_state: State after the last link fired.  Frozen
            against caller mutation.
        reasoning_trace: Tuple of ``LinkResult.explanation`` strings
            in firing order.
        links_fired: Names of links that fired (one per hop).
            Length matches ``reasoning_trace``.
    """

    scenario_id: str
    final_state: Mapping[str, object]
    reasoning_trace: tuple[str, ...]
    links_fired: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario_id must not be empty")
        if len(self.reasoning_trace) != len(self.links_fired):
            raise ValueError(
                "reasoning_trace and links_fired must align",
            )
        object.__setattr__(
            self,
            "final_state",
            MappingProxyType(dict(self.final_state)),
        )


class MaxHopsExceededError(RuntimeError):
    """Raised when a reasoner runs past ``max_hops``."""


class CounterfactualReasoner:
    """Run :class:`CausalLink` rules forward from an intervention.

    Args:
        links: Sequence of links composing the simulator.  Order
            does not matter; the reasoner sorts internally.
        max_hops: Safety cap on the number of times a link can
            fire — protects against infinite loops in poorly
            written rule sets.

    Raises:
        ValueError: If two links share a name.
    """

    def __init__(
        self,
        links: Sequence[CausalLink],
        *,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> None:
        if max_hops < 1:
            raise ValueError("max_hops must be >= 1")
        seen: set[str] = set()
        for link in links:
            if link.name in seen:
                raise ValueError(
                    f"duplicate link name: {link.name!r}",
                )
            seen.add(link.name)
        self._links: tuple[CausalLink, ...] = tuple(
            sorted(
                links,
                key=lambda link: (-link.priority, link.name),
            ),
        )
        self._max_hops = max_hops

    @property
    def max_hops(self) -> int:
        return self._max_hops

    def link_names(self) -> tuple[str, ...]:
        """Names in firing-priority order."""
        return tuple(link.name for link in self._links)

    def reason(
        self,
        base_state: Mapping[str, object],
        counterfactual: Counterfactual,
    ) -> CounterfactualOutcome:
        """Run the simulation and return the outcome."""
        if not isinstance(base_state, Mapping):
            raise TypeError("base_state must be a Mapping")

        state: dict[str, object] = dict(base_state)
        state.update(counterfactual.intervention)
        original_keys = set(state)

        trace: list[str] = []
        fired: list[str] = []
        hops = 0

        # Greedy fixed-point: each pass walks links in priority
        # order; we restart from the top whenever a link fires
        # so high-priority links can react to changes from
        # low-priority ones.  Bounded by ``max_hops`` total
        # firings.
        while True:
            if hops >= self._max_hops:
                raise MaxHopsExceededError(
                    f"reasoner exceeded {self._max_hops} hops "
                    f"on {counterfactual.scenario_id!r}",
                )
            advanced = False
            for link in self._links:
                if not link.applies(state):
                    continue
                result = link.apply(state)
                if set(result.new_state) != original_keys:
                    raise ValueError(
                        f"link {link.name!r} reshaped state "
                        f"keys; expected {sorted(original_keys)}",
                    )
                if dict(result.new_state) == state:
                    # Idempotent fire — skip to avoid loops.
                    continue
                state = dict(result.new_state)
                trace.append(result.explanation)
                fired.append(link.name)
                hops += 1
                advanced = True
                break
            if not advanced:
                break

        return CounterfactualOutcome(
            scenario_id=counterfactual.scenario_id,
            final_state=state,
            reasoning_trace=tuple(trace),
            links_fired=tuple(fired),
        )
