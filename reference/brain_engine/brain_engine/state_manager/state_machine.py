"""State Machine for managing conversation flow.

Provides a generic finite state machine with enum-based states and
event-driven transitions. The state machine enforces valid transitions
and emits callbacks on state changes for downstream consumers (e.g.,
the StateBroadcaster for AG-UI streaming).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BaseState(StrEnum):
    """Default conversation states provided by the universal chassis.

    Projects should define their own state enum that extends or replaces this.
    """

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SLOT_FILLING = "slot_filling"
    CONFIRMING = "confirming"
    EXECUTING = "executing"
    RESPONDING = "responding"
    ERROR = "error"
    COMPLETED = "completed"


@dataclass(frozen=True)
class Transition:
    """Defines a valid state transition.

    Supports two calling styles:
        # Event-driven style:
        Transition(source="A", event="go", target="B")

        # Direct state-to-state style (used by flows):
        Transition(from_state="A", to_state="B")
        Transition(from_state="A", to_state="B", condition=lambda ctx: True)
    """

    source: str = ""
    event: str = ""
    target: str = ""
    from_state: str = ""
    to_state: str = ""
    guard: Callable[[dict[str, Any]], bool] | None = field(default=None, repr=False)
    condition: Callable[[dict[str, Any]], bool] | None = field(default=None, repr=False)
    action: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)

    @property
    def effective_source(self) -> str:
        return self.source or self.from_state

    @property
    def effective_target(self) -> str:
        return self.target or self.to_state

    @property
    def effective_guard(self) -> Callable[[dict[str, Any]], bool] | None:
        return self.guard or self.condition


class StateMachine:
    """A generic finite state machine for conversation flow control.

    Supports two modes:
    1. Event-driven: transition("event_name")
    2. Direct: transition(to_state="TARGET_STATE")

    Args:
        initial_state: The starting state of the machine.
        transitions: List of valid Transition definitions.
        states: Optional list of valid states (for documentation/validation).
    """

    def __init__(
        self,
        initial_state: str | BaseState = BaseState.IDLE,
        transitions: list[Transition] | None = None,
        *,
        states: list[str] | None = None,
    ) -> None:
        self._current_state: str = str(initial_state)
        self._valid_states: set[str] | None = set(states) if states else None
        # Event-driven transitions: (source, event) -> Transition
        self._event_transitions: dict[tuple[str, str], Transition] = {}
        # Direct transitions: (source, target) -> Transition
        self._direct_transitions: dict[tuple[str, str], Transition] = {}
        self._history: list[dict[str, Any]] = []
        self._on_change_callbacks: list[Callable[[str, str, str], None]] = []
        self._context: dict[str, Any] = {}

        if transitions:
            for t in transitions:
                self.add_transition(t)

    @property
    def current_state(self) -> str:
        return self._current_state

    @property
    def is_terminal(self) -> bool:
        """True if no transitions are available from current state."""
        has_event = any(
            src == self._current_state
            for (src, _) in self._event_transitions
        )
        has_direct = any(
            src == self._current_state
            for (src, _) in self._direct_transitions
        )
        return not has_event and not has_direct

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    @property
    def context(self) -> dict[str, Any]:
        return self._context

    def add_transition(self, transition: Transition) -> None:
        """Register a transition definition."""
        src = transition.effective_source
        tgt = transition.effective_target

        if transition.event:
            # Event-driven style
            key = (src, transition.event)
            self._event_transitions[key] = transition
        else:
            # Direct state-to-state style
            key = (src, tgt)
            self._direct_transitions[key] = transition

    def on_change(self, callback: Callable[[str, str, str], None]) -> None:
        """Register a callback invoked on every successful state change."""
        self._on_change_callbacks.append(callback)

    def can_transition(self, event: str = "", *, to_state: str = "") -> bool:
        """Check whether a transition is possible."""
        if to_state:
            key = (self._current_state, to_state)
            t = self._direct_transitions.get(key)
        else:
            key = (self._current_state, event)
            t = self._event_transitions.get(key)

        if t is None:
            return False

        g = t.effective_guard
        if g is not None:
            try:
                return g(self._context)
            except Exception:
                return False
        return True

    def transition(self, event: str = "", *, to_state: str = "") -> str:
        """Execute a state transition.

        Two styles:
            sm.transition("event_name")      # event-driven
            sm.transition(to_state="TARGET")  # direct
        """
        if to_state:
            key = (self._current_state, to_state)
            t = self._direct_transitions.get(key)
            label = f"-> {to_state}"
        else:
            key = (self._current_state, event)
            t = self._event_transitions.get(key)
            label = event

        if t is None:
            # For direct transitions, allow if target is a valid state
            if to_state and (self._valid_states is None or to_state in self._valid_states):
                from_state = self._current_state
                self._current_state = to_state
                self._history.append(
                    {"from": from_state, "event": f"-> {to_state}", "to": to_state}
                )
                logger.info("State transition: %s -> %s (direct)", from_state, to_state)
                for cb in self._on_change_callbacks:
                    try:
                        cb(from_state, f"-> {to_state}", to_state)
                    except Exception as exc:
                        logger.error("on_change callback raised: %s", exc)
                return self._current_state

            raise ValueError(
                f"No transition defined for '{label}' "
                f"from state '{self._current_state}'"
            )

        g = t.effective_guard
        if g is not None and not g(self._context):
            raise ValueError(
                f"Guard condition failed for '{label}' "
                f"from state '{self._current_state}'"
            )

        from_state = self._current_state
        target = t.effective_target

        if t.action is not None:
            try:
                t.action(self._context)
            except Exception as exc:
                logger.error("Transition action failed: %s", exc)
                raise

        self._current_state = target
        self._history.append(
            {"from": from_state, "event": label, "to": target}
        )

        logger.info("State transition: %s --%s--> %s", from_state, label, target)

        for cb in self._on_change_callbacks:
            try:
                cb(from_state, label, target)
            except Exception as exc:
                logger.error("on_change callback raised: %s", exc)

        return self._current_state

    def get_available_transitions(self) -> list[str]:
        """List all events/targets available from the current state."""
        events = [
            event
            for (source, event) in self._event_transitions
            if source == self._current_state
        ]
        targets = [
            tgt
            for (source, tgt) in self._direct_transitions
            if source == self._current_state
        ]
        return events + targets

    def reset(self, initial_state: str | BaseState = BaseState.IDLE) -> None:
        """Reset the machine to a given state and clear history."""
        self._current_state = str(initial_state)
        self._history.clear()
        self._context.clear()

    def __repr__(self) -> str:
        available = self.get_available_transitions()
        return (
            f"StateMachine(state={self._current_state!r}, "
            f"available={available})"
        )
