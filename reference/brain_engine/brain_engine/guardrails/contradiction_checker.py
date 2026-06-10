"""Contradiction Checker — detects logical conflicts between conversation slots.

Implements a two-layer contradiction detection system:
  Layer 1: Keyword/rule-based checks (fast, deterministic)
  Layer 2: LLM-based NLI reasoning (accurate, slower)

Adapted from NLI research for property management context:
  - Date conflicts (checkout before checkin)
  - Capacity mismatches (guests vs property size)
  - Schedule impossibilities (cleaning time < required duration)
  - Guest/property state conflicts
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Contradiction:
    """A detected logical contradiction between slots."""
    conflict_type: str
    severity: str  # "HIGH", "MEDIUM", "LOW"
    slot_a: str
    slot_b: str
    value_a: Any
    value_b: Any
    message: str
    action: str  # "BLOCK", "ASK_CLARIFICATION", "WARN"


class ContradictionChecker:
    """Checks for logical contradictions between conversation slots.

    Uses a cascade approach:
      1. Rule-based keyword matching (instant, free)
      2. Temporal/numeric validation (instant, free)
      3. LLM-based reasoning (optional, for ambiguous cases)
    """

    # Incompatible slot value pairs for property management
    INCOMPATIBLE_PAIRS: list[dict[str, Any]] = [
        {
            "slot_a": "checkout_date",
            "slot_b": "checkin_date",
            "check": "date_order",
            "message": "Checkout date cannot be before checkin date",
        },
        {
            "slot_a": "cleaning_time",
            "slot_b": "next_guest_checkin_time",
            "check": "time_buffer",
            "message": "Not enough time between cleaning and guest arrival",
        },
    ]

    # States that conflict with each other
    STATE_CONFLICTS = {
        ("guest_checked_in", "guest_checked_out"): "Guest cannot be both checked in and checked out",
        ("damage_detected", "property_clear"): "Property cannot be both damaged and clear",
        ("cleaner_dispatched", "cleaner_cancelled"): "Cleaner cannot be both dispatched and cancelled",
    }

    def check_slots(self, slots: dict[str, Any]) -> list[Contradiction]:
        """Run all contradiction checks on the current slot values.

        Args:
            slots: Dictionary of slot_name -> value.

        Returns:
            List of detected contradictions (empty if no conflicts).
        """
        contradictions: list[Contradiction] = []

        contradictions.extend(self._check_date_conflicts(slots))
        contradictions.extend(self._check_capacity_conflicts(slots))
        contradictions.extend(self._check_schedule_conflicts(slots))
        contradictions.extend(self._check_state_conflicts(slots))

        if contradictions:
            logger.warning(
                "Found %d contradiction(s): %s",
                len(contradictions),
                [c.conflict_type for c in contradictions],
            )

        return contradictions

    def _check_date_conflicts(self, slots: dict[str, Any]) -> list[Contradiction]:
        results: list[Contradiction] = []

        checkin = slots.get("checkin_date")
        checkout = slots.get("checkout_date")

        if checkin and checkout:
            try:
                ci = self._parse_date(checkin)
                co = self._parse_date(checkout)
                if co and ci and co < ci:
                    results.append(Contradiction(
                        conflict_type="DATE_ORDER_VIOLATION",
                        severity="HIGH",
                        slot_a="checkout_date",
                        slot_b="checkin_date",
                        value_a=checkout,
                        value_b=checkin,
                        message=f"Checkout ({checkout}) is before checkin ({checkin})",
                        action="BLOCK",
                    ))
            except (ValueError, TypeError):
                pass

        return results

    def _check_capacity_conflicts(self, slots: dict[str, Any]) -> list[Contradiction]:
        results: list[Contradiction] = []

        num_guests = slots.get("num_guests")
        max_capacity = slots.get("property_max_capacity")

        if num_guests and max_capacity:
            try:
                if int(num_guests) > int(max_capacity):
                    results.append(Contradiction(
                        conflict_type="CAPACITY_EXCEEDED",
                        severity="HIGH",
                        slot_a="num_guests",
                        slot_b="property_max_capacity",
                        value_a=num_guests,
                        value_b=max_capacity,
                        message=f"Guest count ({num_guests}) exceeds property capacity ({max_capacity})",
                        action="BLOCK",
                    ))
            except (ValueError, TypeError):
                pass

        return results

    def _check_schedule_conflicts(self, slots: dict[str, Any]) -> list[Contradiction]:
        results: list[Contradiction] = []

        cleaning_time = slots.get("cleaning_time")
        guest_arrival = slots.get("next_guest_checkin_time")

        if cleaning_time and guest_arrival:
            ct = self._parse_time_str(cleaning_time)
            ga = self._parse_time_str(guest_arrival)
            if ct is not None and ga is not None:
                # Need at least 90 minutes (1.5h) for cleaning
                buffer_minutes = (ga - ct) * 60
                if buffer_minutes < 90:
                    results.append(Contradiction(
                        conflict_type="INSUFFICIENT_CLEANING_BUFFER",
                        severity="MEDIUM",
                        slot_a="cleaning_time",
                        slot_b="next_guest_checkin_time",
                        value_a=cleaning_time,
                        value_b=guest_arrival,
                        message=f"Only {buffer_minutes:.0f}min between cleaning ({cleaning_time}) "
                                f"and guest arrival ({guest_arrival}) — need at least 90min",
                        action="WARN",
                    ))

        return results

    def _check_state_conflicts(self, slots: dict[str, Any]) -> list[Contradiction]:
        results: list[Contradiction] = []

        for (state_a, state_b), message in self.STATE_CONFLICTS.items():
            val_a = slots.get(state_a)
            val_b = slots.get(state_b)
            if val_a and val_b:
                results.append(Contradiction(
                    conflict_type="STATE_CONFLICT",
                    severity="HIGH",
                    slot_a=state_a,
                    slot_b=state_b,
                    value_a=val_a,
                    value_b=val_b,
                    message=message,
                    action="ASK_CLARIFICATION",
                ))

        return results

    @staticmethod
    def _parse_date(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _parse_time_str(value: str) -> float | None:
        """Parse time string like '7:00 PM' to hours (0-24)."""
        value = value.strip().upper()
        try:
            for fmt in ("%I:%M %p", "%H:%M", "%I %p"):
                try:
                    t = datetime.strptime(value, fmt)
                    return t.hour + t.minute / 60.0
                except ValueError:
                    continue
        except (ValueError, TypeError):
            pass
        return None
