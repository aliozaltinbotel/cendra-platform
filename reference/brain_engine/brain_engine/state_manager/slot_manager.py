"""Slot Manager for tracking conversation slots and their fill status.

Slots represent pieces of information the agent needs to collect from the user
to complete a task. Each slot has a name, optional value, required flag, and
validation function.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SlotInfo:
    """Describes a single slot in the conversation state.

    Attributes:
        name: Unique identifier for the slot.
        value: Current value of the slot (None if unfilled).
        required: Whether this slot must be filled before the task can proceed.
        filled: Whether the slot has been explicitly set.
        description: Human-readable description of what this slot captures.
        validator: Optional callable that validates a proposed value.
            Should return True if valid, False otherwise.
    """

    name: str
    value: Any = None
    required: bool = True
    filled: bool = False
    description: str = ""
    validator: Callable[[Any], bool] | None = field(default=None, repr=False)

    def validate(self, proposed_value: Any) -> bool:
        """Check whether a proposed value is valid for this slot."""
        if self.validator is None:
            return True
        try:
            return self.validator(proposed_value)
        except Exception as exc:
            logger.warning("Validator for slot '%s' raised: %s", self.name, exc)
            return False


class SlotManager:
    """Manages a collection of named slots for a conversational task.

    Provides methods to set, get, query missing slots, and check overall
    completion. The slot manager is the source of truth for what information
    the agent still needs to collect.

    Args:
        slots: Initial slot definitions. Can be a list of SlotInfo objects
            or a dict mapping slot names to SlotInfo objects.
    """

    def __init__(
        self, slots: list[SlotInfo] | dict[str, SlotInfo] | None = None
    ) -> None:
        self._slots: dict[str, SlotInfo] = {}
        if isinstance(slots, list):
            for slot in slots:
                self._slots[slot.name] = slot
        elif isinstance(slots, dict):
            self._slots = dict(slots)

    def add_slot(self, slot: SlotInfo) -> None:
        """Register a new slot definition.

        Args:
            slot: The SlotInfo to register. Overwrites any existing slot
                with the same name.
        """
        self._slots[slot.name] = slot
        logger.debug("Registered slot: %s (required=%s)", slot.name, slot.required)

    def set_slot(self, name: str, value: Any) -> bool:
        """Set the value of a slot if it passes validation.

        Args:
            name: The slot name to set.
            value: The value to assign.

        Returns:
            True if the slot was successfully set, False if validation
            failed or the slot does not exist.
        """
        slot = self._slots.get(name)
        if slot is None:
            # Auto-create slot for dynamic usage (e.g. scenario flows)
            self.add_slot(SlotInfo(name=name, required=False))
            slot = self._slots[name]

        if not slot.validate(value):
            logger.info("Validation failed for slot '%s' with value: %s", name, value)
            return False

        slot.value = value
        slot.filled = True
        logger.info("Slot '%s' set to: %s", name, value)
        return True

    def get_slot(self, name: str) -> SlotInfo | None:
        """Retrieve a slot by name.

        Args:
            name: The slot name to look up.

        Returns:
            The SlotInfo if found, None otherwise.
        """
        return self._slots.get(name)

    def get_value(self, name: str, default: Any = None) -> Any:
        """Get just the value of a slot.

        Args:
            name: The slot name.
            default: Value to return if slot is not found or not filled.

        Returns:
            The slot's value, or the default.
        """
        slot = self._slots.get(name)
        if slot is None or not slot.filled:
            return default
        return slot.value

    def get_missing_slots(self) -> list[SlotInfo]:
        """Return all required slots that have not been filled.

        Returns:
            List of SlotInfo objects that are required but not yet filled.
        """
        return [
            slot
            for slot in self._slots.values()
            if slot.required and not slot.filled
        ]

    def get_filled_slots(self) -> list[SlotInfo]:
        """Return all slots that have been filled."""
        return [slot for slot in self._slots.values() if slot.filled]

    def is_complete(self) -> bool:
        """Check whether all required slots are filled.

        Returns:
            True if every required slot has been filled.
        """
        return len(self.get_missing_slots()) == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the slot manager state to a plain dictionary.

        Returns:
            Dictionary mapping slot names to their current state.
        """
        return {
            name: {
                "value": slot.value,
                "required": slot.required,
                "filled": slot.filled,
                "description": slot.description,
            }
            for name, slot in self._slots.items()
        }

    def reset(self, slot_names: list[str] | None = None) -> None:
        """Reset slots to their unfilled state.

        Args:
            slot_names: Optional list of specific slot names to reset.
                If None, all slots are reset.
        """
        targets = slot_names if slot_names is not None else list(self._slots.keys())
        for name in targets:
            slot = self._slots.get(name)
            if slot is not None:
                slot.value = None
                slot.filled = False
                logger.debug("Reset slot: %s", name)

    @property
    def all_slots(self) -> dict[str, SlotInfo]:
        """Read-only access to the internal slots dictionary."""
        return dict(self._slots)

    def __len__(self) -> int:
        return len(self._slots)

    def __repr__(self) -> str:
        filled = len(self.get_filled_slots())
        total = len(self._slots)
        return f"SlotManager(filled={filled}/{total}, complete={self.is_complete()})"
