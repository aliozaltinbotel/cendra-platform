"""Interrupt/Resume — human-in-the-loop pipeline control.

Inspired by LangGraph's interrupt() + Command(resume=...) pattern.
When a pipeline step requires human approval, it raises PipelineInterrupt.
The pipeline state is checkpointed, and execution pauses.

When the human responds (via /approval/decision), InterruptResume
loads the checkpoint and resumes from the interrupted step.

Usage:
    # In pipeline step:
    if requires_approval:
        raise PipelineInterrupt(
            reason="Cost exceeds threshold",
            data={"cost": 150, "threshold": 100},
        )

    # Resume after approval:
    manager = InterruptResume(checkpointer)
    state = await manager.resume(pipeline_id, resume_data={"approved": True})
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from brain_engine.durability.checkpointer import (
    PipelineCheckpointer,
    PipelineState,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineInterrupt(Exception):
    """Raised when pipeline needs human input to continue.

    Attributes:
        reason: Why the pipeline was interrupted.
        data: Context data for the human reviewer.
        interrupt_id: Unique identifier for this interrupt.
    """

    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    interrupt_id: str = ""


class InterruptResume:
    """Manages pipeline interruption and resumption.

    Coordinates with PipelineCheckpointer to save state
    when interrupted and restore it when resumed.

    Args:
        checkpointer: Pipeline state persistence layer.
    """

    def __init__(self, checkpointer: PipelineCheckpointer) -> None:
        self._checkpointer = checkpointer

    async def interrupt(
        self,
        state: PipelineState,
        reason: str,
        data: dict[str, Any] | None = None,
    ) -> PipelineState:
        """Interrupt pipeline and save state for later resumption.

        Args:
            state: Current pipeline state at interruption point.
            reason: Human-readable reason for interruption.
            data: Context data for the reviewer.

        Returns:
            Updated PipelineState with interrupted status.
        """
        state.metadata["interrupt_data"] = data or {}
        return await self._checkpointer.mark_interrupted(state, reason)

    async def resume(
        self,
        pipeline_id: str,
        resume_data: dict[str, Any] | None = None,
    ) -> PipelineState | None:
        """Resume an interrupted pipeline with human decision.

        Loads the interrupted state, applies the resume data,
        and marks the pipeline as ready to continue.

        Args:
            pipeline_id: Pipeline execution identifier.
            resume_data: Human decision data (e.g., approved=True).

        Returns:
            Updated PipelineState ready for continuation, or None.
        """
        state = await self._checkpointer.load(pipeline_id)
        if state is None:
            logger.warning("Pipeline %s not found for resume", pipeline_id)
            return None

        if not self._is_resumable(state):
            logger.warning(
                "Pipeline %s status=%s, cannot resume",
                pipeline_id,
                state.status,
            )
            return None

        return await self._apply_resume(state, resume_data or {})

    async def get_pending_interrupts(
        self,
        thread_id: str,
    ) -> PipelineState | None:
        """Get interrupted pipeline for a thread (if any).

        Args:
            thread_id: Conversation/request thread identifier.

        Returns:
            Interrupted PipelineState, or None.
        """
        state = await self._checkpointer.load_by_thread(thread_id)
        if state is None or state.status != "interrupted":
            return None
        return state

    @staticmethod
    def _is_resumable(state: PipelineState) -> bool:
        """Check if pipeline state allows resumption.

        Args:
            state: Pipeline state to check.

        Returns:
            True if state is interrupted and can be resumed.
        """
        return state.status == "interrupted"

    async def _apply_resume(
        self,
        state: PipelineState,
        resume_data: dict[str, Any],
    ) -> PipelineState:
        """Apply resume data and mark pipeline as in_progress.

        Args:
            state: Interrupted pipeline state.
            resume_data: Human decision data.

        Returns:
            Updated PipelineState ready for execution.
        """
        state.metadata["resume_data"] = resume_data
        state.metadata.pop("interrupt_reason", None)
        state.status = "in_progress"

        from brain_engine.durability.checkpointer import _now_iso
        state.updated_at = _now_iso()

        await self._checkpointer.save(state)

        logger.info(
            "Pipeline %s resumed at step %d",
            state.pipeline_id,
            state.current_step,
        )
        return state
