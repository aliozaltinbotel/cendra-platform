"""Vendor channel registry — resolve vendor names to ``SendText`` callables.

The :class:`NegotiationSessionManager` talks to a single transport
seam: a ``send`` callable that takes a string and awaits its delivery.
This module is the default resolver that turns a vendor name into that
callable by consulting a small per-vendor mapping.

Supported channels
------------------

* ``telegram``  — message is delivered via an injected
  :class:`~brain_engine.integrations.messaging.telegram_bot.TelegramBot`.
  The spec's ``address`` is the Telegram chat id (stored as a string so
  the same VendorChannelSpec shape serves every channel).
* ``whatsapp``  — delivered via
  :class:`~brain_engine.integrations.messaging.whatsapp.WhatsAppClient`.
  The ``address`` is the recipient phone number in E.164 format.
* ``log``       — writes every outbound to the module logger.  Useful
  for demos and for vendors whose real channel has not been connected
  yet; the negotiation still runs, you just see outbound text in the
  pod logs instead of on the counterparty's phone.

Design notes
------------

The registry is intentionally in-memory and process-local.  A pod
restart loses registrations; this matches the operational posture of
the session manager itself, where in-flight sessions are lost on crash
and the DecisionCase store holds the authoritative outcome record.

The registry implements the :type:`SendResolver` protocol by exposing
``__call__(vendor_name) -> SendText | None``, so it can be handed
straight to :class:`NegotiationSessionManager` without an adapter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from brain_engine.integrations.messaging.telegram_bot import TelegramBot
    from brain_engine.integrations.messaging.whatsapp import WhatsAppClient

logger = logging.getLogger(__name__)


SendText = Callable[[str], Awaitable[None]]


_SUPPORTED_CHANNELS = frozenset({"telegram", "whatsapp", "log"})


@dataclass(frozen=True, slots=True)
class VendorChannelSpec:
    """How to reach one counterparty.

    Attributes:
        channel: Transport tag — one of ``telegram``, ``whatsapp``,
            ``log``.  Normalised to lower-case by :meth:`register`.
        address: Channel-specific recipient identifier.  Telegram
            uses chat id (numeric, stored as a string for uniformity),
            WhatsApp uses an E.164 phone number, ``log`` ignores the
            field entirely.
    """

    channel: str
    address: str


class VendorChannelRegistry:
    """In-memory registry of vendor → transport mappings.

    The registry does not own the underlying transport clients; they
    are injected at construction time and their lifecycle is managed
    by the caller (typically the FastAPI lifespan).  A registry built
    without a transport will reject ``__call__`` for specs that
    require it, logging a warning rather than raising — a missing
    transport is an operational issue, not a programming bug, and the
    negotiation can fall back to record-only mode.
    """

    def __init__(
        self,
        *,
        telegram_bot: "TelegramBot | None" = None,
        whatsapp_client: "WhatsAppClient | None" = None,
    ) -> None:
        self._telegram_bot = telegram_bot
        self._whatsapp_client = whatsapp_client
        self._specs: dict[str, VendorChannelSpec] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(
        self,
        vendor_name: str,
        *,
        channel: str,
        address: str = "",
    ) -> VendorChannelSpec:
        """Record or update a vendor's channel.

        Args:
            vendor_name: Human-readable vendor identifier — must match
                the name passed to
                :meth:`NegotiationSessionManager.start`.
            channel: One of ``telegram``, ``whatsapp``, ``log``.  Case
                is normalised.
            address: Channel-specific recipient.  Required for
                ``telegram`` and ``whatsapp``; ignored for ``log``.

        Returns:
            The stored :class:`VendorChannelSpec`.

        Raises:
            ValueError: On unknown channel, empty vendor name, or a
                non-``log`` channel with an empty address.
        """
        if not vendor_name:
            raise ValueError("vendor_name must not be empty")
        normalised = channel.strip().lower()
        if normalised not in _SUPPORTED_CHANNELS:
            raise ValueError(
                f"unsupported channel: {channel!r} "
                f"(expected one of {sorted(_SUPPORTED_CHANNELS)})"
            )
        if normalised != "log" and not address:
            raise ValueError(
                f"{normalised} channel requires an address",
            )
        spec = VendorChannelSpec(channel=normalised, address=address)
        self._specs[vendor_name] = spec
        logger.info(
            "Vendor channel registered: %s -> %s:%s",
            vendor_name, normalised, address or "<n/a>",
        )
        return spec

    def unregister(self, vendor_name: str) -> bool:
        """Drop a vendor from the registry.  Returns True on hit."""
        existed = self._specs.pop(vendor_name, None) is not None
        if existed:
            logger.info("Vendor channel removed: %s", vendor_name)
        return existed

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, vendor_name: str) -> VendorChannelSpec | None:
        """Return the stored spec, or ``None`` when unknown."""
        return self._specs.get(vendor_name)

    def known_vendors(self) -> list[str]:
        """Return a sorted snapshot of the registered vendor names."""
        return sorted(self._specs)

    # ------------------------------------------------------------------
    # SendResolver protocol
    # ------------------------------------------------------------------

    def __call__(self, vendor_name: str) -> SendText | None:
        """Resolve a vendor to a :type:`SendText` callable.

        Returns ``None`` when the vendor has no spec or when the spec
        references a transport that was not wired at construction.
        """
        spec = self._specs.get(vendor_name)
        if spec is None:
            return None
        if spec.channel == "log":
            return _make_log_sender(vendor_name)
        if spec.channel == "telegram":
            if self._telegram_bot is None:
                logger.warning(
                    "Vendor %s wants telegram but no bot is wired",
                    vendor_name,
                )
                return None
            return _make_telegram_sender(
                self._telegram_bot, spec.address,
            )
        if spec.channel == "whatsapp":
            if self._whatsapp_client is None:
                logger.warning(
                    "Vendor %s wants whatsapp but no client is wired",
                    vendor_name,
                )
                return None
            return _make_whatsapp_sender(
                self._whatsapp_client, spec.address,
            )
        # Unreachable — register() already rejects unknown channels.
        return None


# ---------------------------------------------------------------------------
# Sender factories — pinned to one recipient each
# ---------------------------------------------------------------------------


def _make_log_sender(vendor_name: str) -> SendText:
    """Return a ``SendText`` that writes outbound text to the logger."""

    async def send(text: str) -> None:
        logger.info("[negotiation|log] to=%s | %s", vendor_name, text)

    return send


def _make_telegram_sender(
    bot: "TelegramBot", chat_id: str,
) -> SendText:
    """Bind a :class:`TelegramBot` to one chat id as a ``SendText``."""

    async def send(text: str) -> None:
        await bot.send_message(chat_id=chat_id, text=text)

    return send


def _make_whatsapp_sender(
    client: "WhatsAppClient", phone: str,
) -> SendText:
    """Bind a :class:`WhatsAppClient` to one phone as a ``SendText``."""

    async def send(text: str) -> None:
        await client.send_message(phone=phone, text=text)

    return send
