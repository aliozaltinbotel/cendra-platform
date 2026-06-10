"""Command — resume execution with human-provided data.

The Command class encapsulates the human's response to an interrupt
and optionally includes state updates and routing instructions.
Inspired by LangGraph's Command primitive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Command:
    """A command to resume interrupted execution.

    Can carry the human's resume data, state updates, and
    routing instructions (goto next node).

    Attributes:
        resume: The human's response data. This is passed back
            as the return value at the interrupt() call site.
        update: Optional state updates to apply before resuming.
        goto: Optional next node/step to jump to.
        interrupt_id: ID of the interrupt being resolved. If None,
            resolves the most recent pending interrupt.

    Example::

        # Resume with approval
        cmd = Command(resume={"decision": "approve"})

        # Resume with edits and redirect
        cmd = Command(
            resume={"decision": "edit", "new_args": {...}},
            update={"status": "edited"},
            goto="review_step",
        )
    """

    resume: Any = None
    update: dict[str, Any] | None = None
    goto: str | None = None
    interrupt_id: str | None = None

    @property
    def has_resume(self) -> bool:
        """Whether this command carries resume data."""
        return self.resume is not None

    @property
    def has_update(self) -> bool:
        """Whether this command carries state updates."""
        return self.update is not None and len(self.update) > 0

    @property
    def has_goto(self) -> bool:
        """Whether this command specifies a routing target."""
        return self.goto is not None

    def get_decision(self) -> str:
        """Extract the decision from resume data if it's a dict.

        Returns:
            Decision string, or "unknown" if not present.
        """
        if isinstance(self.resume, dict):
            return str(self.resume.get("decision", "unknown"))
        return str(self.resume) if self.resume else "unknown"
