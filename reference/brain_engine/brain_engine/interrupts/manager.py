"""InterruptManager — coordinates interrupt lifecycle.

Manages the full lifecycle of interrupts: creation, checkpoint,
client notification, human response, and resumption. Integrates
with BrainZFS for snapshot-on-interrupt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from brain_engine.interrupts.command import Command
from brain_engine.interrupts.config import InterruptConfig, InterruptPolicy
from brain_engine.interrupts.models import (
    Interrupt,
    InterruptDecision,
    InterruptStatus,
    ResumePayload,
)
from brain_engine.interrupts.primitives import InterruptError

logger = logging.getLogger(__name__)


class InterruptManager:
    """Manages interrupt creation, storage, and resolution.

    Coordinates between the execution engine (which catches
    InterruptError), the client (which presents the interrupt
    to the human), and the resume path (which continues execution).

    Args:
        configs: Dict of tool_name → InterruptConfig.
        zfs: Optional BrainZFS for checkpoint-on-interrupt.
    """

    def __init__(
        self,
        configs: dict[str, InterruptConfig] | None = None,
        zfs: Any | None = None,
    ) -> None:
        self._configs = configs or {}
        self._zfs = zfs
        self._pending: dict[str, Interrupt] = {}
        self._history: list[Interrupt] = []

    @property
    def pending_count(self) -> int:
        """Return the number of pending interrupts."""
        return len(self._pending)

    @property
    def has_pending(self) -> bool:
        """Whether there are any pending interrupts."""
        return len(self._pending) > 0

    # ── Configuration ────────────────────────────────────────────────

    def add_config(self, config: InterruptConfig) -> None:
        """Register an interrupt config for a tool.

        Args:
            config: InterruptConfig to register.
        """
        self._configs[config.tool_name] = config

    def get_config(self, tool_name: str) -> InterruptConfig | None:
        """Get the interrupt config for a tool.

        Args:
            tool_name: Tool name.

        Returns:
            InterruptConfig or None.
        """
        return self._configs.get(tool_name)

    def should_interrupt(
        self,
        tool_name: str,
        tool_args: dict[str, Any] | None = None,
    ) -> bool:
        """Check if a tool call should be interrupted.

        Args:
            tool_name: Name of the tool being called.
            tool_args: Tool arguments.

        Returns:
            True if the tool call should be interrupted.
        """
        config = self._configs.get(tool_name)
        if config is None:
            return False
        return config.should_interrupt(tool_args)

    # ── Interrupt creation ───────────────────────────────────────────

    async def create_interrupt(
        self,
        error: InterruptError,
        session_id: str = "",
    ) -> Interrupt:
        """Create an Interrupt from a caught InterruptError.

        Takes a snapshot if BrainZFS is available.

        Args:
            error: The caught InterruptError.
            session_id: Current session ID.

        Returns:
            Created Interrupt object.
        """
        config = self._configs.get(error.tool_name)
        description = self._format_description(config, error)
        snapshot_name = await self._take_snapshot(session_id, error.interrupt_id)

        intr = Interrupt(
            id=error.interrupt_id,
            value=error.value,
            tool_name=error.tool_name,
            tool_args=error.tool_args,
            session_id=session_id,
            status=InterruptStatus.AWAITING_HUMAN,
            snapshot_name=snapshot_name,
            description=description,
        )
        self._pending[intr.id] = intr
        self._history.append(intr)

        logger.info(
            "Interrupt created: %s for tool %s (session=%s)",
            intr.id[:8], intr.tool_name, session_id,
        )
        return intr

    # ── Resume ───────────────────────────────────────────────────────

    async def resolve(
        self,
        payload: ResumePayload,
    ) -> Command:
        """Resolve a pending interrupt with the human's response.

        Validates the decision against the tool's config and builds
        a Command for the execution engine.

        Args:
            payload: The human's response.

        Returns:
            Command to resume execution.

        Raises:
            KeyError: If the interrupt ID is not found.
            ValueError: If the decision is not allowed.
        """
        intr = self._pending.get(payload.interrupt_id)
        if intr is None:
            msg = f"Interrupt '{payload.interrupt_id}' not found"
            raise KeyError(msg)

        config = self._configs.get(intr.tool_name)
        if config and not config.is_decision_allowed(payload.decision):
            msg = (
                f"Decision '{payload.decision}' not allowed for "
                f"tool '{intr.tool_name}'. "
                f"Allowed: {config.allowed_decisions}"
            )
            raise ValueError(msg)

        intr.status = InterruptStatus.RESUMED
        del self._pending[intr.id]

        resume_data = self._build_resume_data(payload)

        logger.info(
            "Interrupt resolved: %s → %s",
            intr.id[:8], payload.decision.value,
        )
        return Command(
            resume=resume_data,
            interrupt_id=intr.id,
        )

    async def cancel(self, interrupt_id: str) -> bool:
        """Cancel a pending interrupt.

        Args:
            interrupt_id: ID of the interrupt to cancel.

        Returns:
            True if cancelled, False if not found.
        """
        intr = self._pending.pop(interrupt_id, None)
        if intr is None:
            return False
        intr.status = InterruptStatus.CANCELLED
        return True

    # ── Query ────────────────────────────────────────────────────────

    def get_pending(self, interrupt_id: str) -> Interrupt | None:
        """Get a pending interrupt by ID."""
        return self._pending.get(interrupt_id)

    def list_pending(self) -> list[Interrupt]:
        """List all pending interrupts."""
        return list(self._pending.values())

    def get_history(
        self,
        session_id: str | None = None,
    ) -> list[Interrupt]:
        """Get interrupt history, optionally filtered by session.

        Args:
            session_id: Optional session filter.

        Returns:
            List of Interrupt objects.
        """
        if session_id is None:
            return list(self._history)
        return [i for i in self._history if i.session_id == session_id]

    # ── Internal ─────────────────────────────────────────────────────

    def _format_description(
        self,
        config: InterruptConfig | None,
        error: InterruptError,
    ) -> str:
        """Format the interrupt description."""
        if config:
            return config.format_description(error.tool_args)
        return f"Tool '{error.tool_name}' requires approval."

    async def _take_snapshot(
        self,
        session_id: str,
        interrupt_id: str,
    ) -> str:
        """Take a BrainZFS snapshot at the interrupt point."""
        if self._zfs is None:
            return ""
        try:
            snap_name = f"interrupt_{session_id}_{interrupt_id[:8]}"
            await self._zfs.snapshot(snap_name)
            return snap_name
        except Exception as exc:
            logger.warning("Failed to snapshot at interrupt: %s", exc)
            return ""

    def _build_resume_data(self, payload: ResumePayload) -> dict[str, Any]:
        """Build resume data from the payload."""
        return {
            "decision": payload.decision.value,
            "data": payload.data,
            "reason": payload.reason,
            "interrupt_id": payload.interrupt_id,
        }
