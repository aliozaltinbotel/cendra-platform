"""Interrupts module — human-in-the-loop with resumable execution.

Provides the ability to pause agent execution at defined points,
send a value to the client for human review, and resume with the
human's response. Integrates with BrainZFS for checkpoint-on-interrupt.

Components:
    - interrupt(): Pause execution and return a value to the client.
    - Command: Resume execution with human-provided data.
    - InterruptConfig: Per-tool interrupt configuration.
    - InterruptManager: Coordinates interrupt lifecycle.
"""

from brain_engine.interrupts.command import Command
from brain_engine.interrupts.config import InterruptConfig, InterruptPolicy
from brain_engine.interrupts.manager import InterruptManager
from brain_engine.interrupts.models import (
    Interrupt,
    InterruptDecision,
    InterruptStatus,
    ResumePayload,
)
from brain_engine.interrupts.primitives import InterruptError, interrupt

__all__ = [
    "Command",
    "Interrupt",
    "InterruptConfig",
    "InterruptDecision",
    "InterruptError",
    "InterruptManager",
    "InterruptPolicy",
    "InterruptStatus",
    "ResumePayload",
    "Command",
    "interrupt",
]
