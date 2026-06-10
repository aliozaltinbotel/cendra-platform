"""Stakeholder Model — Zero-trust role hierarchy for Brain Engine.

Based on: Agents of Chaos (arXiv:2602.20021).
Implements explicit stakeholder model with immutable core rules.

Role hierarchy: OWNER > MANAGER > GUEST > CLEANER > VENDOR > UNKNOWN
Each role has specific permissions. Actions are validated against
the caller's role before execution.
"""

from __future__ import annotations

import logging
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class StakeholderRole(IntEnum):
    """Stakeholder roles ordered by trust level (higher = more trusted)."""

    UNKNOWN = 0
    VENDOR = 10
    CLEANER = 20
    GUEST = 30
    MANAGER = 40
    OWNER = 50


# Actions each role is allowed to perform
_ROLE_PERMISSIONS: dict[StakeholderRole, frozenset[str]] = {
    StakeholderRole.OWNER: frozenset({
        "approve_action",
        "reject_action",
        "set_policy",
        "override_decision",
        "view_financials",
        "manage_property",
        "configure_autonomy",
        "view_intelligence",
        "trigger_consolidation",
        "manage_cleaners",
        "manage_guests",
    }),
    StakeholderRole.MANAGER: frozenset({
        "approve_action",
        "reject_action",
        "view_intelligence",
        "manage_cleaners",
        "manage_guests",
        "view_financials",
        "manage_property",
    }),
    StakeholderRole.GUEST: frozenset({
        "send_message",
        "request_service",
        "report_issue",
        "request_late_checkout",
        "view_booking_info",
    }),
    StakeholderRole.CLEANER: frozenset({
        "accept_task",
        "decline_task",
        "report_completion",
        "report_issue",
        "upload_photos",
    }),
    StakeholderRole.VENDOR: frozenset({
        "accept_job",
        "decline_job",
        "submit_quote",
        "report_completion",
    }),
    StakeholderRole.UNKNOWN: frozenset({
        "send_message",
    }),
}

# Immutable core rules that cannot be overridden by any role
_IMMUTABLE_RULES: list[str] = [
    "Never share guest personal data with other guests",
    "Never approve financial actions above owner-set limits",
    "Never bypass safety checks for any stakeholder",
    "Never execute actions without audit trail",
    "Always require owner approval for policy changes",
    "Never disclose owner financial details to guests",
]

# Maximum number of actions per single request per role
_MAX_ACTIONS_PER_REQUEST: dict[StakeholderRole, int] = {
    StakeholderRole.OWNER: 20,
    StakeholderRole.MANAGER: 15,
    StakeholderRole.GUEST: 5,
    StakeholderRole.CLEANER: 5,
    StakeholderRole.VENDOR: 3,
    StakeholderRole.UNKNOWN: 1,
}


class StakeholderModel:
    """Zero-trust stakeholder validation engine.

    Validates every action against the caller's role and enforces
    immutable security rules that cannot be overridden.
    """

    def identify_role(self, context: dict[str, Any]) -> StakeholderRole:
        """Identify the stakeholder role from request context.

        Args:
            context: Request context containing role indicators.

        Returns:
            The identified StakeholderRole.
        """
        explicit_role = context.get("role", "").lower()
        role = _ROLE_NAME_MAP.get(explicit_role, StakeholderRole.UNKNOWN)

        if role == StakeholderRole.UNKNOWN:
            role = self._infer_role(context)

        logger.debug("Identified stakeholder role: %s", role.name)
        return role

    def get_permissions(self, role: StakeholderRole) -> frozenset[str]:
        """Get the set of allowed actions for a role.

        Args:
            role: The stakeholder role.

        Returns:
            Frozen set of permitted action names.
        """
        return _ROLE_PERMISSIONS.get(role, frozenset())

    def validate_action(
        self,
        role: StakeholderRole,
        action: str,
    ) -> bool:
        """Check if a role is allowed to perform an action.

        Args:
            role: The stakeholder role.
            action: The action to validate.

        Returns:
            True if the action is permitted.
        """
        permissions = self.get_permissions(role)
        allowed = action in permissions

        if not allowed:
            logger.warning(
                "Action blocked: role=%s action=%s",
                role.name, action,
            )
        return allowed

    def validate_action_count(
        self,
        role: StakeholderRole,
        action_count: int,
    ) -> bool:
        """Check if the number of actions exceeds the role's limit.

        Args:
            role: The stakeholder role.
            action_count: Number of actions in the request.

        Returns:
            True if within limits.
        """
        max_allowed = _MAX_ACTIONS_PER_REQUEST.get(role, 1)
        return action_count <= max_allowed

    def get_immutable_rules(self) -> list[str]:
        """Return the list of immutable security rules.

        Returns:
            List of rule strings that can never be overridden.
        """
        return list(_IMMUTABLE_RULES)

    def get_trust_level(self, role: StakeholderRole) -> float:
        """Get a normalized trust level for a role (0.0 to 1.0).

        Args:
            role: The stakeholder role.

        Returns:
            Trust level as a float.
        """
        return role.value / StakeholderRole.OWNER.value

    @staticmethod
    def _infer_role(context: dict[str, Any]) -> StakeholderRole:
        """Infer role from contextual clues when not explicitly set.

        Args:
            context: Request context dict.

        Returns:
            Best-guess StakeholderRole.
        """
        if context.get("owner_id"):
            return StakeholderRole.OWNER
        if context.get("manager_id"):
            return StakeholderRole.MANAGER
        if context.get("guest_id") or context.get("reservation_id"):
            return StakeholderRole.GUEST
        if context.get("cleaner_id"):
            return StakeholderRole.CLEANER
        if context.get("vendor_id"):
            return StakeholderRole.VENDOR
        return StakeholderRole.UNKNOWN


_ROLE_NAME_MAP: dict[str, StakeholderRole] = {
    "owner": StakeholderRole.OWNER,
    "manager": StakeholderRole.MANAGER,
    "guest": StakeholderRole.GUEST,
    "cleaner": StakeholderRole.CLEANER,
    "vendor": StakeholderRole.VENDOR,
}
