"""FallbackChain — Ordered chain of fallback actions for any failing operation.

Provides a generic chain-of-responsibility pattern:
primary action → secondary → tertiary → manual escalation.

Usage:
    chain = FallbackChain()
    chain.add_step("call_primary_cleaner", call_primary)
    chain.add_step("call_backup_cleaner", call_backup)
    chain.add_step("call_third_cleaner", call_third)
    chain.add_step("call_manager", call_manager)
    chain.add_step("call_owner", call_owner)
    result = await chain.execute(context)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Type alias for async fallback action
FallbackAction = Callable[[dict[str, Any]], Awaitable[bool]]


@dataclass(slots=True)
class FallbackStep:
    """A single step in the fallback chain.

    Attributes:
        name: Human-readable step name.
        action: Async callable that returns True on success.
        description: What this step does.
        executed: Whether this step has been executed.
        success: Whether this step succeeded.
        error: Error message if this step failed.
    """

    name: str
    action: FallbackAction
    description: str = ""
    executed: bool = False
    success: bool = False
    error: str = ""


@dataclass(slots=True)
class FallbackResult:
    """Result of executing a fallback chain.

    Attributes:
        resolved: Whether any step succeeded.
        successful_step: Name of the step that succeeded.
        steps_attempted: How many steps were tried.
        total_steps: Total steps in the chain.
        step_details: Details of each attempted step.
    """

    resolved: bool = False
    successful_step: str = ""
    steps_attempted: int = 0
    total_steps: int = 0
    step_details: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API/SSE responses."""
        return {
            "resolved": self.resolved,
            "successful_step": self.successful_step,
            "steps_attempted": self.steps_attempted,
            "total_steps": self.total_steps,
            "step_details": self.step_details,
        }


class FallbackChain:
    """Executes an ordered chain of fallback actions.

    Each step is tried in order. The chain stops at the first successful step.
    All step results are recorded for audit trail.

    Example for the "all cleaners busy" scenario:
        1. Call primary cleaner
        2. Call backup cleaner
        3. Search for a third cleaner
        4. Call property manager
        5. Call property owner
        6. Post on Turno marketplace
    """

    def __init__(self) -> None:
        self._steps: list[FallbackStep] = []

    def add_step(
        self,
        name: str,
        action: FallbackAction,
        description: str = "",
    ) -> FallbackChain:
        """Add a step to the chain. Returns self for chaining.

        Args:
            name: Step identifier.
            action: Async callable that returns True on success.
            description: Human-readable description.
        """
        self._steps.append(FallbackStep(
            name=name,
            action=action,
            description=description,
        ))
        return self

    async def execute(self, context: dict[str, Any] | None = None) -> FallbackResult:
        """Execute the fallback chain, stopping at first success.

        Args:
            context: Shared context passed to each step.

        Returns:
            FallbackResult with details of all attempted steps.
        """
        ctx = context or {}
        result = FallbackResult(total_steps=len(self._steps))

        for step in self._steps:
            result.steps_attempted += 1
            step.executed = True

            logger.info(
                "Fallback chain: trying step %d/%d '%s'",
                result.steps_attempted, result.total_steps, step.name,
            )

            try:
                step.success = await step.action(ctx)
            except Exception as exc:
                step.error = str(exc)
                step.success = False
                logger.exception(
                    "Fallback step '%s' failed with error", step.name,
                )

            result.step_details.append({
                "name": step.name,
                "description": step.description,
                "success": step.success,
                "error": step.error,
            })

            if step.success:
                result.resolved = True
                result.successful_step = step.name
                logger.info(
                    "Fallback chain resolved at step '%s' (%d/%d)",
                    step.name, result.steps_attempted, result.total_steps,
                )
                break

        if not result.resolved:
            logger.warning(
                "Fallback chain exhausted: %d steps tried, none succeeded",
                result.steps_attempted,
            )

        return result

    @property
    def step_count(self) -> int:
        """Number of steps in the chain."""
        return len(self._steps)

    @property
    def step_names(self) -> list[str]:
        """Names of all steps in order."""
        return [s.name for s in self._steps]


def build_cleaner_fallback_chain(
    cleaners: list[dict[str, Any]],
    voice_client: Any | None = None,
    notifier: Any | None = None,
    manager_phone: str = "",
    owner_phone: str = "",
) -> FallbackChain:
    """Build a pre-configured fallback chain for cleaner dispatch.

    The chain tries each cleaner in order (by rating), then escalates
    to manager, then owner, then marketplace.

    Args:
        cleaners: List of cleaner configs (name, phone, rating, available).
        voice_client: ElevenLabs voice client for calls.
        notifier: Telegram/WhatsApp notifier.
        manager_phone: Property manager phone number.
        owner_phone: Property owner phone number.

    Returns:
        Configured FallbackChain ready to execute.
    """
    chain = FallbackChain()

    # Sort cleaners by rating (best first)
    sorted_cleaners = sorted(
        cleaners,
        key=lambda c: c.get("rating", 0),
        reverse=True,
    )

    # Add each cleaner as a step
    for i, cleaner in enumerate(sorted_cleaners, start=1):
        name = cleaner.get("name", f"Cleaner {i}")
        phone = cleaner.get("phone", "")

        async def _call_cleaner(
            ctx: dict[str, Any],
            _name: str = name,
            _phone: str = phone,
        ) -> bool:
            if not _phone:
                return False
            logger.info("Attempting to reach cleaner %s at %s", _name, _phone)
            if voice_client:
                try:
                    result = await voice_client.make_call(
                        phone_number=_phone,
                        script=ctx.get("cleaner_script", ""),
                        first_message=f"Hello {_name}, we have an urgent cleaning job...",
                    )
                    return result is not None
                except Exception:
                    logger.exception("Failed to call cleaner %s", _name)
            return False

        chain.add_step(
            name=f"call_cleaner_{name.lower().replace(' ', '_')}",
            action=_call_cleaner,
            description=f"Call cleaner {name} ({phone})",
        )

    # Escalation: call property manager
    if manager_phone:
        async def _call_manager(ctx: dict[str, Any]) -> bool:
            logger.info("Escalating to property manager: %s", manager_phone)
            if voice_client:
                try:
                    await voice_client.make_call(
                        phone_number=manager_phone,
                        script=(
                            "All cleaners are currently unavailable. "
                            f"Property: {ctx.get('property_address', 'N/A')}. "
                            "Do you have an alternative cleaner?"
                        ),
                    )
                    return True
                except Exception:
                    logger.exception("Failed to call manager")
            if notifier:
                try:
                    await notifier.send_message(
                        target=manager_phone,
                        text=(
                            "URGENT: All cleaners busy for "
                            f"{ctx.get('property_address', 'your property')}. "
                            "Please suggest an alternative cleaner."
                        ),
                    )
                    return True
                except Exception:
                    logger.exception("Failed to message manager")
            return False

        chain.add_step(
            name="call_manager",
            action=_call_manager,
            description=f"Call property manager ({manager_phone})",
        )

    # Escalation: call property owner
    if owner_phone:
        async def _call_owner(ctx: dict[str, Any]) -> bool:
            logger.info("Escalating to property owner: %s", owner_phone)
            if notifier:
                try:
                    await notifier.send_message(
                        target=owner_phone,
                        text=(
                            "We could not find an available cleaner for "
                            f"{ctx.get('property_address', 'your property')}. "
                            "All cleaners and the manager were unavailable. "
                            "Options:\n"
                            "1) Provide another cleaner contact\n"
                            "2) Post on Turno marketplace\n"
                            "3) Delay cleaning\n"
                            "Reply with your choice."
                        ),
                    )
                    return True
                except Exception:
                    logger.exception("Failed to message owner")
            return False

        chain.add_step(
            name="call_owner",
            action=_call_owner,
            description=f"Notify property owner ({owner_phone})",
        )

    return chain
