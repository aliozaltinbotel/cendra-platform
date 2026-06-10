"""Custom exception hierarchy for the Brain Engine.

All Brain Engine exceptions inherit from BrainEngineError.
Each subsystem has its own error class for targeted handling.

Usage:
    try:
        result = await gateway.request_approval(...)
    except ApprovalTimeoutError:
        # Handle timeout specifically
    except ApprovalError:
        # Handle any approval error
    except BrainEngineError:
        # Handle any Brain Engine error
"""

from __future__ import annotations

from typing import Any


class BrainEngineError(Exception):
    """Base exception for all Brain Engine errors."""

    def __init__(self, message: str, code: int = 0, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.context = context

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.args[0]!r}, code={self.code})"


# ── Approval System ──────────────────────────────────────────────────────


class ApprovalError(BrainEngineError):
    """Base exception for approval gateway errors."""


class ApprovalNotFoundError(ApprovalError):
    """Raised when an approval request is not found."""

    def __init__(self, request_id: str) -> None:
        super().__init__(
            f"Approval request '{request_id}' not found",
            code=404,
            request_id=request_id,
        )
        self.request_id = request_id


class ApprovalTimeoutError(ApprovalError):
    """Raised when an approval request times out."""

    def __init__(self, request_id: str, timeout_seconds: int) -> None:
        super().__init__(
            f"Approval request '{request_id}' timed out after {timeout_seconds}s",
            code=408,
            request_id=request_id,
            timeout_seconds=timeout_seconds,
        )
        self.request_id = request_id
        self.timeout_seconds = timeout_seconds


class InvalidActionTypeError(ApprovalError):
    """Raised when an invalid action type is provided."""

    def __init__(self, action_type: str, valid_types: list[str]) -> None:
        super().__init__(
            f"Invalid action type '{action_type}'. Valid: {valid_types}",
            code=400,
            action_type=action_type,
        )


# ── Preferences ──────────────────────────────────────────────────────────


class PreferenceError(BrainEngineError):
    """Base exception for preference engine errors."""


class RuleNotFoundError(PreferenceError):
    """Raised when a preference rule is not found."""

    def __init__(self, rule_id: str) -> None:
        super().__init__(
            f"Preference rule '{rule_id}' not found",
            code=404,
            rule_id=rule_id,
        )


class InvalidScopeError(PreferenceError):
    """Raised when an invalid rule scope is provided."""

    def __init__(self, scope: str) -> None:
        super().__init__(f"Invalid scope '{scope}'", code=400, scope=scope)


# ── Fallback ─────────────────────────────────────────────────────────────


class FallbackError(BrainEngineError):
    """Base exception for fallback system errors."""


class GapResolutionError(FallbackError):
    """Raised when a gap cannot be resolved after exhausting all steps."""

    def __init__(self, gap_type: str, steps_tried: int) -> None:
        super().__init__(
            f"Gap '{gap_type}' unresolved after {steps_tried} steps",
            code=503,
            gap_type=gap_type,
            steps_tried=steps_tried,
        )


class ConfigValidationError(FallbackError):
    """Raised when flow configuration validation fails."""

    def __init__(self, flow_type: str, gaps: list[str]) -> None:
        super().__init__(
            f"Config validation failed for '{flow_type}': {', '.join(gaps)}",
            code=400,
            flow_type=flow_type,
            gaps=gaps,
        )


# ── Guest Intelligence ───────────────────────────────────────────────────


class GuestIntelligenceError(BrainEngineError):
    """Base exception for guest intelligence errors."""


class GuestNotFoundError(GuestIntelligenceError):
    """Raised when a guest profile cannot be found or built."""

    def __init__(self, guest_id: str) -> None:
        super().__init__(
            f"Guest '{guest_id}' not found",
            code=404,
            guest_id=guest_id,
        )


# ── Guardrails ───────────────────────────────────────────────────────────


class GuardrailError(BrainEngineError):
    """Base exception for guardrail errors."""


class ContradictionDetectedError(GuardrailError):
    """Raised when a contradiction is detected and blocks execution."""

    def __init__(self, explanation: str, layer: int = 0) -> None:
        super().__init__(
            f"Contradiction detected at layer {layer}: {explanation}",
            code=409,
            layer=layer,
        )


class HallucinationDetectedError(GuardrailError):
    """Raised when a hallucination is detected in agent output."""

    def __init__(self, warnings: list[str]) -> None:
        super().__init__(
            f"Hallucination detected: {'; '.join(warnings[:3])}",
            code=422,
            warnings=warnings,
        )


# ── Memory ───────────────────────────────────────────────────────────────


class MemoryError(BrainEngineError):
    """Base exception for memory system errors."""


class MemoryConnectionError(MemoryError):
    """Raised when Redis/Qdrant connection fails."""

    def __init__(self, backend: str, url: str) -> None:
        super().__init__(
            f"Cannot connect to {backend} at {url}",
            code=503,
            backend=backend,
            url=url,
        )
