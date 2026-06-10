"""State Broadcaster - Streams state machine and slot changes as AG-UI events.

Connects the StateMachine and SlotManager to the AG-UI event stream,
automatically broadcasting STATE_DELTA events when states transition
or slots are filled. Designed to be wired up once and then operate
transparently as state changes occur.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from brain_engine.state_manager.slot_manager import SlotManager
from brain_engine.state_manager.state_machine import StateMachine
from brain_engine.streaming.ag_ui_emitter import AGUIEmitter, AGUIEvent
from brain_engine.streaming.event_types import EventType

logger = logging.getLogger(__name__)


class StateBroadcaster:
    """Broadcasts state machine and slot changes to connected UI clients.

    Integrates with StateMachine (via on_change callbacks) and SlotManager
    to emit real-time STATE_DELTA, SLOT_FILLED, and FLOW_STATE_CHANGED
    events through an AGUIEmitter.

    Args:
        emitter: The AGUIEmitter to use for creating and queuing events.
        state_machine: Optional StateMachine to observe. If provided,
            state transitions are automatically broadcast.
        slot_manager: Optional SlotManager. Slot changes are broadcast
            when broadcast_slot_update() is called.
    """

    def __init__(
        self,
        emitter: AGUIEmitter | None = None,
        state_machine: StateMachine | None = None,
        slot_manager: SlotManager | None = None,
    ) -> None:
        self._emitter = emitter or AGUIEmitter()
        self._state_machine = state_machine
        self._slot_manager = slot_manager
        self._subscribers: list[Callable[[AGUIEvent], Awaitable[None]]] = []
        self._last_state: dict[str, Any] = {}

        # Wire up state machine observation
        if self._state_machine is not None:
            self._state_machine.on_change(self._on_state_change)

    def set_state_machine(self, state_machine: StateMachine) -> None:
        """Attach or replace the observed state machine.

        Args:
            state_machine: The StateMachine to observe.
        """
        self._state_machine = state_machine
        state_machine.on_change(self._on_state_change)
        logger.info("StateBroadcaster attached to state machine")

    def set_slot_manager(self, slot_manager: SlotManager) -> None:
        """Attach or replace the observed slot manager.

        Args:
            slot_manager: The SlotManager to observe.
        """
        self._slot_manager = slot_manager
        logger.info("StateBroadcaster attached to slot manager")

    # ── Subscriber management ───────────────────────────────────────

    def subscribe(
        self, callback: Callable[[AGUIEvent], Awaitable[None]]
    ) -> None:
        """Register a subscriber callback for broadcast events.

        The callback will be invoked asynchronously for every state
        change, slot update, or flow transition event.

        Args:
            callback: Async function that receives an AGUIEvent.
        """
        self._subscribers.append(callback)
        logger.debug("Subscriber added. Total: %d", len(self._subscribers))

    def unsubscribe(
        self, callback: Callable[[AGUIEvent], Awaitable[None]]
    ) -> None:
        """Remove a subscriber callback.

        Args:
            callback: The callback to remove.
        """
        self._subscribers = [s for s in self._subscribers if s is not callback]

    # ── Broadcasting methods ────────────────────────────────────────

    async def broadcast_snapshot(self, state: dict[str, Any] | None = None) -> None:
        """Broadcast a full state snapshot.

        If no state is provided, assembles one from the current state
        machine and slot manager.

        Args:
            state: Optional explicit state dict. If None, auto-assembled.
        """
        if state is None:
            state = self._build_current_state()

        event = self._emitter.state_snapshot(state)
        self._last_state = dict(state)
        await self._notify(event)

    async def broadcast_delta(self, delta: dict[str, Any]) -> None:
        """Broadcast a partial state update.

        Args:
            delta: Dictionary of changed state fields.
        """
        event = self._emitter.state_delta(delta)
        self._last_state.update(delta)
        await self._notify(event)

    async def broadcast_slot_update(
        self, slot_name: str, value: Any
    ) -> None:
        """Broadcast a slot fill event and corresponding state delta.

        Args:
            slot_name: Name of the filled slot.
            value: The slot's new value.
        """
        # Emit the specific slot event
        self._emitter.slot_filled(slot_name, value)

        # Also emit a state delta with the updated slot info
        delta = {"slots": {slot_name: value}}
        event = self._emitter.state_delta(delta)
        await self._notify(event)

        logger.info("Broadcast slot update: %s = %s", slot_name, value)

    async def broadcast_flow_change(
        self,
        flow_name: str,
        from_state: str,
        to_state: str,
    ) -> None:
        """Broadcast a flow/state machine transition event.

        Args:
            flow_name: Name of the flow or state machine.
            from_state: The previous state.
            to_state: The new state.
        """
        event = self._emitter.flow_state_changed(flow_name, from_state, to_state)
        await self._notify(event)

        # Also emit a state delta
        delta_event = self._emitter.state_delta({
            "current_state": to_state,
            "previous_state": from_state,
        })
        await self._notify(delta_event)

    async def broadcast_all_slots(self) -> None:
        """Broadcast the full slot state as a state delta.

        Reads from the attached SlotManager and emits the complete
        slot dictionary.
        """
        if self._slot_manager is None:
            return

        slots_dict = self._slot_manager.to_dict()
        event = self._emitter.state_delta({"slots": slots_dict})
        await self._notify(event)

    # ── Internal ────────────────────────────────────────────────────

    def _on_state_change(
        self, from_state: str, event: str, to_state: str
    ) -> None:
        """Synchronous callback invoked by StateMachine.on_change.

        Schedules async broadcast in the running event loop.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.broadcast_flow_change("state_machine", from_state, to_state)
            )
        except RuntimeError:
            # No running event loop - log and skip
            logger.debug(
                "No event loop for state broadcast: %s -> %s",
                from_state,
                to_state,
            )

    def _build_current_state(self) -> dict[str, Any]:
        """Build a state snapshot from attached state machine and slot manager."""
        state: dict[str, Any] = {}

        if self._state_machine is not None:
            state["current_state"] = self._state_machine.current_state
            state["available_transitions"] = (
                self._state_machine.get_available_transitions()
            )

        if self._slot_manager is not None:
            state["slots"] = self._slot_manager.to_dict()
            state["slots_complete"] = self._slot_manager.is_complete()
            state["missing_slots"] = [
                s.name for s in self._slot_manager.get_missing_slots()
            ]

        return state

    async def _notify(self, event: AGUIEvent) -> None:
        """Notify all subscribers of an event."""
        for subscriber in self._subscribers:
            try:
                await subscriber(event)
            except Exception as exc:
                logger.error("Subscriber error: %s", exc)

    # ── Properties ──────────────────────────────────────────────────

    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._subscribers)

    @property
    def last_state(self) -> dict[str, Any]:
        """The last broadcast state snapshot."""
        return dict(self._last_state)

    @property
    def emitter(self) -> AGUIEmitter:
        """The underlying AGUIEmitter instance."""
        return self._emitter
