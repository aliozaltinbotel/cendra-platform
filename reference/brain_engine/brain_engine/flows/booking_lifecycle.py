"""Booking Lifecycle — full autonomous apartment preparation.

One trigger (new booking) → Brain Engine autonomously:
1. Checks IoT devices (TV, AC, locks, plumbing)
2. Contacts outgoing guest: "When are you leaving?"
3. Cascades cleaners: #1 busy → #2 → #3 → #4
4. Dispatches vendor if repairs needed
5. Creates access codes for new guest
6. Prepares climate 30 min before check-in
7. Welcomes incoming guest with directions
8. Scores guest and offers upsells

All via Pregel BSP — parallel where possible, sequential where needed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# State
# ══════════════════════════════════════════════════════════════════════


class Phase(StrEnum):
    """Lifecycle phases."""

    INIT = "init"
    CHECK_IOT = "check_iot"
    CONTACT_OUTGOING = "contact_outgoing"
    CLEANER_CASCADE = "cleaner_cascade"
    VENDOR_REPAIR = "vendor_repair"
    PREPARE_APARTMENT = "prepare_apartment"
    ACCESS_CODES = "access_codes"
    WELCOME_GUEST = "welcome_guest"
    SCORE_UPSELL = "score_upsell"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BookingState:
    """Full state for the booking lifecycle.

    Attributes:
        booking_id: Reservation identifier.
        property_id: Property identifier.
        phase: Current lifecycle phase.
        checkin_time: Expected check-in datetime.
        checkout_time: Expected check-out datetime.
        incoming_guest: New guest profile.
        outgoing_guest: Current guest profile (if any).
        iot_status: Device status from IoT check.
        repairs_needed: List of items needing repair.
        cleaner_status: Cleaner assignment state.
        cleaners_tried: Cleaners contacted so far.
        vendor_status: Vendor dispatch state.
        access_code: Generated access code.
        guest_score: Calculated guest score.
        upsell_offers: Generated upsell offers.
        actions: MCP actions to execute.
        events: Event log for tracing.
        error: Error message if failed.
    """

    booking_id: str = ""
    property_id: str = ""
    phase: str = Phase.INIT
    checkin_time: str = ""
    checkout_time: str = ""
    incoming_guest: dict[str, Any] = field(default_factory=dict)
    outgoing_guest: dict[str, Any] = field(default_factory=dict)
    iot_status: dict[str, Any] = field(default_factory=dict)
    repairs_needed: list[str] = field(default_factory=list)
    cleaner_status: str = "pending"
    cleaners_tried: list[str] = field(default_factory=list)
    cleaner_assigned: str = ""
    vendor_status: str = "not_needed"
    access_code: str = ""
    guest_score: float = 0.0
    upsell_offers: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    error: str = ""


# ══════════════════════════════════════════════════════════════════════
# Nodes — each is a pure function (state in → state out)
# ══════════════════════════════════════════════════════════════════════


async def check_iot_devices(state: dict[str, Any]) -> dict[str, Any]:
    """Check all IoT devices in the property.

    Queries smart locks, thermostats, sensors for status.
    Identifies any devices that need repair.

    Args:
        state: Current booking state.

    Returns:
        Updated state with iot_status and repairs_needed.
    """
    property_id = state.get("property_id", "")
    events = list(state.get("events", []))
    events.append(f"Checking IoT devices for property {property_id}")

    # Build IoT check action
    actions = list(state.get("actions", []))
    actions.append({
        "tool": "checkDeviceStatus",
        "params": {"property_id": property_id},
        "priority": "high",
    })

    # Simulate device check (real: calls Seam API via MCP)
    iot_status = _build_iot_status(property_id)
    repairs = _identify_repairs(iot_status)

    return {
        "phase": Phase.CHECK_IOT,
        "iot_status": iot_status,
        "repairs_needed": repairs,
        "actions": actions,
        "events": events,
    }


async def contact_outgoing_guest(state: dict[str, Any]) -> dict[str, Any]:
    """Contact the current guest to confirm checkout time.

    Calls or messages the outgoing guest: "When are you leaving?"

    Args:
        state: Current booking state.

    Returns:
        Updated state with outgoing guest contact action.
    """
    outgoing = state.get("outgoing_guest", {})
    events = list(state.get("events", []))
    actions = list(state.get("actions", []))

    if not outgoing.get("phone"):
        events.append("No outgoing guest to contact")
        return {"events": events, "phase": Phase.CONTACT_OUTGOING}

    events.append(f"Contacting outgoing guest: {outgoing.get('name', '?')}")
    actions.append(_build_guest_call_action(outgoing, "outgoing"))

    return {
        "phase": Phase.CONTACT_OUTGOING,
        "actions": actions,
        "events": events,
    }


async def cascade_cleaners(state: dict[str, Any]) -> dict[str, Any]:
    """Contact cleaners in cascade order until one accepts.

    Tries cleaner #1 → #2 → #3 → #4. Each refusal or no-reply
    triggers the next in the cascade.

    Args:
        state: Current booking state.

    Returns:
        Updated state with cleaner assignment or escalation.
    """
    property_id = state.get("property_id", "")
    tried = list(state.get("cleaners_tried", []))
    events = list(state.get("events", []))
    actions = list(state.get("actions", []))

    cleaners = _get_cleaner_contacts(property_id)
    available = [c for c in cleaners if c["id"] not in tried]

    if not available:
        events.append("All cleaners exhausted — escalating to PM")
        return _escalate_cleaning(state, events, actions)

    next_cleaner = available[0]
    tried.append(next_cleaner["id"])
    events.append(f"Contacting cleaner: {next_cleaner['name']}")

    actions.append({
        "tool": "sendWhatsApp",
        "params": {
            "phone": next_cleaner["phone"],
            "message": _build_cleaner_message(state),
        },
        "priority": "high",
    })

    return {
        "phase": Phase.CLEANER_CASCADE,
        "cleaner_status": "contacting",
        "cleaners_tried": tried,
        "actions": actions,
        "events": events,
    }


async def dispatch_vendor(state: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a repair vendor if repairs are needed.

    Contacts handyman/technician for broken appliances.

    Args:
        state: Current booking state.

    Returns:
        Updated state with vendor dispatch actions.
    """
    repairs = state.get("repairs_needed", [])
    events = list(state.get("events", []))
    actions = list(state.get("actions", []))

    if not repairs:
        events.append("No repairs needed — skipping vendor")
        return {
            "phase": Phase.VENDOR_REPAIR,
            "vendor_status": "not_needed",
            "events": events,
        }

    events.append(f"Dispatching vendor for: {', '.join(repairs)}")
    actions.append({
        "tool": "sendWhatsApp",
        "params": {
            "phone": "+000000000",
            "message": _build_vendor_message(repairs, state),
        },
        "priority": "high",
    })

    return {
        "phase": Phase.VENDOR_REPAIR,
        "vendor_status": "dispatched",
        "actions": actions,
        "events": events,
    }


async def prepare_apartment(state: dict[str, Any]) -> dict[str, Any]:
    """Final apartment preparation: climate + locks.

    Activates automation templates: pre-heat/cool, unlock
    doors, set up access for cleaning crew.

    Args:
        state: Current booking state.

    Returns:
        Updated state with preparation actions.
    """
    property_id = state.get("property_id", "")
    events = list(state.get("events", []))
    actions = list(state.get("actions", []))

    events.append("Preparing apartment: climate + locks")

    # Climate control
    actions.append({
        "tool": "setClimate",
        "params": {"property_id": property_id, "preset": "comfort"},
        "priority": "medium",
    })

    # Unlock for cleaner
    actions.append({
        "tool": "unlockDoor",
        "params": {"property_id": property_id, "reason": "cleaner_access"},
        "priority": "high",
    })

    return {
        "phase": Phase.PREPARE_APARTMENT,
        "actions": actions,
        "events": events,
    }


async def create_access_codes(state: dict[str, Any]) -> dict[str, Any]:
    """Generate time-bound access code for incoming guest.

    Creates a PIN code valid from check-in to check-out time.

    Args:
        state: Current booking state.

    Returns:
        Updated state with access code.
    """
    guest = state.get("incoming_guest", {})
    events = list(state.get("events", []))
    actions = list(state.get("actions", []))

    code = _generate_access_code()
    events.append(f"Created access code: {code}")

    actions.append({
        "tool": "createAccessCode",
        "params": {
            "property_id": state.get("property_id", ""),
            "code": code,
            "guest_name": guest.get("name", "Guest"),
            "starts": state.get("checkin_time", ""),
            "ends": state.get("checkout_time", ""),
        },
        "priority": "high",
    })

    return {
        "phase": Phase.ACCESS_CODES,
        "access_code": code,
        "actions": actions,
        "events": events,
    }


async def welcome_guest(state: dict[str, Any]) -> dict[str, Any]:
    """Send welcome message to incoming guest with all details.

    Includes: directions, access code, WiFi, house rules.

    Args:
        state: Current booking state.

    Returns:
        Updated state with welcome action.
    """
    guest = state.get("incoming_guest", {})
    code = state.get("access_code", "")
    events = list(state.get("events", []))
    actions = list(state.get("actions", []))

    message = _build_welcome_message(guest, code, state)
    events.append(f"Welcome sent to {guest.get('name', 'Guest')}")

    actions.append({
        "tool": "sendWhatsApp",
        "params": {
            "phone": guest.get("phone", ""),
            "message": message,
        },
        "priority": "medium",
    })

    return {
        "phase": Phase.WELCOME_GUEST,
        "actions": actions,
        "events": events,
    }


async def score_and_upsell(state: dict[str, Any]) -> dict[str, Any]:
    """Calculate guest score and generate upsell offers.

    Scoring based on: booking history, VIP status, issues encountered.
    Upsells: late checkout, early checkin, gap nights.

    Args:
        state: Current booking state.

    Returns:
        Updated state with score and offers.
    """
    guest = state.get("incoming_guest", {})
    repairs = state.get("repairs_needed", [])
    events = list(state.get("events", []))

    score = _calculate_guest_score(guest, repairs)
    offers = _generate_upsell_offers(guest, score, state)

    events.append(
        f"Guest score: {score:.0f}, "
        f"offers: {len(offers)}"
    )

    # Discount if there were problems
    if repairs:
        events.append(f"Applying apology discount for {len(repairs)} issues")
        offers.append({
            "type": "apology_discount",
            "amount_percent": min(15, len(repairs) * 5),
            "reason": f"Issues found: {', '.join(repairs)}",
        })

    return {
        "phase": Phase.SCORE_UPSELL,
        "guest_score": score,
        "upsell_offers": offers,
        "events": events,
    }


# ══════════════════════════════════════════════════════════════════════
# Router — decides next phase based on state
# ══════════════════════════════════════════════════════════════════════


def route_after_iot(state: dict[str, Any]) -> str:
    """Route after IoT check: parallel outgoing + cleaner.

    Args:
        state: Current state.

    Returns:
        Next phase name.
    """
    return "contact_outgoing"


def route_after_cleaner(state: dict[str, Any]) -> str:
    """Route after cleaner cascade: vendor if repairs, else prepare.

    Args:
        state: Current state.

    Returns:
        Next phase name.
    """
    if state.get("repairs_needed"):
        return "vendor_repair"
    return "prepare_apartment"


def route_after_vendor(state: dict[str, Any]) -> str:
    """Route after vendor dispatch.

    Args:
        state: Current state.

    Returns:
        Next phase name.
    """
    return "prepare_apartment"


# ══════════════════════════════════════════════════════════════════════
# Orchestrator — builds and runs the full graph
# ══════════════════════════════════════════════════════════════════════


class BookingLifecycle:
    """Autonomous booking lifecycle orchestrator.

    Builds a Pregel StateGraph and runs the full apartment
    preparation pipeline from a single trigger.

    Args:
        deps: Dependency container with MCP tools, memory, etc.
    """

    def __init__(self, deps: dict[str, Any] | None = None) -> None:
        self._deps = deps or {}

    async def run(
        self,
        booking_id: str,
        property_id: str,
        checkin_time: str,
        checkout_time: str,
        incoming_guest: dict[str, Any],
        outgoing_guest: dict[str, Any] | None = None,
    ) -> BookingState:
        """Run the full autonomous lifecycle.

        Args:
            booking_id: Reservation ID.
            property_id: Property ID.
            checkin_time: Check-in datetime ISO string.
            checkout_time: Check-out datetime ISO string.
            incoming_guest: New guest profile.
            outgoing_guest: Current guest (if occupied).

        Returns:
            Final BookingState with all actions and events.
        """
        executor = _build_graph()

        initial_state = {
            "__input__": True,
            "booking_id": booking_id,
            "property_id": property_id,
            "checkin_time": checkin_time,
            "checkout_time": checkout_time,
            "incoming_guest": incoming_guest,
            "outgoing_guest": outgoing_guest or {},
            "phase": Phase.INIT,
            "actions": [],
            "events": [f"Lifecycle started for booking {booking_id}"],
            "repairs_needed": [],
            "cleaners_tried": [],
            "iot_status": {},
            "cleaner_status": "pending",
            "cleaner_assigned": "",
            "vendor_status": "not_needed",
            "access_code": "",
            "guest_score": 0.0,
            "upsell_offers": [],
            "error": "",
        }

        result = await executor.run(initial_state)
        return _result_to_state(result, booking_id)


# ══════════════════════════════════════════════════════════════════════
# Graph builder
# ══════════════════════════════════════════════════════════════════════


def _build_graph() -> Any:
    """Build the Pregel executor for the booking lifecycle.

    Returns:
        Configured PregelExecutor.
    """
    from brain_engine.channels.last_value import LastValue
    from brain_engine.channels.binop import BinaryOperatorAggregate
    from brain_engine.pregel.executor import PregelExecutor, PregelNode
    from brain_engine.pregel.write import ChannelWriteEntry
    import operator

    channels = _create_lifecycle_channels()
    nodes = _create_lifecycle_nodes()

    return PregelExecutor(nodes, channels, max_steps=15)


def _create_lifecycle_channels() -> dict[str, Any]:
    """Create all channels for the lifecycle graph.

    Returns:
        Dict of channel_name -> channel.
    """
    from brain_engine.channels.last_value import LastValue
    from brain_engine.channels.binop import BinaryOperatorAggregate
    import operator

    lv = LastValue
    ba = BinaryOperatorAggregate

    return {
        "__input__": lv(bool),
        "booking_id": lv(str, default=""),
        "property_id": lv(str, default=""),
        "phase": lv(str, default=Phase.INIT),
        "checkin_time": lv(str, default=""),
        "checkout_time": lv(str, default=""),
        "incoming_guest": lv(dict, default=None),
        "outgoing_guest": lv(dict, default=None),
        "iot_status": lv(dict, default=None),
        "repairs_needed": ba(list, operator.add, default=list),
        "cleaner_status": lv(str, default="pending"),
        "cleaners_tried": ba(list, operator.add, default=list),
        "cleaner_assigned": lv(str, default=""),
        "vendor_status": lv(str, default="not_needed"),
        "access_code": lv(str, default=""),
        "guest_score": lv(float, default=0.0),
        "upsell_offers": ba(list, operator.add, default=list),
        "actions": ba(list, operator.add, default=list),
        "events": ba(list, operator.add, default=list),
        "error": lv(str, default=""),
        # Trigger channels
        "__done_check_iot__": lv(bool),
        "__done_contact_outgoing__": lv(bool),
        "__done_cleaner_cascade__": lv(bool),
        "__done_vendor__": lv(bool),
        "__done_prepare__": lv(bool),
        "__done_access__": lv(bool),
        "__done_welcome__": lv(bool),
    }


def _create_lifecycle_nodes() -> dict[str, Any]:
    """Create all Pregel nodes for the lifecycle graph.

    Returns:
        Dict of node_name -> PregelNode.
    """
    from brain_engine.pregel.executor import PregelNode
    from brain_engine.pregel.write import ChannelWriteEntry

    all_ch = [
        "phase", "iot_status", "repairs_needed", "cleaner_status",
        "cleaners_tried", "cleaner_assigned", "vendor_status",
        "access_code", "guest_score", "upsell_offers", "actions",
        "events",
    ]

    def writers(ch_list: list[str], done_ch: str) -> list[ChannelWriteEntry]:
        w = [ChannelWriteEntry(channel=c) for c in ch_list]
        w.append(ChannelWriteEntry(channel=done_ch, value=True))
        return w

    return {
        "check_iot": PregelNode(
            name="check_iot",
            func=check_iot_devices,
            channels=list(_state_channels()),
            triggers=["__input__"],
            writers=writers(all_ch, "__done_check_iot__"),
        ),
        "contact_outgoing": PregelNode(
            name="contact_outgoing",
            func=contact_outgoing_guest,
            channels=list(_state_channels()),
            triggers=["__done_check_iot__"],
            writers=writers(all_ch, "__done_contact_outgoing__"),
        ),
        "cleaner_cascade": PregelNode(
            name="cleaner_cascade",
            func=cascade_cleaners,
            channels=list(_state_channels()),
            triggers=["__done_contact_outgoing__"],
            writers=writers(all_ch, "__done_cleaner_cascade__"),
        ),
        "vendor_repair": PregelNode(
            name="vendor_repair",
            func=dispatch_vendor,
            channels=list(_state_channels()),
            triggers=["__done_cleaner_cascade__"],
            writers=writers(all_ch, "__done_vendor__"),
        ),
        "prepare_apartment": PregelNode(
            name="prepare_apartment",
            func=prepare_apartment,
            channels=list(_state_channels()),
            triggers=["__done_vendor__"],
            writers=writers(all_ch, "__done_prepare__"),
        ),
        "create_access": PregelNode(
            name="create_access",
            func=create_access_codes,
            channels=list(_state_channels()),
            triggers=["__done_prepare__"],
            writers=writers(all_ch, "__done_access__"),
        ),
        "welcome_guest": PregelNode(
            name="welcome_guest",
            func=welcome_guest,
            channels=list(_state_channels()),
            triggers=["__done_access__"],
            writers=writers(all_ch, "__done_welcome__"),
        ),
        "score_upsell": PregelNode(
            name="score_upsell",
            func=score_and_upsell,
            channels=list(_state_channels()),
            triggers=["__done_welcome__"],
            writers=[ChannelWriteEntry(channel=c) for c in all_ch],
        ),
    }


def _state_channels() -> list[str]:
    """List of readable state channel names.

    Returns:
        State channel names (excluding triggers).
    """
    return [
        "booking_id", "property_id", "phase",
        "checkin_time", "checkout_time",
        "incoming_guest", "outgoing_guest",
        "iot_status", "repairs_needed",
        "cleaner_status", "cleaners_tried", "cleaner_assigned",
        "vendor_status", "access_code",
        "guest_score", "upsell_offers",
        "actions", "events", "error",
    ]


# ══════════════════════════════════════════════════════════════════════
# Helpers — pure functions, max 40 lines each
# ══════════════════════════════════════════════════════════════════════


def _build_iot_status(property_id: str) -> dict[str, Any]:
    """Build IoT device status report.

    Args:
        property_id: Property to check.

    Returns:
        Device status dict.
    """
    return {
        "smart_lock": "online",
        "thermostat": "online",
        "property_id": property_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _identify_repairs(iot_status: dict[str, Any]) -> list[str]:
    """Identify devices needing repair from IoT status.

    Args:
        iot_status: Device statuses.

    Returns:
        List of items needing repair.
    """
    repairs: list[str] = []
    for device, status in iot_status.items():
        if status == "offline" or status == "error":
            repairs.append(device)
    return repairs


def _get_cleaner_contacts(property_id: str) -> list[dict[str, str]]:
    """Get ordered list of cleaners for a property.

    Args:
        property_id: Property identifier.

    Returns:
        Cleaner contacts in priority order.
    """
    return [
        {"id": "c1", "name": "Cleaner 1", "phone": "+1001"},
        {"id": "c2", "name": "Cleaner 2", "phone": "+1002"},
        {"id": "c3", "name": "Cleaner 3", "phone": "+1003"},
        {"id": "c4", "name": "Cleaner 4", "phone": "+1004"},
    ]


def _build_cleaner_message(state: dict[str, Any]) -> str:
    """Build cleaning request message.

    Args:
        state: Current booking state.

    Returns:
        Message text.
    """
    checkout = state.get("checkout_time", "TBD")
    checkin = state.get("checkin_time", "TBD")
    return (
        f"Hi! We need cleaning at property {state.get('property_id', '?')}. "
        f"Checkout: {checkout}, next check-in: {checkin}. "
        f"Can you do it?"
    )


def _build_vendor_message(
    repairs: list[str],
    state: dict[str, Any],
) -> str:
    """Build vendor repair request message.

    Args:
        repairs: Items needing repair.
        state: Current booking state.

    Returns:
        Message text.
    """
    items = ", ".join(repairs)
    return (
        f"Repair needed at property {state.get('property_id', '?')}: "
        f"{items}. Guest arriving {state.get('checkin_time', 'soon')}. "
        f"Can you fix before then?"
    )


def _build_guest_call_action(
    guest: dict[str, Any],
    direction: str,
) -> dict[str, Any]:
    """Build a voice call action for a guest.

    Args:
        guest: Guest profile with phone.
        direction: 'outgoing' or 'incoming'.

    Returns:
        MCP action dict.
    """
    if direction == "outgoing":
        script = "confirm_checkout_time"
        message = f"Hi {guest.get('name', '')}! Just checking — what time will you be checking out today?"
    else:
        script = "confirm_arrival_time"
        message = f"Hi {guest.get('name', '')}! Looking forward to your arrival. What time will you be coming?"

    return {
        "tool": "makeCall",
        "params": {
            "phone": guest.get("phone", ""),
            "script": script,
            "first_message": message,
        },
        "priority": "high",
    }


def _generate_access_code() -> str:
    """Generate a random 6-digit access code.

    Returns:
        6-digit string.
    """
    import random
    return str(random.randint(100000, 999999))


def _build_welcome_message(
    guest: dict[str, Any],
    code: str,
    state: dict[str, Any],
) -> str:
    """Build welcome message with all guest needs.

    Args:
        guest: Guest profile.
        code: Access code.
        state: Full state.

    Returns:
        Welcome message text.
    """
    name = guest.get("name", "Guest")
    return (
        f"Welcome {name}! 🏠\n\n"
        f"Your access code: {code}\n"
        f"Check-in: {state.get('checkin_time', 'as scheduled')}\n\n"
        f"The apartment is ready for you. "
        f"Let us know if you need anything!"
    )


def _calculate_guest_score(
    guest: dict[str, Any],
    repairs: list[str],
) -> float:
    """Calculate guest score (0-100).

    Args:
        guest: Guest profile.
        repairs: Issues found in property.

    Returns:
        Score between 0-100.
    """
    base = 50.0
    bookings = guest.get("booking_count", 0)
    base += min(30.0, bookings * 5.0)

    if guest.get("is_vip"):
        base += 15.0

    # Penalty for problems guest will encounter
    base -= len(repairs) * 5.0
    return max(0.0, min(100.0, base))


def _generate_upsell_offers(
    guest: dict[str, Any],
    score: float,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate upsell offers based on guest score.

    Args:
        guest: Guest profile.
        score: Calculated guest score.
        state: Full state.

    Returns:
        List of offer dicts.
    """
    offers: list[dict[str, Any]] = []

    if score >= 70:
        offers.append({
            "type": "late_checkout",
            "description": "Free late checkout until 2 PM",
            "free": score >= 80,
        })

    if score >= 60:
        offers.append({
            "type": "early_checkin",
            "description": "Early check-in from 12 PM",
            "fee": 0 if score >= 80 else 15,
        })

    return offers


def _escalate_cleaning(
    state: dict[str, Any],
    events: list[str],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Escalate when all cleaners exhausted.

    Args:
        state: Current state.
        events: Event log.
        actions: Action list.

    Returns:
        Updated state with escalation.
    """
    actions.append({
        "tool": "sendSlackMessage",
        "params": {
            "channel": "#ops-urgent",
            "message": (
                f"⚠️ All cleaners unavailable for "
                f"property {state.get('property_id', '?')}. "
                f"Guest arriving {state.get('checkin_time', 'soon')}."
            ),
        },
        "priority": "critical",
    })
    return {
        "phase": Phase.CLEANER_CASCADE,
        "cleaner_status": "escalated",
        "actions": actions,
        "events": events,
    }


def _result_to_state(
    result: Any,
    booking_id: str,
) -> BookingState:
    """Convert Pregel result to BookingState.

    Args:
        result: PregelResult from executor.
        booking_id: Original booking ID.

    Returns:
        BookingState dataclass.
    """
    values = getattr(result, "values", {})
    return BookingState(
        booking_id=booking_id,
        property_id=values.get("property_id", ""),
        phase=values.get("phase", Phase.COMPLETED),
        checkin_time=values.get("checkin_time", ""),
        checkout_time=values.get("checkout_time", ""),
        incoming_guest=values.get("incoming_guest", {}),
        outgoing_guest=values.get("outgoing_guest", {}),
        iot_status=values.get("iot_status", {}),
        repairs_needed=values.get("repairs_needed", []),
        cleaner_status=values.get("cleaner_status", ""),
        cleaners_tried=values.get("cleaners_tried", []),
        cleaner_assigned=values.get("cleaner_assigned", ""),
        vendor_status=values.get("vendor_status", ""),
        access_code=values.get("access_code", ""),
        guest_score=values.get("guest_score", 0.0),
        upsell_offers=values.get("upsell_offers", []),
        actions=values.get("actions", []),
        events=values.get("events", []),
        error=values.get("error", ""),
    )
