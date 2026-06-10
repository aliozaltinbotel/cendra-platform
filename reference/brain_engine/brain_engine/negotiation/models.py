"""Data models for the negotiation subsystem.

Value objects shared between the :class:`Negotiator` orchestrator and
its callers.  All models are frozen dataclasses with ``slots=True`` so
they are hashable, cheaply copied, and cannot drift into partially
initialised states mid-round.

Design notes
------------

* :class:`NegotiationOffer` is symmetric — it represents both the
  engine's ask and the counterparty's counter-offer.  The orchestrator
  never needs to distinguish them by type; context comes from whose
  turn produced the offer.
* :class:`NegotiationTarget` carries the engine's constraints and the
  ``max_rounds`` cap.  Keeping policy on its own object means that a
  rule-based decision engine can plug in without touching the
  orchestrator.
* :class:`NegotiationRound` is the audit trail unit.  Each round in
  :class:`NegotiationOutcome.rounds` is the full record of "vendor
  said X, we decided Y, because Z" — suitable for downstream pattern
  extraction through :class:`OpsDecisionLogger`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NegotiationDecision(StrEnum):
    """How the engine reacted to a counter-offer.

    ``ACCEPT`` closes the negotiation positively; ``REJECT`` closes it
    negatively.  ``COUNTER`` keeps the loop open for another round,
    subject to :attr:`NegotiationTarget.max_rounds`.
    """

    ACCEPT = "accept"
    COUNTER = "counter"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class NegotiationOffer:
    """A single offer — the engine's ask or the counterparty's reply.

    Attributes:
        time: ISO-8601 timestamp the work is proposed for.  Empty
            string means "unspecified"; the orchestrator treats empty
            as "any time acceptable".
        price: Proposed total price in the property's currency.
            ``None`` means "no price attached yet" (e.g. the engine's
            opening ask may not carry a price).
        notes: Free-text annotation that rides along with the offer —
            kept in the audit trail but not used for decisions.
    """

    time: str = ""
    price: float | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class NegotiationTarget:
    """Constraints the engine is willing to settle for.

    Attributes:
        target_time: Desired ISO timestamp.  Empty string disables
            time checking so negotiations that care only about price
            remain ergonomic.
        max_price: Upper bound on price.  ``None`` disables the price
            check.  A counter whose price is at or below this bound
            satisfies the price constraint.
        max_rounds: Maximum number of counter-offers to accept before
            rejecting.  Must be at least 1 — a zero-round negotiation
            would mean "send and walk away" which is not a negotiation.
    """

    target_time: str = ""
    max_price: float | None = None
    max_rounds: int = 3

    def __post_init__(self) -> None:
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")


@dataclass(frozen=True, slots=True)
class NegotiationRound:
    """One round in a negotiation — one counter-offer + one decision.

    A round is closed (immutable) so the outcome's ``rounds`` tuple
    can be safely shared with the logger and any downstream audit.

    Attributes:
        round_number: 1-based index within the negotiation.
        counter_offer: The offer received from the counterparty.
        decision: What the engine decided to do with the offer.
        reason: Short human-readable justification — used by the
            pattern extractor as a feature and by ops dashboards as
            an explanation.
    """

    round_number: int
    counter_offer: NegotiationOffer
    decision: NegotiationDecision
    reason: str = ""


@dataclass(frozen=True, slots=True)
class NegotiationOutcome:
    """Final outcome of a bounded negotiation.

    Attributes:
        accepted: Whether the negotiation ended in an accepted offer.
        rounds: Full ordered audit trail of every round that was
            evaluated.  Always non-empty for a completed negotiation;
            empty only when the initial ask could not be delivered
            (transport failure).
        final_offer: The offer associated with the closing decision —
            the accepted one on success, the last rejected counter on
            failure.  ``None`` on transport-failure path.
        reason: Terminal reason string.  Mirrors
            :attr:`NegotiationRound.reason` of the closing round.
    """

    accepted: bool
    rounds: tuple[NegotiationRound, ...] = field(default_factory=tuple)
    final_offer: NegotiationOffer | None = None
    reason: str = ""
