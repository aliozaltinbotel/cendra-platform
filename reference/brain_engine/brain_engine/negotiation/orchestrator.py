"""Negotiation orchestrator — bounded multi-round bargain loop.

The :class:`Negotiator` drives a deterministic negotiation protocol:

1. Send the engine's opening ask to the counterparty.
2. Await the counterparty's reply and parse it into a
   :class:`NegotiationOffer`.
3. Evaluate the offer against the :class:`NegotiationTarget`:

   * if the offer satisfies price **and** time constraints → ACCEPT;
   * else if rounds remain → COUNTER by re-sending the target as a
     concrete offer;
   * else → REJECT.

4. Record the round and continue until a terminal decision.

Every round is persisted as a vendor-negotiation DecisionCase via the
optional :class:`OpsDecisionLogger`, so the learning subsystem can
derive rules such as *"Acme usually accepts a 5% price bump on
weekdays"* without any bespoke instrumentation.

Channel independence is the key architectural property: the
orchestrator never touches WhatsApp, Telegram or voice APIs directly.
Callers inject two async callables — ``send_offer`` and
``receive_offer`` — that wrap the channel.  This keeps the
orchestrator unit-testable with trivial in-memory doubles and leaves
transport concerns (retries, timeouts, auth) where they belong.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

from brain_engine.negotiation.models import (
    NegotiationDecision,
    NegotiationOffer,
    NegotiationOutcome,
    NegotiationRound,
    NegotiationTarget,
)

if TYPE_CHECKING:
    from brain_engine.patterns.ops_decision_logger import OpsDecisionLogger

logger = logging.getLogger(__name__)

SendOffer = Callable[[NegotiationOffer], Awaitable[None]]
ReceiveOffer = Callable[[], Awaitable[NegotiationOffer]]


class Negotiator:
    """Bounded, channel-agnostic negotiation orchestrator.

    The negotiator is stateless across runs — a fresh instance can be
    used per negotiation, or one instance can be reused sequentially.
    It does not spawn background tasks; callers drive the loop by
    awaiting :meth:`negotiate`.

    Attributes:
        _send: Callable that delivers an offer to the counterparty.
        _receive: Callable that waits for and returns the next
            counter-offer.  Callers are responsible for any timeout
            handling — the orchestrator trusts that ``receive``
            eventually returns or raises.
        _ops_logger: Optional logger.  When present, one vendor-
            negotiation DecisionCase is emitted per round.
    """

    def __init__(
        self,
        *,
        send_offer: SendOffer,
        receive_offer: ReceiveOffer,
        ops_logger: OpsDecisionLogger | None = None,
    ) -> None:
        self._send = send_offer
        self._receive = receive_offer
        self._ops_logger = ops_logger

    async def negotiate(
        self,
        *,
        initial_ask: NegotiationOffer,
        target: NegotiationTarget,
        property_id: str,
        owner_id: str,
        vendor_name: str,
        reservation_id: str | None = None,
    ) -> NegotiationOutcome:
        """Run a bounded negotiation and return its outcome.

        The method sends the opening ask exactly once, then loops up
        to :attr:`NegotiationTarget.max_rounds` times receiving,
        deciding, and (on COUNTER) re-sending the target as a
        concrete offer.  Each round's decision is logged through the
        injected :class:`OpsDecisionLogger` when configured.

        Args:
            initial_ask: The engine's opening offer, delivered before
                the first receive.
            target: Constraints + round cap driving the decisions.
            property_id: Property context for the DecisionCase.
            owner_id: Owner context for the DecisionCase.
            vendor_name: Human-readable counterparty identifier.
            reservation_id: Optional reservation this negotiation is
                attached to.

        Returns:
            A :class:`NegotiationOutcome` summarising every round and
            the terminal decision.  When the initial send fails the
            outcome carries ``accepted=False`` and an empty rounds
            tuple so callers can distinguish transport failure from
            a reached-but-rejected negotiation.
        """
        try:
            await self._send(initial_ask)
        except Exception:
            logger.exception(
                "Negotiation opening send failed (vendor=%s)", vendor_name,
            )
            return NegotiationOutcome(
                accepted=False,
                rounds=(),
                final_offer=None,
                reason="opening_send_failed",
            )

        rounds: list[NegotiationRound] = []
        counter_as_offer = NegotiationOffer(
            time=target.target_time,
            price=target.max_price,
            notes="engine counter: target terms",
        )

        for round_number in range(1, target.max_rounds + 1):
            try:
                counter = await self._receive()
            except Exception:
                logger.exception(
                    "Negotiation receive failed on round %d (vendor=%s)",
                    round_number, vendor_name,
                )
                return NegotiationOutcome(
                    accepted=False,
                    rounds=tuple(rounds),
                    final_offer=None,
                    reason="receive_failed",
                )

            decision, reason = _evaluate_counter(
                counter=counter,
                target=target,
                rounds_remaining=target.max_rounds - round_number,
            )
            round_record = NegotiationRound(
                round_number=round_number,
                counter_offer=counter,
                decision=decision,
                reason=reason,
            )
            rounds.append(round_record)

            await self._log_round(
                decision=decision,
                counter=counter,
                property_id=property_id,
                owner_id=owner_id,
                vendor_name=vendor_name,
                reservation_id=reservation_id,
            )

            if decision is NegotiationDecision.ACCEPT:
                return NegotiationOutcome(
                    accepted=True,
                    rounds=tuple(rounds),
                    final_offer=counter,
                    reason=reason,
                )
            if decision is NegotiationDecision.REJECT:
                return NegotiationOutcome(
                    accepted=False,
                    rounds=tuple(rounds),
                    final_offer=counter,
                    reason=reason,
                )

            # COUNTER — re-send the engine's concrete offer.
            try:
                await self._send(counter_as_offer)
            except Exception:
                logger.exception(
                    "Negotiation counter-send failed on round %d "
                    "(vendor=%s)", round_number, vendor_name,
                )
                return NegotiationOutcome(
                    accepted=False,
                    rounds=tuple(rounds),
                    final_offer=counter,
                    reason="counter_send_failed",
                )

        # Fell off the loop — should be unreachable because the last
        # COUNTER decision is converted to REJECT in
        # :func:`_evaluate_counter` when ``rounds_remaining`` is 0.
        # Keep this branch defensive against future edits.
        last = rounds[-1] if rounds else None
        return NegotiationOutcome(
            accepted=False,
            rounds=tuple(rounds),
            final_offer=last.counter_offer if last else None,
            reason="rounds_exhausted",
        )

    async def _log_round(
        self,
        *,
        decision: NegotiationDecision,
        counter: NegotiationOffer,
        property_id: str,
        owner_id: str,
        vendor_name: str,
        reservation_id: str | None,
    ) -> None:
        """Forward a round's decision to the ops logger when wired.

        Only ACCEPT and REJECT are persisted — a COUNTER is not a
        terminal decision and would pollute the learning signal with
        non-outcomes.  This matches the semantics of
        :meth:`OpsDecisionLogger.log_vendor_negotiation`, which
        encodes ``accepted`` as a boolean.
        """
        if self._ops_logger is None:
            return
        if decision is NegotiationDecision.COUNTER:
            return
        proposal = {
            "time": counter.time,
            "price": counter.price,
            "notes": counter.notes,
        }
        await self._ops_logger.log_vendor_negotiation(
            property_id=property_id,
            owner_id=owner_id,
            reservation_id=reservation_id,
            vendor_name=vendor_name,
            accepted=(decision is NegotiationDecision.ACCEPT),
            proposal=proposal,
        )


# ---------------------------------------------------------------------------
# Decision rules
# ---------------------------------------------------------------------------


def _evaluate_counter(
    *,
    counter: NegotiationOffer,
    target: NegotiationTarget,
    rounds_remaining: int,
) -> tuple[NegotiationDecision, str]:
    """Decide what to do with a counter-offer.

    The rule set is intentionally small and deterministic so the
    learned patterns on top of it remain interpretable:

    * accept if **both** price and time satisfy the target;
    * else counter when rounds remain, rejecting otherwise.

    Args:
        counter: The offer to evaluate.
        target: The engine's constraints.
        rounds_remaining: Rounds left *after* this one is consumed.

    Returns:
        Tuple ``(decision, reason)``.  ``reason`` is a short machine-
        readable token plus optional human-readable tail.
    """
    price_ok = _price_acceptable(counter.price, target.max_price)
    time_ok = _time_acceptable(counter.time, target.target_time)

    if price_ok and time_ok:
        return NegotiationDecision.ACCEPT, "within_constraints"

    mismatch = _mismatch_tag(price_ok=price_ok, time_ok=time_ok)
    if rounds_remaining > 0:
        return NegotiationDecision.COUNTER, f"counter:{mismatch}"
    return NegotiationDecision.REJECT, f"rejected:{mismatch}"


def _price_acceptable(price: float | None, max_price: float | None) -> bool:
    """Return True when ``price`` is within the engine's budget.

    ``max_price=None`` disables the price check entirely.  A missing
    ``price`` on the counter-offer (``None``) counts as unacceptable
    when a budget is set — the engine cannot commit to an unpriced
    job.
    """
    if max_price is None:
        return True
    if price is None:
        return False
    return price <= max_price


def _time_acceptable(offer_time: str, target_time: str) -> bool:
    """Return True when ``offer_time`` matches the target.

    An empty ``target_time`` disables the time check.  String equality
    is sufficient today because offers flow through structured
    channels that already normalise ISO timestamps; richer matching
    (tolerance windows, business-hour snapping) belongs in a follow-up
    and should land behind this seam without changing the caller.
    """
    if not target_time:
        return True
    return offer_time == target_time


def _mismatch_tag(*, price_ok: bool, time_ok: bool) -> str:
    """Compact reason tag describing which constraint failed."""
    if not price_ok and not time_ok:
        return "price_and_time_mismatch"
    if not price_ok:
        return "price_mismatch"
    return "time_mismatch"
