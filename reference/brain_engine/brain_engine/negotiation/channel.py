"""Channel adapter that bridges :class:`Negotiator` to text transports.

The :class:`Negotiator` orchestrator is deliberately transport-agnostic
— it speaks only in :class:`NegotiationOffer` objects and expects
``send_offer`` / ``receive_offer`` callables.  Real deployments need
to plug it into WhatsApp, Telegram or ElevenLabs voice transcripts.

This module provides the plumbing that most of those transports
share:

* :class:`TextNegotiationChannel` — a thin, queue-backed adapter.
  ``send_offer`` formats an offer into text and hands it to a
  channel-specific ``send`` callable; ``receive_offer`` awaits a
  parsed reply that a webhook / polling layer has pushed in through
  :meth:`handle_incoming`.
* :func:`default_format_offer` — a deterministic formatter suitable
  for both WhatsApp and voice (plain text, no markdown).
* :class:`HeuristicReplyParser` — a minimal regex-based parser that
  extracts ISO timestamps and decimal prices from plain replies.
  Production deployments typically plug in the LLM-backed
  :mod:`brain_engine.ops.reply_parser` instead; the heuristic parser
  keeps tests fast and the default path usable offline.

Design notes
------------

The adapter does **not** own the transport.  WhatsApp / voice clients
are constructed elsewhere (usually by the FastAPI lifespan) and
passed in as a callable.  This keeps the adapter free of HTTP / audio
concerns and lets tests exercise it with an in-memory list.

The inbound side is built on :class:`asyncio.Queue` rather than a
condition variable so that bursty replies (a vendor sending three
messages in quick succession) don't lose data — every parsed offer
waits its turn for the orchestrator to consume it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable

from brain_engine.negotiation.models import NegotiationOffer

logger = logging.getLogger(__name__)


SendText = Callable[[str], Awaitable[None]]
FormatOffer = Callable[[NegotiationOffer], str]
ParseReply = Callable[[str], NegotiationOffer]


# ---------------------------------------------------------------------------
# Default formatter
# ---------------------------------------------------------------------------


def default_format_offer(offer: NegotiationOffer) -> str:
    """Render an offer as plain text suitable for any text channel.

    The format is intentionally terse and structured so counterparties
    can reply with "ok" or "counter: $X at T" without ambiguity:

        Proposed time: 2026-05-03T10:00
        Proposed price: 450.00
        Notes: opening offer

    Missing fields are omitted rather than rendered as "None" so the
    message stays readable when the engine opens with an unpriced ask.

    Args:
        offer: The offer to render.

    Returns:
        Human-readable single- or multi-line string.
    """
    lines: list[str] = []
    if offer.time:
        lines.append(f"Proposed time: {offer.time}")
    if offer.price is not None:
        lines.append(f"Proposed price: {offer.price:.2f}")
    if offer.notes:
        lines.append(f"Notes: {offer.notes}")
    if not lines:
        return "Proposal: please confirm availability."
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Heuristic reply parser
# ---------------------------------------------------------------------------


# ISO-8601 date or date+time.  Deliberately conservative — timezone
# suffixes beyond a trailing "Z" are left to the LLM parser.
_ISO_TIME_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?\b",
)

# Decimal number that looks like a price:
#   - optional currency symbol ($, €, £) or ISO code (USD, EUR, TRY);
#   - followed by a number with up to 2 decimals.
_PRICE_RE = re.compile(
    r"(?:[$€£]|\b(?:USD|EUR|TRY|GBP)\b)\s*([0-9]+(?:\.[0-9]{1,2})?)"
    r"|([0-9]+(?:\.[0-9]{1,2})?)\s*(?:[$€£]|USD|EUR|TRY|GBP)\b",
    re.IGNORECASE,
)


class HeuristicReplyParser:
    """Regex-based plain-text reply parser.

    Extracts the first ISO timestamp and the first price-looking
    number from a free-text reply.  The original reply is preserved
    in :attr:`NegotiationOffer.notes` so the audit trail retains the
    full context — regex parsing loses intent (e.g. rejection wording)
    that downstream consumers may need.

    The parser does not attempt rejection detection.  A vendor saying
    "no, thanks" with no price or time simply produces an empty offer,
    which the orchestrator treats as non-matching and either counters
    or rejects per its policy.  Explicit rejection detection belongs
    in the LLM parser.
    """

    def __call__(self, reply: str) -> NegotiationOffer:
        """Parse a reply string into a :class:`NegotiationOffer`.

        Args:
            reply: Raw text from the counterparty.

        Returns:
            Offer populated with any extracted ISO time / price; the
            raw reply is kept in ``notes`` for auditability.
        """
        time_match = _ISO_TIME_RE.search(reply)
        price_match = _PRICE_RE.search(reply)
        price: float | None = None
        if price_match is not None:
            raw_price = price_match.group(1) or price_match.group(2)
            try:
                price = float(raw_price)
            except ValueError:
                logger.debug(
                    "HeuristicReplyParser: price match %r is not a float",
                    raw_price,
                )
                price = None
        return NegotiationOffer(
            time=time_match.group(0) if time_match else "",
            price=price,
            notes=reply.strip(),
        )


# ---------------------------------------------------------------------------
# Channel adapter
# ---------------------------------------------------------------------------


class TextNegotiationChannel:
    """Queue-backed :class:`Negotiator` channel for any text transport.

    The adapter holds one inbound queue.  The outbound side is a
    straight pass-through: ``send_offer`` formats the offer and
    delegates to the injected ``send`` callable.  The inbound side
    requires the transport layer to call :meth:`handle_incoming`
    whenever a reply arrives (webhook, poll, transcript chunk).

    Attributes:
        _send: Channel-specific text sender.
        _format: Callable that renders an offer as text.
        _parse: Callable that parses an inbound text reply.
        _queue: Bounded FIFO of parsed offers awaiting consumption.
    """

    def __init__(
        self,
        *,
        send: SendText,
        format_offer: FormatOffer = default_format_offer,
        parse_reply: ParseReply | None = None,
        queue_maxsize: int = 32,
    ) -> None:
        self._send = send
        self._format = format_offer
        self._parse = parse_reply or HeuristicReplyParser()
        self._queue: asyncio.Queue[NegotiationOffer] = asyncio.Queue(
            maxsize=queue_maxsize,
        )

    async def send_offer(self, offer: NegotiationOffer) -> None:
        """Render and dispatch an offer to the counterparty.

        Args:
            offer: The offer the orchestrator wants to deliver.

        Raises:
            Any exception raised by the injected ``send`` callable is
            propagated unchanged — the orchestrator translates
            transport failures into distinct reason codes and relies
            on exceptions to drive that branching.
        """
        text = self._format(offer)
        await self._send(text)

    async def receive_offer(
        self,
        *,
        timeout: float | None = None,
    ) -> NegotiationOffer:
        """Return the next parsed reply, optionally with a timeout.

        Args:
            timeout: Maximum seconds to wait.  ``None`` waits
                indefinitely, matching the orchestrator's default
                policy of trusting the transport.

        Returns:
            The next :class:`NegotiationOffer` parsed from an inbound
            reply.

        Raises:
            asyncio.TimeoutError: When ``timeout`` is set and no reply
                arrives in that window.
        """
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    async def handle_incoming(self, reply_text: str) -> NegotiationOffer:
        """Accept a raw reply, parse it, and queue it for the orchestrator.

        Designed to be called from a webhook handler or polling loop.
        Returning the parsed offer lets the caller log the parsed view
        alongside the raw text without needing a second parser.

        Args:
            reply_text: Raw text from the counterparty.

        Returns:
            The parsed offer that was enqueued.
        """
        offer = self._parse(reply_text)
        await self._queue.put(offer)
        return offer

    @property
    def pending_replies(self) -> int:
        """Number of parsed replies waiting to be consumed."""
        return self._queue.qsize()
