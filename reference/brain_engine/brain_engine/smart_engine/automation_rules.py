"""Automation Rules Engine — event-driven property automations.

Implements 9 automation templates from Cendra platform.
Brain Engine replaces static rules with dynamic, learnable procedures.
Each template maps a trigger event to a sequence of MCP tool actions.

Templates:
    1. Guest Access Code   — booking_confirmed → create time-bound access code
    2. Check-In Prep       — 60min before check_in → climate + unlock
    3. Pre-Heat/Cool       — 30min before check_in → HVAC on
    4. Check-Out Cleanup   — 10min after check_out → lock + revoke + away preset
    5. HVAC Off (Vacant)   — 30min after check_out → HVAC off
    6. Cancel → Revoke     — booking_cancelled → revoke all access codes
    7. Stay Extension       — booking_modified → revoke old + create new codes
    8. Vacant Door Alert   — lock.unlocked during vacancy → notify PM
    9. Between Bookings    — check_out (no next booking) → away climate preset

Architecture:
    Event (webhook) → AutomationEngine.process(event) → list[MCPAction]
    Brain Engine learns which automations work via SkillEvolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AutomationEvent:
    """An event that may trigger automations.

    Attributes:
        event_type: Event identifier (booking_confirmed, check_in, etc.).
        property_id: Property context.
        reservation_id: Booking identifier (if applicable).
        event_data: Additional event-specific data.
        minutes_offset: Minutes relative to event (negative=before, positive=after).
    """

    event_type: str
    property_id: str = ""
    reservation_id: str = ""
    event_data: dict[str, Any] = field(default_factory=dict)
    minutes_offset: int = 0


@dataclass(frozen=True, slots=True)
class AutomationAction:
    """A single action to execute in response to a trigger.

    Attributes:
        tool: MCP tool name.
        params: Tool parameters.
        description: Human-readable description.
        delay_minutes: Delay before execution (0=immediate).
    """

    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    delay_minutes: int = 0


@dataclass(slots=True)
class AutomationResult:
    """Result of processing an event through automation rules.

    Attributes:
        event_type: Trigger event type.
        property_id: Property context.
        matched_rules: Names of rules that matched.
        actions: MCP actions to execute.
        skipped_rules: Rules that matched but were skipped (disabled, etc.).
    """

    event_type: str
    property_id: str = ""
    matched_rules: list[str] = field(default_factory=list)
    actions: list[AutomationAction] = field(default_factory=list)
    skipped_rules: list[str] = field(default_factory=list)

    def to_mcp_actions(self) -> list[dict[str, Any]]:
        """Convert to MCP action dicts for API response."""
        return [
            {"tool": a.tool, "params": a.params, "delay_minutes": a.delay_minutes}
            for a in self.actions
        ]


# ── Rule definitions ─────────────────────────────────────────────── #


def _rule_guest_access_code(event: AutomationEvent) -> list[AutomationAction]:
    """Template 1: Create time-bound access code on booking confirmation.

    Args:
        event: booking_confirmed event.

    Returns:
        Actions to create access code.
    """
    return [AutomationAction(
        tool="createAccessCode",
        params={
            "propertyId": event.property_id,
            "name": f"Guest {event.reservation_id}",
            "startsAt": event.event_data.get("checkin_datetime", ""),
            "endsAt": event.event_data.get("checkout_datetime", ""),
        },
        description="Create time-bound access code for guest",
    )]


def _rule_checkin_prep(event: AutomationEvent) -> list[AutomationAction]:
    """Template 2: Prepare property 60min before check-in.

    Args:
        event: check_in event with minutes_offset <= -60.

    Returns:
        Actions to set climate and unlock doors.
    """
    return [
        AutomationAction(
            tool="setClimatePreset",
            params={"propertyId": event.property_id, "preset": "comfort"},
            description="Set climate to comfort preset",
        ),
        AutomationAction(
            tool="unlockDoor",
            params={"propertyId": event.property_id},
            description="Unlock main door for guest arrival",
        ),
    ]


def _rule_preheat_cool(event: AutomationEvent) -> list[AutomationAction]:
    """Template 3: Start HVAC 30min before check-in.

    Args:
        event: check_in event with minutes_offset <= -30.

    Returns:
        Action to turn on HVAC.
    """
    return [AutomationAction(
        tool="setClimatePreset",
        params={"propertyId": event.property_id, "preset": "comfort"},
        description="Pre-heat/cool property before guest arrival",
    )]


def _rule_checkout_cleanup(event: AutomationEvent) -> list[AutomationAction]:
    """Template 4: Secure property 10min after checkout.

    Args:
        event: check_out event with minutes_offset >= 10.

    Returns:
        Actions to lock, revoke codes, set away preset.
    """
    return [
        AutomationAction(
            tool="lockDoor",
            params={"propertyId": event.property_id},
            description="Lock all doors",
        ),
        AutomationAction(
            tool="revokeAccessCodes",
            params={
                "propertyId": event.property_id,
                "reservationId": event.reservation_id,
            },
            description="Revoke guest access codes",
        ),
        AutomationAction(
            tool="setClimatePreset",
            params={"propertyId": event.property_id, "preset": "away"},
            description="Set climate to away preset",
        ),
    ]


def _rule_hvac_off(event: AutomationEvent) -> list[AutomationAction]:
    """Template 5: Turn off HVAC 30min after checkout.

    Args:
        event: check_out event with minutes_offset >= 30.

    Returns:
        Action to turn off HVAC.
    """
    return [AutomationAction(
        tool="setClimatePreset",
        params={"propertyId": event.property_id, "preset": "off"},
        description="Turn off HVAC (vacant property)",
    )]


def _rule_cancel_revoke(event: AutomationEvent) -> list[AutomationAction]:
    """Template 6: Revoke all codes on booking cancellation.

    Args:
        event: booking_cancelled event.

    Returns:
        Action to revoke access codes.
    """
    return [AutomationAction(
        tool="revokeAccessCodes",
        params={
            "propertyId": event.property_id,
            "reservationId": event.reservation_id,
        },
        description="Revoke all access codes for cancelled booking",
    )]


def _rule_stay_extension(event: AutomationEvent) -> list[AutomationAction]:
    """Template 7: Update access codes on booking modification.

    Args:
        event: booking_modified event.

    Returns:
        Actions to revoke old and create new codes.
    """
    return [
        AutomationAction(
            tool="revokeAccessCodes",
            params={
                "propertyId": event.property_id,
                "reservationId": event.reservation_id,
            },
            description="Revoke old access codes",
        ),
        AutomationAction(
            tool="createAccessCode",
            params={
                "propertyId": event.property_id,
                "name": f"Extended {event.reservation_id}",
                "startsAt": event.event_data.get("new_checkin_datetime", ""),
                "endsAt": event.event_data.get("new_checkout_datetime", ""),
            },
            description="Create new access codes for extended stay",
        ),
    ]


def _rule_vacant_door_alert(event: AutomationEvent) -> list[AutomationAction]:
    """Template 8: Alert PM when door unlocked during vacancy.

    Args:
        event: lock.unlocked event during vacant period.

    Returns:
        Action to notify PM.
    """
    return [AutomationAction(
        tool="sendSlackMessage",
        params={
            "channel": "ops-alerts",
            "message": (
                f"⚠️ Door unlocked at property {event.property_id} "
                f"during vacant period. Please investigate."
            ),
        },
        description="Alert PM about door unlock during vacancy",
    )]


def _rule_between_bookings_away(
    event: AutomationEvent,
) -> list[AutomationAction]:
    """Template 9: Set away climate when no next booking.

    Args:
        event: check_out event with no next booking.

    Returns:
        Action to set away preset.
    """
    return [AutomationAction(
        tool="setClimatePreset",
        params={"propertyId": event.property_id, "preset": "away"},
        description="Set away climate preset between bookings",
    )]


# ── Rule registry ────────────────────────────────────────────────── #

_AUTOMATION_RULES: list[dict[str, Any]] = [
    {
        "name": "guest_access_code",
        "trigger": "booking_confirmed",
        "handler": _rule_guest_access_code,
        "description": "Create time-bound access code on booking confirmation",
    },
    {
        "name": "checkin_prep",
        "trigger": "check_in",
        "condition": lambda e: e.minutes_offset <= -60,
        "handler": _rule_checkin_prep,
        "description": "Prepare property 60min before check-in",
    },
    {
        "name": "preheat_cool",
        "trigger": "check_in",
        "condition": lambda e: -60 < e.minutes_offset <= -30,
        "handler": _rule_preheat_cool,
        "description": "Start HVAC 30min before check-in",
    },
    {
        "name": "checkout_cleanup",
        "trigger": "check_out",
        "condition": lambda e: e.minutes_offset >= 10,
        "handler": _rule_checkout_cleanup,
        "description": "Secure property 10min after checkout",
    },
    {
        "name": "hvac_off",
        "trigger": "check_out",
        "condition": lambda e: e.minutes_offset >= 30,
        "handler": _rule_hvac_off,
        "description": "Turn off HVAC 30min after checkout",
    },
    {
        "name": "cancel_revoke",
        "trigger": "booking_cancelled",
        "handler": _rule_cancel_revoke,
        "description": "Revoke access codes on cancellation",
    },
    {
        "name": "stay_extension",
        "trigger": "booking_modified",
        "handler": _rule_stay_extension,
        "description": "Update access codes on booking modification",
    },
    {
        "name": "vacant_door_alert",
        "trigger": "lock.unlocked",
        "condition": lambda e: e.event_data.get("is_vacant", False),
        "handler": _rule_vacant_door_alert,
        "description": "Alert on door unlock during vacancy",
    },
    {
        "name": "between_bookings_away",
        "trigger": "check_out",
        "condition": lambda e: not e.event_data.get("has_next_booking", True),
        "handler": _rule_between_bookings_away,
        "description": "Set away climate when no next booking",
    },
]


class AutomationEngine:
    """Event-driven automation rules engine.

    Processes events against registered automation templates.
    Rules can be enabled/disabled per property. Brain Engine
    learns which automations work via SkillEvolution.

    Args:
        disabled_rules: Set of rule names to skip.
    """

    def __init__(
        self,
        disabled_rules: set[str] | None = None,
    ) -> None:
        self._disabled = disabled_rules or set()

    def process(self, event: AutomationEvent) -> AutomationResult:
        """Process an event against all automation rules.

        Args:
            event: Automation trigger event.

        Returns:
            AutomationResult with matched rules and actions.
        """
        result = AutomationResult(
            event_type=event.event_type,
            property_id=event.property_id,
        )

        for rule in _AUTOMATION_RULES:
            self._evaluate_rule(rule, event, result)

        logger.info(
            "Automation: %s → %d rules, %d actions for %s",
            event.event_type,
            len(result.matched_rules),
            len(result.actions),
            event.property_id,
        )
        return result

    def _evaluate_rule(
        self,
        rule: dict[str, Any],
        event: AutomationEvent,
        result: AutomationResult,
    ) -> None:
        """Evaluate a single rule against an event.

        Args:
            rule: Rule definition dict.
            event: Trigger event.
            result: Result to append matched actions to.
        """
        if rule["trigger"] != event.event_type:
            return

        name = rule["name"]
        if name in self._disabled:
            result.skipped_rules.append(name)
            return

        condition = rule.get("condition")
        if condition and not condition(event):
            return

        actions = rule["handler"](event)
        result.matched_rules.append(name)
        result.actions.extend(actions)

    @property
    def available_rules(self) -> list[dict[str, str]]:
        """List all available automation templates."""
        return [
            {"name": r["name"], "trigger": r["trigger"], "description": r["description"]}
            for r in _AUTOMATION_RULES
        ]
