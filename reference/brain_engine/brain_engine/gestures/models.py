"""Value objects for memory prompts and pattern gestures.

Cendra V2 surfaces two kinds of micro-UI elements on every decision
card:

- :class:`MemoryPrompt` — a short, user-facing hint pulled from memory
  (preferences, warnings, past incidents, live facts).  Prompts are
  *context decorators* — they justify or qualify an upcoming action
  without requiring a separate screen.
- :class:`PatternGesture` — a one-tap suggestion derived from a
  matched :class:`~brain_engine.patterns.models.PatternRule`.  Each
  gesture carries its execution mode, confidence, risk, and Undo
  reversibility so the UI can render the right affordance.

:class:`GesturePack` bundles the two for a given
:class:`GestureContext` and is what the API hands to the mobile client.

All types are frozen + slots so packs can be safely cached and
compared.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from brain_engine.cards.models import ReversibilityTier
from brain_engine.patterns.models import (
    DecisionAction,
    RiskLevel,
    Scenario,
)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class MemoryPromptKind(StrEnum):
    """Semantic role a prompt plays on the card."""

    PREFERENCE = "preference"
    WARNING = "warning"
    HISTORY = "history"
    CONTEXT = "context"
    BLOCKER = "blocker"


class MemorySource(StrEnum):
    """Which memory store the prompt was derived from."""

    CUSTOMER_MEMORY = "customer_memory"
    GUEST_HISTORY = "guest_history"
    FACTS = "facts"
    PATTERNS = "patterns"
    PMS = "pms"
    MANUAL = "manual"


class GestureMode(StrEnum):
    """How the UI should render a :class:`PatternGesture`.

    - ``ONE_TAP`` — execute on first tap (AUTO-mode rules).
    - ``CONFIRM`` — short "Are you sure?" affordance.
    - ``APPROVAL_REQUIRED`` — routes through an approval flow.
    - ``BLOCKED`` — shown for context only; never executable.
    """

    ONE_TAP = "one_tap"
    CONFIRM = "confirm"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_MAX_PROMPT_TEXT: int = 160


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


def _normalise_text(text: str) -> str:
    """Strip whitespace and cap prompt length."""
    cleaned = " ".join(text.split())
    if len(cleaned) > _MAX_PROMPT_TEXT:
        return cleaned[: _MAX_PROMPT_TEXT - 1].rstrip() + "\u2026"
    return cleaned


# ---------------------------------------------------------------------------
# Memory prompts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GestureContext:
    """Addresses the slot for which prompts/gestures are assembled."""

    property_id: str
    scenario: Scenario
    guest_id: str | None = None
    owner_id: str | None = None
    features: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryPrompt:
    """A short decorating hint surfaced on a decision card.

    ``relevance`` is a caller-computed score in ``[0.0, 1.0]`` that lets
    the aggregator rank prompts from multiple sources.  ``reference_id``
    links back to the memory record so the UI can open detail views.
    """

    kind: MemoryPromptKind
    source: MemorySource
    text: str
    relevance: float = 0.5
    prompt_id: str = field(default_factory=_new_id)
    reference_id: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate + normalise in-place via object.__setattr__ since
        # the dataclass is frozen.
        if not self.text.strip():
            raise ValueError("MemoryPrompt.text must not be empty")
        clamped = max(0.0, min(1.0, float(self.relevance)))
        object.__setattr__(self, "relevance", clamped)
        object.__setattr__(self, "text", _normalise_text(self.text))

    @property
    def is_urgent(self) -> bool:
        """Warnings and blockers need to be visually elevated."""
        return self.kind in {
            MemoryPromptKind.WARNING,
            MemoryPromptKind.BLOCKER,
        }


# ---------------------------------------------------------------------------
# Pattern gestures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatternGesture:
    """One-tap suggestion surfaced from a matched :class:`PatternRule`.

    The combination of :class:`GestureMode` and :class:`ReversibilityTier`
    controls the UI affordance: a ``ONE_TAP`` + ``GREEN`` gesture shows a
    single button with an undo snackbar; an ``APPROVAL_REQUIRED`` gesture
    always routes through a review step regardless of reversibility.
    """

    label: str
    pattern_id: str
    scenario: Scenario
    action: DecisionAction
    mode: GestureMode
    confidence: float
    risk_level: RiskLevel
    reversibility: ReversibilityTier
    gesture_id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        """Whether the UI should render an active button."""
        return self.mode is not GestureMode.BLOCKED


# ---------------------------------------------------------------------------
# GesturePack
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GesturePack:
    """Bundle of prompts + gestures for a single :class:`GestureContext`."""

    context: GestureContext
    prompts: tuple[MemoryPrompt, ...] = ()
    gestures: tuple[PatternGesture, ...] = ()
    assembled_at: datetime = field(default_factory=_utc_now)

    @property
    def top_gesture(self) -> PatternGesture | None:
        """First actionable gesture, preserving builder sort order."""
        for g in self.gestures:
            if g.is_actionable:
                return g
        return None

    @property
    def has_one_tap(self) -> bool:
        """Whether any gesture is ready for one-tap execution."""
        return any(g.mode is GestureMode.ONE_TAP for g in self.gestures)

    @property
    def urgent_prompts(self) -> tuple[MemoryPrompt, ...]:
        """Prompts flagged as warnings or blockers."""
        return tuple(p for p in self.prompts if p.is_urgent)
