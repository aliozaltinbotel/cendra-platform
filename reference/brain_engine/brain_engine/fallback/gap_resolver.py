"""GapResolver — Resolves missing data by escalating through a resolution chain.

When a gap is detected (no cleaner, no vendor contact, etc.), the resolver
attempts to fill the gap through a priority-ordered chain:
1. Check alternative data sources
2. Contact the property manager
3. Contact the property owner
4. Use a marketplace/fallback service
"""

from __future__ import annotations

import logging
from enum import StrEnum
from dataclasses import dataclass, field
from typing import Any

from brain_engine.protocols import Notifier, VoiceClient, SlotStore

logger = logging.getLogger(__name__)


class GapType(StrEnum):
    """Types of data gaps that can be resolved."""

    NO_CLEANER = "no_cleaner"
    ALL_CLEANERS_BUSY = "all_cleaners_busy"
    NO_VENDOR = "no_vendor"
    NO_MANAGER_CONTACT = "no_manager_contact"
    NO_GUEST_PHONE = "no_guest_phone"
    NO_ACCESS_CODE = "no_access_code"
    NO_PROPERTY_CONFIG = "no_property_config"
    NO_BEFORE_PHOTOS = "no_before_photos"


@dataclass(slots=True)
class ResolutionStep:
    """A single step in the gap resolution process.

    Attributes:
        action: What action to take.
        target: Who/what to contact or check.
        message: Message to send (if applicable).
        completed: Whether this step has been executed.
        success: Whether this step resolved the gap.
        result: Any data returned by this step.
    """

    action: str
    target: str
    message: str = ""
    completed: bool = False
    success: bool = False
    result: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolutionPlan:
    """A complete plan for resolving a detected gap.

    Attributes:
        gap_type: Type of gap being resolved.
        steps: Ordered list of resolution steps.
        resolved: Whether the gap has been resolved.
        resolution_data: Data that fills the gap.
    """

    gap_type: GapType
    steps: list[ResolutionStep] = field(default_factory=list)
    resolved: bool = False
    resolution_data: dict[str, Any] = field(default_factory=dict)


# Resolution chains per gap type
RESOLUTION_CHAINS: dict[GapType, list[dict[str, str]]] = {
    GapType.ALL_CLEANERS_BUSY: [
        {
            "action": "call_additional_cleaners",
            "target": "backup_cleaner_list",
            "message": (
                "All primary cleaners are busy. "
                "Checking backup cleaner list..."
            ),
        },
        {
            "action": "call_manager",
            "target": "property_manager",
            "message": (
                "No backup cleaners available. "
                "Contacting property manager for assistance: "
                "All cleaners are currently busy. The situation is: "
                "{situation_summary}. Do you have another cleaner "
                "we can dispatch?"
            ),
        },
        {
            "action": "call_owner",
            "target": "property_owner",
            "message": (
                "Property manager unreachable. "
                "Contacting property owner: We could not find an available "
                "cleaner for your property. Would you like us to: "
                "1) Keep trying current cleaners "
                "2) Post the job on Turno marketplace "
                "3) Delay the cleaning"
            ),
        },
        {
            "action": "marketplace_search",
            "target": "turno_marketplace",
            "message": (
                "Searching Turno marketplace for available cleaners in the area..."
            ),
        },
    ],
    GapType.NO_CLEANER: [
        {
            "action": "notify_manager",
            "target": "property_manager",
            "message": (
                "No cleaner is configured for property {property_id}. "
                "Please add a cleaner to the system."
            ),
        },
        {
            "action": "notify_owner",
            "target": "property_owner",
            "message": (
                "Your property {property_id} has no assigned cleaner. "
                "Would you like to: "
                "1) Add your cleaner's contact "
                "2) Find one on Turno marketplace"
            ),
        },
    ],
    GapType.NO_VENDOR: [
        {
            "action": "notify_manager",
            "target": "property_manager",
            "message": (
                "No vendor is available for repair type '{repair_type}'. "
                "Please provide a vendor contact."
            ),
        },
        {
            "action": "notify_owner",
            "target": "property_owner",
            "message": (
                "We detected damage that needs repair, but no vendor is configured. "
                "Please provide a repair vendor contact for {property_id}."
            ),
        },
    ],
    GapType.NO_MANAGER_CONTACT: [
        {
            "action": "notify_owner",
            "target": "property_owner",
            "message": (
                "We need to contact the property manager but no contact "
                "information is available. Please provide manager details."
            ),
        },
    ],
    GapType.NO_GUEST_PHONE: [
        {
            "action": "check_pms",
            "target": "graphql_unified_data",
            "message": "Checking unified data layer for guest contact information...",
        },
        {
            "action": "check_booking_platform",
            "target": "airbnb_messaging",
            "message": "Attempting to contact guest through Airbnb messaging...",
        },
        {
            "action": "notify_manager",
            "target": "property_manager",
            "message": (
                "Cannot reach guest — no phone number available. "
                "Please check the booking for contact details."
            ),
        },
    ],
    GapType.NO_ACCESS_CODE: [
        {
            "action": "generate_code",
            "target": "nuki_smart_lock",
            "message": "Generating temporary access code via Nuki...",
        },
        {
            "action": "notify_manager",
            "target": "property_manager",
            "message": (
                "Smart lock unavailable. Please provide the physical "
                "key location or manual access code."
            ),
        },
    ],
    GapType.NO_BEFORE_PHOTOS: [
        {
            "action": "check_previous_checkout",
            "target": "photo_archive",
            "message": "Checking photo archive for previous checkout photos...",
        },
        {
            "action": "request_from_cleaner",
            "target": "cleaner",
            "message": (
                "Please take 'before cleaning' photos of the apartment "
                "immediately and send them via Telegram."
            ),
        },
    ],
}


class GapResolver:
    """Resolves data gaps through escalation chains.

    For each gap type, maintains an ordered resolution chain.
    Executes steps sequentially until the gap is resolved or
    all options are exhausted.

    Args:
        notifier: Notification backend for sending messages.
        voice_client: Voice client for making phone calls.
        slot_manager: SlotManager for reading/writing slot values.
    """

    def __init__(
        self,
        notifier: Notifier | None = None,
        voice_client: VoiceClient | None = None,
        slot_manager: SlotStore | None = None,
    ) -> None:
        self._notifier = notifier
        self._voice_client = voice_client
        self._slot_manager = slot_manager

    def create_resolution_plan(
        self,
        gap_type: GapType,
        context: dict[str, Any] | None = None,
    ) -> ResolutionPlan:
        """Create a resolution plan for a specific gap type.

        Args:
            gap_type: The type of gap to resolve.
            context: Context data for message formatting.

        Returns:
            ResolutionPlan with ordered steps.
        """
        chain = RESOLUTION_CHAINS.get(gap_type, [])
        ctx = context or {}

        steps = [
            ResolutionStep(
                action=step["action"],
                target=step["target"],
                message=step["message"].format_map(_SafeFormatDict(ctx)),
            )
            for step in chain
        ]

        plan = ResolutionPlan(gap_type=gap_type, steps=steps)

        logger.info(
            "Created resolution plan for %s with %d steps",
            gap_type, len(steps),
        )
        return plan

    async def execute_step(
        self,
        step: ResolutionStep,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Execute a single resolution step.

        Args:
            step: The step to execute.
            context: Additional context data.

        Returns:
            True if the step resolved the gap, False otherwise.
        """
        ctx = context or {}

        logger.info(
            "Executing resolution step: action=%s, target=%s",
            step.action, step.target,
        )
        step.completed = True

        match step.action:
            case "call_additional_cleaners":
                step.success = await self._try_backup_cleaners(ctx)

            case "call_manager" | "notify_manager":
                step.success = await self._notify_target(
                    target_type="manager",
                    message=step.message,
                    context=ctx,
                )

            case "call_owner" | "notify_owner":
                step.success = await self._notify_target(
                    target_type="owner",
                    message=step.message,
                    context=ctx,
                )

            case "marketplace_search":
                step.success = await self._search_marketplace(ctx)

            case "check_pms":
                step.success = await self._check_pms(ctx)

            case "generate_code":
                step.success = await self._generate_access_code(ctx)

            case _:
                logger.warning("Unknown resolution action: %s", step.action)
                step.success = False

        return step.success

    async def execute_plan(
        self,
        plan: ResolutionPlan,
        context: dict[str, Any] | None = None,
    ) -> ResolutionPlan:
        """Execute a full resolution plan, stopping at first success.

        Args:
            plan: The plan to execute.
            context: Additional context data.

        Returns:
            The updated plan with step results.
        """
        for step in plan.steps:
            success = await self.execute_step(step, context)
            if success:
                plan.resolved = True
                plan.resolution_data = step.result
                logger.info(
                    "Gap %s resolved at step: %s",
                    plan.gap_type, step.action,
                )
                break

        if not plan.resolved:
            logger.warning(
                "Gap %s could not be resolved after %d steps",
                plan.gap_type, len(plan.steps),
            )

        return plan

    async def _try_backup_cleaners(self, context: dict[str, Any]) -> bool:
        """Try to find and contact backup cleaners."""
        backup_cleaners = context.get("backup_cleaners", [])
        if not backup_cleaners:
            return False

        for cleaner in backup_cleaners:
            phone = cleaner.get("phone", "")
            name = cleaner.get("name", "Unknown")
            if not phone:
                continue

            logger.info("Trying backup cleaner: %s (%s)", name, phone)

            if self._voice_client:
                try:
                    result = await self._voice_client.make_call(
                        phone_number=phone,
                        script=context.get("cleaner_script", ""),
                        first_message=f"Hello {name}, we need a cleaner urgently...",
                    )
                    if result and result.status == "completed":
                        return True
                except Exception:
                    logger.exception("Failed to call backup cleaner %s", name)

            if self._notifier:
                try:
                    await self._notifier.send_message(
                        target=phone,
                        text=(
                            f"Urgent cleaning request for {context.get('property_address', 'property')}. "
                            f"Available? Reply YES or NO."
                        ),
                    )
                except Exception:
                    logger.exception("Failed to message backup cleaner %s", name)

        return False

    async def _notify_target(
        self,
        target_type: str,
        message: str,
        context: dict[str, Any],
    ) -> bool:
        """Send notification to manager or owner."""
        contact = context.get(f"{target_type}_phone", "")
        if not contact and self._slot_manager:
            contact = self._slot_manager.get_value(f"{target_type}_phone", "")

        if not contact:
            logger.warning("No %s contact available for notification", target_type)
            return False

        if self._notifier:
            try:
                await self._notifier.send_message(target=contact, text=message)
                logger.info("Notified %s at %s", target_type, contact)
                return True
            except Exception:
                logger.exception("Failed to notify %s", target_type)

        if self._voice_client:
            try:
                await self._voice_client.make_call(
                    phone_number=contact,
                    script=message,
                )
                logger.info("Called %s at %s", target_type, contact)
                return True
            except Exception:
                logger.exception("Failed to call %s", target_type)

        return False

    async def _search_marketplace(self, context: dict[str, Any]) -> bool:
        """Search Turno or other marketplace for available cleaners."""
        logger.info("Searching marketplace for cleaners (simulated)")
        # In production, this would call the Turno API
        return False

    async def _check_pms(self, context: dict[str, Any]) -> bool:
        """Check PMS system for missing data."""
        logger.info("Checking PMS for missing data (simulated)")
        return False

    async def _generate_access_code(self, context: dict[str, Any]) -> bool:
        """Generate a temporary access code via smart lock API."""
        logger.info("Generating access code (simulated)")
        return False


class _SafeFormatDict(dict):
    """Dict subclass that returns {key} for missing keys in format_map."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"
