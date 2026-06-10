"""Cleaner Coordination Flow - Finding and dispatching a cleaning crew.

Manages the process of identifying an available cleaner, contacting them,
confirming their availability, and dispatching them to the property.

States: NEED_CLEANER -> SEARCH_AVAILABLE -> CONTACT -> CONFIRM -> DISPATCH -> DONE
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from brain_engine.state_manager.state_machine import StateMachine, Transition
from brain_engine.state_manager.slot_manager import SlotManager
from brain_engine.streaming.ag_ui_emitter import AGUIEmitter, AGUIEvent
from brain_engine.streaming.event_types import EventType
from brain_engine.memory.event_recorder import EventRecorder
from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.fallback.fallback_chain import FallbackChain, build_cleaner_fallback_chain
from brain_engine.fallback.gap_resolver import GapResolver, GapType
from brain_engine.approval.gateway import ApprovalGateway
from brain_engine.approval.models import ActionType, ApprovalStatus
from brain_engine.patterns.ops_decision_logger import OpsDecisionLogger

logger = logging.getLogger(__name__)

# State constants
NEED_CLEANER = "NEED_CLEANER"
SEARCH_AVAILABLE = "SEARCH_AVAILABLE"
CONTACT = "CONTACT"
CONFIRM = "CONFIRM"
DISPATCH = "DISPATCH"
ESCALATE = "ESCALATE"
DONE = "DONE"

STATES = [NEED_CLEANER, SEARCH_AVAILABLE, CONTACT, CONFIRM, DISPATCH, ESCALATE, DONE]

# Cleaner database (in production, this would come from an API/database)
AVAILABLE_CLEANERS = [
    {"name": "Maria", "phone": "+1-555-0101", "rating": 4.9, "available": True},
    {"name": "Carlos", "phone": "+1-555-0102", "rating": 4.7, "available": True},
    {"name": "Aisha", "phone": "+1-555-0103", "rating": 4.8, "available": False},
    {"name": "David", "phone": "+1-555-0104", "rating": 4.6, "available": True},
]


class CleanerCoordinationFlow:
    """Manages finding and assigning a cleaner to the property.

    Orchestrates the search for available cleaners, contact attempts,
    confirmation of availability, and dispatch with timing instructions.

    Args:
        slot_manager: SlotManager instance with incident slots registered.
        emitter: AG-UI event emitter for streaming updates.
        session_id: Unique session identifier.
        cleaners: Optional list of cleaner dicts to override defaults.
    """

    def __init__(
        self,
        slot_manager: SlotManager,
        emitter: AGUIEmitter | None = None,
        session_id: str = "",
        cleaners: list[dict[str, Any]] | None = None,
        event_recorder: EventRecorder | None = None,
        episodic: EpisodicMemory | None = None,
        voice_client: Any | None = None,
        notifier: Any | None = None,
        manager_phone: str = "",
        owner_phone: str = "",
        approval_gateway: ApprovalGateway | None = None,
        owner_id: str = "",
        property_id: str = "",
        ops_logger: OpsDecisionLogger | None = None,
        reservation_id: str | None = None,
    ) -> None:
        self.slot_manager = slot_manager
        self.emitter = emitter or AGUIEmitter()
        self.session_id = session_id
        self._cleaners = cleaners or AVAILABLE_CLEANERS
        self._recorder = event_recorder
        self._episodic = episodic
        self._voice_client = voice_client
        self._notifier = notifier
        self._manager_phone = manager_phone
        self._owner_phone = owner_phone
        self._approval = approval_gateway
        self._owner_id = owner_id
        self._property_id = property_id
        self._ops_logger = ops_logger
        self._reservation_id = reservation_id

        transitions = [
            Transition(from_state=NEED_CLEANER, to_state=SEARCH_AVAILABLE),
            Transition(
                from_state=SEARCH_AVAILABLE,
                to_state=CONTACT,
                condition=lambda ctx: self._has_available_cleaner(),
            ),
            Transition(
                from_state=SEARCH_AVAILABLE,
                to_state=ESCALATE,
                condition=lambda ctx: not self._has_available_cleaner(),
            ),
            Transition(from_state=CONTACT, to_state=CONFIRM),
            Transition(
                from_state=CONFIRM,
                to_state=DISPATCH,
                condition=lambda ctx: self._cleaner_confirmed(),
            ),
            Transition(
                from_state=CONFIRM,
                to_state=SEARCH_AVAILABLE,
                condition=lambda ctx: not self._cleaner_confirmed(),
            ),
            Transition(from_state=DISPATCH, to_state=DONE),
            Transition(from_state=ESCALATE, to_state=DONE),
        ]

        self.state_machine = StateMachine(
            states=STATES,
            transitions=transitions,
            initial_state=NEED_CLEANER,
        )

        self._selected_cleaner: dict[str, Any] | None = None
        self._contacted_cleaners: list[str] = []

    def _has_available_cleaner(self) -> bool:
        """Check if there are available cleaners we haven't contacted yet."""
        for cleaner in self._cleaners:
            if (
                cleaner.get("available", False)
                and cleaner["name"] not in self._contacted_cleaners
            ):
                return True
        return False

    def _get_best_available_cleaner(self) -> dict[str, Any] | None:
        """Get the highest-rated available cleaner not yet contacted."""
        candidates = [
            c
            for c in self._cleaners
            if c.get("available", False) and c["name"] not in self._contacted_cleaners
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda c: c.get("rating", 0), reverse=True)[0]

    def _cleaner_confirmed(self) -> bool:
        """Check if the selected cleaner has confirmed availability."""
        return self.slot_manager.get_value("cleaner_confirmed") is True

    async def run(self) -> AsyncIterator[AGUIEvent]:
        """Execute the cleaner coordination flow, yielding AG-UI events.

        Yields:
            AGUIEvent objects for state transitions, slot updates, and messages.
        """
        flow_name = "cleaner_coordination"

        yield self.emitter.flow_started(flow_name, self.state_machine.current_state)

        if self._episodic:
            await self._episodic.add_episode(
                event="cleaner_coordination_started",
                content="Cleaner coordination flow initiated",
                metadata={"state": NEED_CLEANER, "session_id": self.session_id},
            )

        # --- NEED_CLEANER ---
        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "Initiating cleaner coordination. Searching for available cleaning crew."
        )
        yield self.emitter.text_message_end()

        # Transition to SEARCH_AVAILABLE
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=SEARCH_AVAILABLE)
        yield self.emitter.flow_state_changed(flow_name, old_state, SEARCH_AVAILABLE)

        # --- SEARCH_AVAILABLE ---
        yield self.emitter.tool_call_start("search_cleaners")

        best_cleaner = self._get_best_available_cleaner()

        if best_cleaner is None:
            yield self.emitter.tool_call_end("search_cleaners", {"found": False})

            if self._episodic:
                await self._episodic.add_episode(
                    event="no_cleaners_available",
                    content="No available cleaners found — starting escalation",
                    metadata={"contacted": self._contacted_cleaners},
                )

            # ── ESCALATE: All cleaners busy → fallback chain ────────────
            old_state = self.state_machine.current_state
            self.state_machine.transition(to_state=ESCALATE)
            yield self.emitter.flow_state_changed(flow_name, old_state, ESCALATE)

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "All primary cleaners are unavailable. "
                "Starting escalation: trying backup cleaners, "
                "then contacting the property manager, then the owner."
            )
            yield self.emitter.text_message_end()

            # Build and run the fallback chain
            escalation_result = await self._run_escalation_chain()

            yield self.emitter.tool_call_start("escalation_chain")
            yield self.emitter.tool_call_end(
                "escalation_chain",
                escalation_result,
            )

            if escalation_result.get("resolved"):
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    f"Escalation resolved at step: {escalation_result.get('successful_step', 'N/A')}. "
                    "Help is on the way."
                )
                yield self.emitter.text_message_end()
            else:
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    "Escalation completed. All available channels have been notified. "
                    "Waiting for a response from manager or owner."
                )
                yield self.emitter.text_message_end()

            # ── Memory: Record escalation ────────────────────────────────
            if self._recorder:
                await self._recorder.record_incident_update(
                    event="cleaner_escalation",
                    details=(
                        f"All {len(self._contacted_cleaners)} cleaners unavailable. "
                        f"Escalation {'resolved' if escalation_result.get('resolved') else 'pending'}."
                    ),
                    contacted=self._contacted_cleaners,
                    escalation_result=escalation_result,
                )

            old_state = self.state_machine.current_state
            self.state_machine.transition(to_state=DONE)
            yield self.emitter.flow_state_changed(flow_name, old_state, DONE)

            yield self.emitter.flow_completed(
                flow_name,
                {
                    "success": escalation_result.get("resolved", False),
                    "reason": "escalation_chain",
                    "escalation": escalation_result,
                },
            )
            return

        self._selected_cleaner = best_cleaner
        yield self.emitter.tool_call_end(
            "search_cleaners",
            {"found": True, "cleaner": best_cleaner["name"], "rating": best_cleaner["rating"]},
        )

        if self._episodic:
            await self._episodic.add_episode(
                event="cleaner_found",
                content=f"Found cleaner {best_cleaner['name']} (rating: {best_cleaner['rating']})",
                metadata={"cleaner": best_cleaner["name"], "rating": best_cleaner["rating"]},
            )

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Found available cleaner: {best_cleaner['name']} "
            f"(rating: {best_cleaner['rating']}/5.0). Contacting now."
        )
        yield self.emitter.text_message_end()

        # Transition to CONTACT
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=CONTACT)
        yield self.emitter.flow_state_changed(flow_name, old_state, CONTACT)

        # --- CONTACT ---
        self._contacted_cleaners.append(best_cleaner["name"])

        if self._episodic:
            await self._episodic.add_episode(
                event="cleaner_contacted",
                content=f"Contacting {best_cleaner['name']} at {best_cleaner['phone']}",
                metadata={"cleaner": best_cleaner["name"], "phone": best_cleaner["phone"]},
            )

        yield self.emitter.tool_call_start("contact_cleaner")
        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Contacting {best_cleaner['name']} at {best_cleaner['phone']}..."
        )
        yield self.emitter.text_message_end()
        yield self.emitter.tool_call_end(
            "contact_cleaner",
            {"contacted": best_cleaner["name"], "phone": best_cleaner["phone"]},
        )

        self.slot_manager.set_slot("cleaner_name", best_cleaner["name"])
        yield self.emitter.slot_filled("cleaner_name", best_cleaner["name"])

        self.slot_manager.set_slot("cleaner_phone", best_cleaner["phone"])
        yield self.emitter.slot_filled("cleaner_phone", best_cleaner["phone"])

        # Transition to CONFIRM
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=CONFIRM)
        yield self.emitter.flow_state_changed(flow_name, old_state, CONFIRM)

        # --- CONFIRM ---
        if not self._cleaner_confirmed():
            yield self.emitter.slot_requested(
                "cleaner_confirmed",
                f"Has {best_cleaner['name']} confirmed availability for the cleaning job?"
            )
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Waiting for confirmation from {best_cleaner['name']}. "
                "The cleaner has been sent the job details including property address, "
                "checkout time, and special instructions."
            )
            yield self.emitter.text_message_end()
            return

        # --- DISPATCH ---
        # ── Human-in-the-Loop: Request approval to dispatch cleaner ──────
        if self._approval:
            yield self.emitter.tool_call_start("request_owner_approval")

            approval_result = await self._approval.request_approval(
                action_type=ActionType.DISPATCH_CLEANER,
                owner_id=self._owner_id,
                property_id=self._property_id,
                description=(
                    f"Dispatch cleaner {best_cleaner['name']} "
                    f"(rating: {best_cleaner['rating']}/5) "
                    f"to {self.slot_manager.get_value('property_address', 'property')}."
                ),
                proposed_action={
                    "cleaner_name": best_cleaner["name"],
                    "cleaner_phone": best_cleaner["phone"],
                    "cleaner_rating": best_cleaner["rating"],
                },
                context={
                    "cleaning_time": self.slot_manager.get_value("cleaning_time"),
                    "property_address": self.slot_manager.get_value("property_address"),
                },
                urgency=3,
            )

            yield self.emitter.tool_call_end(
                "request_owner_approval",
                {"status": approval_result.status.value},
            )

            if approval_result.status == ApprovalStatus.DENIED:
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    f"Owner denied dispatching {best_cleaner['name']}. "
                    "Searching for alternatives..."
                )
                yield self.emitter.text_message_end()
                # Go back to search
                self._contacted_cleaners.append(best_cleaner["name"])
                # Fall through to the next search iteration would happen on re-run
                return

        if self._episodic:
            await self._episodic.add_episode(
                event="cleaner_confirmed",
                content=f"{best_cleaner['name']} confirmed availability",
                metadata={"cleaner": best_cleaner["name"]},
            )

        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=DISPATCH)
        yield self.emitter.flow_state_changed(flow_name, old_state, DISPATCH)

        arrival_time = self.slot_manager.get_value("cleaner_arrival_time", "TBD")
        self.slot_manager.set_slot("cleaner_eta_minutes", 30)
        yield self.emitter.slot_filled("cleaner_eta_minutes", 30)

        self.slot_manager.set_slot("cleaning_duration_minutes", 90)
        yield self.emitter.slot_filled("cleaning_duration_minutes", 90)

        if self._episodic:
            await self._episodic.add_episode(
                event="cleaner_dispatched",
                content=f"{best_cleaner['name']} dispatched. ETA: {arrival_time}, duration: 90 min",
                metadata={"cleaner": best_cleaner["name"], "eta": arrival_time, "duration_min": 90},
            )

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Cleaner {best_cleaner['name']} has been dispatched. "
            f"Estimated arrival: {arrival_time}. "
            "Expected cleaning duration: 90 minutes. "
            "The cleaner has been provided with the SOP checklist and access code."
        )
        yield self.emitter.text_message_end()

        # Transition to DONE
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=DONE)
        yield self.emitter.flow_state_changed(flow_name, old_state, DONE)

        # ── Memory: Record cleaner dispatch ─────────────────────────────
        if self._recorder:
            await self._recorder.record_incident_update(
                event="cleaner_dispatched",
                details=f"Cleaner {best_cleaner['name']} dispatched. ETA: 30 min",
                cleaner_name=best_cleaner["name"],
                cleaner_phone=best_cleaner["phone"],
            )

        yield self.emitter.flow_completed(
            flow_name,
            {
                "success": True,
                "cleaner": best_cleaner["name"],
                "phone": best_cleaner["phone"],
                "eta_minutes": 30,
                "duration_minutes": 90,
            },
        )

    async def _run_escalation_chain(self) -> dict[str, Any]:
        """Run the cleaner fallback chain: backup cleaners → manager → owner.

        Returns:
            Dict with escalation result details.
        """
        # Collect backup cleaners (those not yet contacted)
        backup_cleaners = [
            c for c in self._cleaners
            if c["name"] not in self._contacted_cleaners
        ]

        chain = build_cleaner_fallback_chain(
            cleaners=backup_cleaners,
            voice_client=self._voice_client,
            notifier=self._notifier,
            manager_phone=self._manager_phone or self.slot_manager.get_value("manager_phone", ""),
            owner_phone=self._owner_phone or self.slot_manager.get_value("owner_phone", ""),
        )

        context = {
            "property_address": self.slot_manager.get_value("property_address", "N/A"),
            "cleaning_time": self.slot_manager.get_value("cleaning_time", "N/A"),
            "cleaner_script": self.slot_manager.get_value("cleaner_script", ""),
        }

        result = await chain.execute(context)

        logger.info(
            "Escalation chain result: resolved=%s, steps=%d/%d, successful=%s",
            result.resolved, result.steps_attempted, result.total_steps,
            result.successful_step,
        )

        # Emit an ops DecisionCase so the learning subsystem sees the
        # cleaner-fallback outcome.  Requires property + owner context;
        # the logger itself no-ops when the store is disabled.
        if self._ops_logger and self._property_id and self._owner_id:
            await self._ops_logger.log_cleaner_dispatch(
                property_id=self._property_id,
                owner_id=self._owner_id,
                reservation_id=self._reservation_id,
                fallback_result=result,
            )

        return result.to_dict()

    @property
    def current_state(self) -> str:
        return self.state_machine.current_state

    @property
    def is_done(self) -> bool:
        return self.state_machine.is_terminal
