"""Late Checkout Flow - Handles late checkout negotiation and approval.

Manages the full lifecycle of a late checkout request, from initial
guest request through policy check, fee negotiation, and confirmation.

Example scenario:
    Guest John wants to check out at 2 PM instead of 11 AM. Next guest
    George is arriving at 4 PM. The flow checks policy, calculates the
    fee ($50 for 1-2h extension), negotiates with John, and confirms.

States: RECEIVED -> CHECK_POLICY -> NEGOTIATE -> CONFIRM -> DONE
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
from brain_engine.approval.gateway import ApprovalGateway
from brain_engine.approval.models import ActionType, ApprovalStatus

logger = logging.getLogger(__name__)

# State constants
RECEIVED = "RECEIVED"
CHECK_POLICY = "CHECK_POLICY"
NEGOTIATE = "NEGOTIATE"
CONFIRM = "CONFIRM"
DONE = "DONE"

STATES = [RECEIVED, CHECK_POLICY, NEGOTIATE, CONFIRM, DONE]

# Late checkout fee schedule (from knowledge base)
FEE_SCHEDULE = {
    (1, 2): 50.0,    # 1-2 hours: $50
    (2, 4): 100.0,   # 2-4 hours: $100
}
MAX_EXTENSION_HOUR = 16  # No later than 4 PM (16:00)


class LateCheckoutFlow:
    """Manages late checkout request negotiation.

    Orchestrates the process from receiving a late checkout request
    through policy verification, fee calculation, guest negotiation,
    and final confirmation.

    Args:
        slot_manager: SlotManager instance with incident slots registered.
        emitter: AG-UI event emitter for streaming updates to the frontend.
        session_id: Unique session identifier.
    """

    def __init__(
        self,
        slot_manager: SlotManager,
        emitter: AGUIEmitter | None = None,
        session_id: str = "",
        event_recorder: EventRecorder | None = None,
        episodic: EpisodicMemory | None = None,
        approval_gateway: ApprovalGateway | None = None,
        owner_id: str = "",
        property_id: str = "",
    ) -> None:
        self.slot_manager = slot_manager
        self.emitter = emitter or AGUIEmitter()
        self.session_id = session_id
        self._recorder = event_recorder
        self._episodic = episodic
        self._approval = approval_gateway
        self._owner_id = owner_id
        self._property_id = property_id

        # Define transitions with conditions
        transitions = [
            Transition(
                from_state=RECEIVED,
                to_state=CHECK_POLICY,
                condition=lambda ctx: self._has_checkout_request(),
            ),
            Transition(
                from_state=CHECK_POLICY,
                to_state=NEGOTIATE,
                condition=lambda ctx: self._policy_allows_extension(),
            ),
            Transition(
                from_state=CHECK_POLICY,
                to_state=DONE,
                condition=lambda ctx: not self._policy_allows_extension(),
            ),
            Transition(
                from_state=NEGOTIATE,
                to_state=CONFIRM,
                condition=lambda ctx: self._fee_agreed(),
            ),
            Transition(
                from_state=NEGOTIATE,
                to_state=DONE,
                condition=lambda ctx: self._fee_declined(),
            ),
            Transition(
                from_state=CONFIRM,
                to_state=DONE,
            ),
        ]

        self.state_machine = StateMachine(
            states=STATES,
            transitions=transitions,
            initial_state=RECEIVED,
        )

    def _has_checkout_request(self) -> bool:
        """Check if we have the requested checkout time."""
        return self.slot_manager.get_value("john_checkout_time") is not None

    def _policy_allows_extension(self) -> bool:
        """Check if the requested extension is within policy limits."""
        checkout_time = self.slot_manager.get_value("john_checkout_time")
        george_checkin = self.slot_manager.get_value("george_checkin_time")

        if not checkout_time:
            return False

        # Parse hour from time string (simplified: expects "2:00 PM" or "14:00")
        requested_hour = self._parse_hour(checkout_time)
        if requested_hour is None or requested_hour > MAX_EXTENSION_HOUR:
            return False

        # Check against next guest check-in
        if george_checkin:
            next_guest_hour = self._parse_hour(george_checkin)
            if next_guest_hour is not None:
                # Need at least 2 hours between checkout and next check-in
                if requested_hour > next_guest_hour - 2:
                    return False

        return True

    def _fee_agreed(self) -> bool:
        """Check if the guest has agreed to the fee."""
        return self.slot_manager.get_value("fee_agreed") is True

    def _fee_declined(self) -> bool:
        """Check if the guest has explicitly declined the fee."""
        return self.slot_manager.get_value("fee_agreed") is False

    def _calculate_fee(self) -> float:
        """Calculate the late checkout fee based on extension hours."""
        checkout_time = self.slot_manager.get_value("john_checkout_time")
        if not checkout_time:
            return 0.0

        requested_hour = self._parse_hour(checkout_time)
        if requested_hour is None:
            return 0.0

        standard_hour = 11  # 11 AM standard checkout
        extension = requested_hour - standard_hour

        for (low, high), fee in FEE_SCHEDULE.items():
            if low <= extension <= high:
                return fee

        return 0.0

    @staticmethod
    def _parse_hour(time_str: str) -> int | None:
        """Parse hour from common time formats.

        Handles: "2:00 PM", "14:00", "2 PM", "2pm", etc.
        """
        import re

        time_str = time_str.strip().upper()

        # Try 24-hour format (14:00)
        match_24 = re.match(r"(\d{1,2}):?\d{0,2}$", time_str)
        if match_24 and "AM" not in time_str and "PM" not in time_str:
            hour = int(match_24.group(1))
            if 0 <= hour <= 23:
                return hour

        # Try 12-hour format (2:00 PM, 2 PM, 2PM)
        match_12 = re.match(r"(\d{1,2})(?::?\d{0,2})?\s*(AM|PM)", time_str)
        if match_12:
            hour = int(match_12.group(1))
            period = match_12.group(2)
            if period == "PM" and hour != 12:
                hour += 12
            elif period == "AM" and hour == 12:
                hour = 0
            return hour

        return None

    async def run(self) -> AsyncIterator[AGUIEvent]:
        """Execute the late checkout flow, yielding AG-UI events.

        Yields:
            AGUIEvent objects for each state transition, slot update,
            and text message produced during the flow.
        """
        flow_name = "late_checkout"

        # Emit flow started
        yield self.emitter.flow_started(flow_name, self.state_machine.current_state)

        if self._episodic:
            await self._episodic.add_episode(
                event="late_checkout_flow_started",
                content="Late checkout flow initiated",
                metadata={"state": RECEIVED, "session_id": self.session_id},
            )

        # --- RECEIVED state ---
        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "Late checkout request received. Let me check the details."
        )
        yield self.emitter.text_message_end()

        # Check if we have the necessary info to proceed
        if not self._has_checkout_request():
            yield self.emitter.slot_requested(
                "john_checkout_time",
                "What time would the guest like to check out?"
            )
            return

        # Transition to CHECK_POLICY
        old_state = self.state_machine.current_state
        new_state = self.state_machine.transition()
        if new_state != old_state:
            yield self.emitter.flow_state_changed(flow_name, old_state, new_state)

        # --- CHECK_POLICY state ---
        checkout_time = self.slot_manager.get_value("john_checkout_time")
        george_checkin = self.slot_manager.get_value("george_checkin_time", "4:00 PM")
        guest_name = self.slot_manager.get_value("guest_name", "the guest")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Checking late checkout policy for {guest_name}. "
            f"Requested checkout: {checkout_time}. "
            f"Next guest check-in: {george_checkin}."
        )
        yield self.emitter.text_message_end()

        if not self._policy_allows_extension():
            # Policy does not allow - transition to DONE
            if self._episodic:
                await self._episodic.add_episode(
                    event="policy_check_denied",
                    content=f"Late checkout to {checkout_time} denied by policy. Next guest: {george_checkin}",
                    metadata={"checkout_time": checkout_time, "next_checkin": george_checkin},
                )

            old_state = self.state_machine.current_state
            self.state_machine.transition(to_state=DONE)
            yield self.emitter.flow_state_changed(flow_name, old_state, DONE)

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Unfortunately, the requested late checkout to {checkout_time} "
                f"is not possible. The next guest arrives at {george_checkin} "
                "and we need at least 2 hours for cleaning. "
                "The maximum checkout extension is 4:00 PM."
            )
            yield self.emitter.text_message_end()

            self.slot_manager.set_slot("late_checkout_approved", False)
            yield self.emitter.slot_filled("late_checkout_approved", False)

            yield self.emitter.flow_completed(flow_name, {"approved": False})
            return

        # Policy allows extension
        if self._episodic:
            await self._episodic.add_episode(
                event="policy_check_approved",
                content=f"Late checkout to {checkout_time} approved by policy",
                metadata={"checkout_time": checkout_time, "next_checkin": george_checkin},
            )

        # Calculate fee and transition to NEGOTIATE
        fee = self._calculate_fee()
        self.slot_manager.set_slot("late_checkout_fee", fee)
        yield self.emitter.slot_filled("late_checkout_fee", fee)

        extension_hours = (self._parse_hour(checkout_time) or 11) - 11
        self.slot_manager.set_slot("checkout_extension_hours", extension_hours)
        yield self.emitter.slot_filled("checkout_extension_hours", extension_hours)

        old_state = self.state_machine.current_state
        new_state = self.state_machine.transition()
        if new_state != old_state:
            yield self.emitter.flow_state_changed(flow_name, old_state, new_state)

        # --- NEGOTIATE state ---
        if self._episodic:
            await self._episodic.add_episode(
                event="fee_calculated",
                content=f"Fee ${fee:.0f} for {extension_hours}h extension to {checkout_time}",
                metadata={"fee": fee, "extension_hours": extension_hours, "guest": guest_name},
            )

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Late checkout to {checkout_time} is available. "
            f"The fee for a {extension_hours}-hour extension is ${fee:.0f}. "
            f"Shall I confirm this with {guest_name}?"
        )
        yield self.emitter.text_message_end()

        # Wait for fee agreement (in practice, this would pause for user input)
        if not self._fee_agreed() and not self._fee_declined():
            yield self.emitter.slot_requested(
                "fee_agreed",
                f"Does {guest_name} agree to the ${fee:.0f} late checkout fee?"
            )
            return

        if self._fee_declined():
            if self._episodic:
                await self._episodic.add_episode(
                    event="fee_declined",
                    content=f"{guest_name} declined ${fee:.0f} late checkout fee",
                    metadata={"guest": guest_name, "fee": fee},
                )

            old_state = self.state_machine.current_state
            self.state_machine.transition(to_state=DONE)
            yield self.emitter.flow_state_changed(flow_name, old_state, DONE)

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"{guest_name} declined the late checkout fee. "
                "Standard checkout time of 11:00 AM applies."
            )
            yield self.emitter.text_message_end()

            self.slot_manager.set_slot("late_checkout_approved", False)
            yield self.emitter.slot_filled("late_checkout_approved", False)

            yield self.emitter.flow_completed(flow_name, {"approved": False})
            return

        # --- CONFIRM state ---
        # ── Human-in-the-Loop: Request owner approval before confirming ──
        if self._approval:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Requesting owner approval for late checkout: "
                f"{guest_name} → {checkout_time} (${fee:.0f} fee)..."
            )
            yield self.emitter.text_message_end()

            yield self.emitter.tool_call_start("request_owner_approval")

            approval_result = await self._approval.request_approval(
                action_type=ActionType.LATE_CHECKOUT,
                owner_id=self._owner_id,
                property_id=self._property_id,
                description=(
                    f"Guest {guest_name} requests late checkout at {checkout_time}. "
                    f"Fee: ${fee:.0f}. Next guest arrives at "
                    f"{self.slot_manager.get_value('george_checkin_time', 'TBD')}."
                ),
                proposed_action={
                    "checkout_time": checkout_time,
                    "fee": fee,
                    "extension_hours": extension_hours,
                },
                context={
                    "guest_name": guest_name,
                    "guest_rating": self.slot_manager.get_value("guest_rating"),
                },
                urgency=3,
            )

            yield self.emitter.tool_call_end(
                "request_owner_approval",
                {"status": approval_result.status.value, "message": approval_result.message},
            )

            if approval_result.status in (ApprovalStatus.DENIED,):
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    f"Owner DENIED the late checkout request. "
                    f"Standard checkout time applies."
                )
                yield self.emitter.text_message_end()

                self.slot_manager.set_slot("late_checkout_approved", False)
                yield self.emitter.slot_filled("late_checkout_approved", False)

                old_state = self.state_machine.current_state
                self.state_machine.transition(to_state=DONE)
                yield self.emitter.flow_state_changed(flow_name, old_state, DONE)
                yield self.emitter.flow_completed(flow_name, {"approved": False, "reason": "owner_denied"})
                return

            approval_label = "auto-approved" if approval_result.status == ApprovalStatus.AUTO_APPROVED else "approved"
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Owner {approval_label} the late checkout."
            )
            yield self.emitter.text_message_end()

        if self._episodic:
            await self._episodic.add_episode(
                event="fee_agreed",
                content=f"{guest_name} agreed to ${fee:.0f} late checkout fee",
                metadata={"guest": guest_name, "fee": fee, "checkout_time": checkout_time},
            )

        old_state = self.state_machine.current_state
        new_state = self.state_machine.transition()
        if new_state != old_state:
            yield self.emitter.flow_state_changed(flow_name, old_state, new_state)

        self.slot_manager.set_slot("late_checkout_approved", True)
        yield self.emitter.slot_filled("late_checkout_approved", True)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Late checkout confirmed for {guest_name}. "
            f"New checkout time: {checkout_time}. "
            f"Fee: ${fee:.0f}. "
            "The cleaning team has been notified of the adjusted schedule."
        )
        yield self.emitter.text_message_end()

        # Transition to DONE
        old_state = self.state_machine.current_state
        new_state = self.state_machine.transition()
        if new_state != old_state:
            yield self.emitter.flow_state_changed(flow_name, old_state, new_state)

        # ── Memory: Record late checkout confirmation ───────────────────
        if self._recorder:
            await self._recorder.record_incident_update(
                event="late_checkout_confirmed",
                details=f"Late checkout to {checkout_time} confirmed. Fee: ${fee:.0f}",
                guest_name=guest_name,
                extension_hours=extension_hours,
            )

        yield self.emitter.flow_completed(
            flow_name,
            {
                "approved": True,
                "checkout_time": checkout_time,
                "fee": fee,
                "extension_hours": extension_hours,
            },
        )

    @property
    def current_state(self) -> str:
        return self.state_machine.current_state

    @property
    def is_done(self) -> bool:
        return self.state_machine.is_terminal
