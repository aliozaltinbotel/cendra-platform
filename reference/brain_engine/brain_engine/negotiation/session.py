"""In-flight negotiation session manager.

The :class:`Negotiator` runs one bounded negotiation from opening ask
to terminal decision in a single ``await`` call.  HTTP callers cannot
hold that call open across many minutes — replies from vendors arrive
via webhooks long after the request that started the negotiation has
returned.  This module closes that gap with a session-oriented façade:

* :meth:`NegotiationSessionManager.start` launches the negotiator in a
  background task, wired to a fresh :class:`TextNegotiationChannel`,
  and returns a session id immediately.
* :meth:`NegotiationSessionManager.feed_reply` lets webhook / polling
  handlers push raw counterparty text into the session, which the
  channel parses and forwards to the running negotiator.
* :meth:`NegotiationSessionManager.status` reports the session state
  (``running`` / ``completed`` / ``cancelled`` / ``error``), including
  the outbox, queue size, and terminal outcome once available.

The manager is intentionally lightweight: it holds sessions in a plain
dict and does not persist them.  A crash loses active sessions — this
is acceptable because a negotiation is a best-effort ops helper, and
the authoritative record of ACCEPT / REJECT outcomes is already in the
DecisionCase store via :class:`OpsDecisionLogger`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from brain_engine.negotiation.channel import TextNegotiationChannel
from brain_engine.negotiation.models import (
    NegotiationOffer,
    NegotiationOutcome,
    NegotiationTarget,
)
from brain_engine.negotiation.orchestrator import Negotiator

if TYPE_CHECKING:
    from brain_engine.patterns.ops_decision_logger import OpsDecisionLogger

logger = logging.getLogger(__name__)


SendText = Callable[[str], Awaitable[None]]
SendResolver = Callable[[str], "SendText | None"]


@dataclass
class NegotiationSession:
    """Mutable record of one in-flight negotiation.

    Attributes:
        session_id: Opaque identifier returned to the caller.
        vendor_name: Counterparty name, propagated into DecisionCases.
        property_id: Property context for the negotiation.
        owner_id: Owner context for the negotiation.
        reservation_id: Reservation the negotiation is attached to,
            if any.
        created_at: ISO timestamp of session creation — useful for
            dashboards and stale-session cleanup.
        channel: The channel adapter this session writes to / reads
            from.  Exposed so webhook layers can call
            :meth:`TextNegotiationChannel.handle_incoming` through
            :meth:`NegotiationSessionManager.feed_reply`.
        task: The background task running
            :meth:`Negotiator.negotiate`.  When done, its result is
            the :class:`NegotiationOutcome`.
        sent_messages: Ordered list of every outbound text that has
            been dispatched through the channel.  Primarily for
            tests, demos, and ops dashboards.
    """

    session_id: str
    vendor_name: str
    property_id: str
    owner_id: str
    reservation_id: str | None
    created_at: str
    channel: TextNegotiationChannel
    task: "asyncio.Task[NegotiationOutcome]"
    sent_messages: list[str]

    def is_done(self) -> bool:
        """Return True when the underlying negotiation task has ended."""
        return self.task.done()

    def outcome(self) -> NegotiationOutcome | None:
        """Return the terminal outcome, or ``None`` while still running.

        The method swallows cancellation and task-level exceptions so
        status queries never raise.  Use :meth:`error` to inspect
        failures.
        """
        if not self.task.done() or self.task.cancelled():
            return None
        if self.task.exception() is not None:
            return None
        return self.task.result()

    def error(self) -> BaseException | None:
        """Return a task-level exception if the negotiation crashed."""
        if not self.task.done() or self.task.cancelled():
            return None
        return self.task.exception()


class NegotiationSessionManager:
    """Track in-flight negotiation sessions keyed by session id.

    The manager is a lifespan-scoped singleton.  It does not own the
    :class:`OpsDecisionLogger`; callers inject one when they want
    ACCEPT / REJECT rounds persisted as DecisionCases.
    """

    def __init__(
        self,
        *,
        ops_logger: OpsDecisionLogger | None = None,
        send_resolver: SendResolver | None = None,
    ) -> None:
        self._ops_logger = ops_logger
        self._send_resolver = send_resolver
        self._sessions: dict[str, NegotiationSession] = {}

    async def start(
        self,
        *,
        vendor_name: str,
        property_id: str,
        owner_id: str,
        initial_ask: NegotiationOffer,
        target: NegotiationTarget,
        send: SendText | None = None,
        reservation_id: str | None = None,
    ) -> str:
        """Launch a new negotiation in the background.

        Args:
            vendor_name: Counterparty name for logging / audit.
            property_id: Property the negotiation is about.
            owner_id: Property owner — required for scope-keyed
                learning via :class:`OpsDecisionLogger`.
            initial_ask: Opening offer delivered before the first
                reply is awaited.
            target: Engine constraints driving ACCEPT / COUNTER /
                REJECT decisions.
            send: Channel-specific text sender.  When ``None`` the
                manager consults its ``send_resolver`` (if configured)
                to look up a sender for ``vendor_name``; if that also
                returns ``None`` the session runs in record-only mode
                — outbound messages are captured in
                :attr:`NegotiationSession.sent_messages` but never
                leave the process.  This keeps the endpoint safe to
                call before a real transport is wired.
            reservation_id: Optional reservation this negotiation is
                tied to.

        Returns:
            Opaque session id the caller can use with
            :meth:`feed_reply` and :meth:`status`.
        """
        session_id = f"NEG-{uuid.uuid4().hex[:12].upper()}"
        if send is None and self._send_resolver is not None:
            send = self._send_resolver(vendor_name)
        sent: list[str] = []

        async def recording_send(text: str) -> None:
            sent.append(text)
            if send is not None:
                await send(text)

        channel = TextNegotiationChannel(send=recording_send)
        negotiator = Negotiator(
            send_offer=channel.send_offer,
            receive_offer=channel.receive_offer,
            ops_logger=self._ops_logger,
        )
        task = asyncio.create_task(
            negotiator.negotiate(
                initial_ask=initial_ask,
                target=target,
                property_id=property_id,
                owner_id=owner_id,
                vendor_name=vendor_name,
                reservation_id=reservation_id,
            ),
            name=f"negotiation:{session_id}",
        )
        session = NegotiationSession(
            session_id=session_id,
            vendor_name=vendor_name,
            property_id=property_id,
            owner_id=owner_id,
            reservation_id=reservation_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            channel=channel,
            task=task,
            sent_messages=sent,
        )
        self._sessions[session_id] = session

        # Yield once so the negotiator has a chance to dispatch the
        # opening ask before start() returns.  Callers that inspect
        # ``sent_messages`` immediately after start then see the
        # opening text, which matches HTTP-caller expectations.
        await asyncio.sleep(0)

        logger.info(
            "Negotiation session started: id=%s vendor=%s property=%s",
            session_id, vendor_name, property_id,
        )
        return session_id

    async def feed_reply(
        self,
        session_id: str,
        reply_text: str,
    ) -> NegotiationOffer:
        """Push a raw counterparty reply into an active session.

        Args:
            session_id: Session to feed.
            reply_text: Verbatim counterparty text.

        Returns:
            The parsed :class:`NegotiationOffer` that was enqueued on
            the session's channel.

        Raises:
            KeyError: ``session_id`` is unknown.
            RuntimeError: The negotiation has already terminated — the
                reply would never be consumed.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if session.is_done():
            raise RuntimeError(
                f"Session {session_id} already completed; "
                "reply not accepted"
            )
        return await session.channel.handle_incoming(reply_text)

    def status(self, session_id: str) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of a session.

        The result is a plain dict so endpoints can return it verbatim.
        Pydantic models would buy no extra safety given the endpoint
        returns a ``dict[str, Any]`` response shape anyway.

        Raises:
            KeyError: ``session_id`` is unknown.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)

        state = _classify_state(session)
        snapshot: dict[str, Any] = {
            "session_id": session.session_id,
            "vendor_name": session.vendor_name,
            "property_id": session.property_id,
            "owner_id": session.owner_id,
            "reservation_id": session.reservation_id,
            "created_at": session.created_at,
            "status": state,
            "outbox_count": len(session.sent_messages),
            "sent_messages": list(session.sent_messages),
            "pending_replies": session.channel.pending_replies,
            "outcome": None,
            "error": None,
        }
        outcome = session.outcome()
        if outcome is not None:
            snapshot["outcome"] = _outcome_to_dict(outcome)
        err = session.error()
        if err is not None:
            snapshot["error"] = repr(err)
        return snapshot

    def get(self, session_id: str) -> NegotiationSession | None:
        """Return the raw session, or ``None`` if unknown."""
        return self._sessions.get(session_id)

    async def cancel(self, session_id: str) -> None:
        """Cancel an active session; no-op when session is done or absent."""
        session = self._sessions.get(session_id)
        if session is None or session.is_done():
            return
        session.task.cancel()
        try:
            await session.task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "Negotiation task raised on cancel (session=%s)",
                session_id,
            )

    async def close_all(self) -> None:
        """Cancel every active session — called from lifespan shutdown."""
        for session_id in list(self._sessions):
            await self.cancel(session_id)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _classify_state(session: NegotiationSession) -> str:
    """Translate task state to a public-facing status string."""
    if not session.task.done():
        return "running"
    if session.task.cancelled():
        return "cancelled"
    if session.task.exception() is not None:
        return "error"
    return "completed"


def _outcome_to_dict(outcome: NegotiationOutcome) -> dict[str, Any]:
    """Convert a :class:`NegotiationOutcome` to a JSON-friendly dict."""
    return {
        "accepted": outcome.accepted,
        "reason": outcome.reason,
        "final_offer": (
            _offer_to_dict(outcome.final_offer)
            if outcome.final_offer is not None
            else None
        ),
        "rounds": [
            {
                "round_number": r.round_number,
                "decision": r.decision.value,
                "reason": r.reason,
                "counter_offer": _offer_to_dict(r.counter_offer),
            }
            for r in outcome.rounds
        ],
    }


def _offer_to_dict(offer: NegotiationOffer) -> dict[str, Any]:
    """Convert a :class:`NegotiationOffer` to a plain dict."""
    return {
        "time": offer.time,
        "price": offer.price,
        "notes": offer.notes,
    }
