"""Decision value objects used by the §10 priority chain.

These types are the inputs and outputs of
:class:`brain_engine.orchestrator.priority_chain.ExecutionOrchestrator`.
They sit at a deliberately narrow "glue" layer: the conversation
pipeline (upstream) and the action runners (downstream) only need
to know about :class:`DecisionContext` and :class:`Decision` —
never the priority-chain internals.

Every dataclass is ``frozen=True, slots=True`` so a decision can
travel safely across coroutines and into the case logger without
anyone mutating it underneath.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

__all__ = [
    "DECISION_ACTIONS",
    "EXECUTION_MODES",
    "PRIORITY_TIERS",
    "Decision",
    "DecisionAction",
    "DecisionContext",
    "ExecutionMode",
    "PriorityTier",
]


PriorityTier = Literal[
    "manual",
    "blocker",
    "safety",
    "learned",
    "preference",
    "ask",
]
"""The §10 priority chain, top-down."""


PRIORITY_TIERS: Final[tuple[PriorityTier, ...]] = (
    "manual",
    "blocker",
    "safety",
    "learned",
    "preference",
    "ask",
)
"""Iteration order of the §10 priority chain."""


ExecutionMode = Literal["auto", "ask", "approval", "block"]
"""Runtime mode the orchestrator emits alongside the decision."""


EXECUTION_MODES: Final[tuple[ExecutionMode, ...]] = (
    "auto",
    "ask",
    "approval",
    "block",
)
"""Runtime-iterable view of :data:`ExecutionMode`."""


DecisionAction = Literal[
    "ask",
    "approve",
    "deny",
    "charge",
    "quote",
    "block",
    "escalate",
    "dispatch",
    "fetch_live_data",
]
"""Concrete action the action runner should execute."""


DECISION_ACTIONS: Final[tuple[DecisionAction, ...]] = (
    "ask",
    "approve",
    "deny",
    "charge",
    "quote",
    "block",
    "escalate",
    "dispatch",
    "fetch_live_data",
)
"""Runtime-iterable view of :data:`DecisionAction`."""


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """All inputs the orchestrator needs to choose an action.

    Most fields are optional so the upstream pipeline can populate
    them lazily — the orchestrator skips a tier when its inputs are
    absent rather than hard-failing.

    Attributes:
        scenario: Stable scenario key extracted by the conversation
            pipeline (``"guest_count_mismatch"``,
            ``"discount_request"``, ``"pet_request"`` …).
        property_id: Property identifier (typically
            ``propertyChannelId``) used to scope owner preferences
            and blockers.
        owner_id: Cendra ``ownerId`` of the property owner.
        tenant_id: Cendra workspace id; ``""`` for global.
        reservation_id: Reservation id when the message lives on a
            booking.  ``""`` for pre-booking inquiries.
        guest_id: Guest identifier when known.
        message_text: Original guest message text.
        message_language: BCP-47 language tag of ``message_text``.
        extracted_entities: Structured slots the NER step produced
            (``stated_guest_count``, ``requested_amenity`` …).
        pms_snapshot: Reservation / property PMS state at decision
            time.
        calendar_snapshot: Availability / gap-night data.
        ops_snapshot: Live ops state (vendors, cleaners).
    """

    scenario: str
    property_id: str
    owner_id: str
    tenant_id: str = ""
    reservation_id: str = ""
    guest_id: str = ""
    message_text: str = ""
    message_language: str = ""
    extracted_entities: Mapping[str, Any] = field(default_factory=dict)
    pms_snapshot: Mapping[str, Any] = field(default_factory=dict)
    calendar_snapshot: Mapping[str, Any] = field(default_factory=dict)
    ops_snapshot: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    """The orchestrator's verdict for one :class:`DecisionContext`.

    Attributes:
        action: Concrete :data:`DecisionAction` to execute.
        mode: :data:`ExecutionMode` the action runner should honour.
        tier: :data:`PriorityTier` that produced the decision.
        params: Optional structured parameters for ``action``.
        rationale: Human-readable explanation — surfaced into the
            decision-case log so review tooling can audit which
            rule fired.
    """

    action: DecisionAction
    mode: ExecutionMode
    tier: PriorityTier
    params: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""
