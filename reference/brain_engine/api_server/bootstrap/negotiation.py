"""Lifespan wiring for negotiation: vendor channels + session manager.

The :class:`VendorChannelRegistry` binds to whatever transports
are wired earlier in lifespan (currently Telegram; WhatsApp is
slot-reserved as ``None``).  When a transport is missing, specs
that require it resolve to ``None`` and the session falls back to
record-only mode — no exceptions, no startup abort.

The :class:`NegotiationSessionManager` is a lifespan singleton
that owns per-session lifecycles for vendor outreach (parts /
labour requests).  It depends on:

* ``ops_logger`` — for DecisionCase + outcome capture (R4)
* ``send_resolver`` — the vendor-channel registry built here

Both are constructed in tandem because the manager's
``send_resolver`` argument **is** the registry — splitting them
would force the caller to thread the registry through, which is
exactly the kind of coupling SRP is meant to avoid here.

The shutdown contract still lives in ``server.lifespan``: ``await
manager.close_all()`` flushes per-session state.  Moving that
teardown belongs to a later PR once readers stop reaching the
module globals directly.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from brain_engine.integrations.messaging.telegram_bot import TelegramBot
from brain_engine.negotiation import (
    NegotiationSessionManager,
    VendorChannelRegistry,
)
from brain_engine.patterns.ops_decision_logger import OpsDecisionLogger

logger = logging.getLogger(__name__)


def wire(
    application: FastAPI,
    *,
    telegram_bot: TelegramBot | None,
    ops_logger: OpsDecisionLogger,
) -> tuple[VendorChannelRegistry, NegotiationSessionManager]:
    """Construct the vendor-channel registry and session manager.

    On success ``application.state.vendor_channels`` and
    ``application.state.negotiation_manager`` are populated so that
    future readers migrated off the module globals can resolve them
    through the FastAPI request lifecycle.

    Neither component performs network I/O at construction —
    ``wire`` is therefore synchronous.  The registry merely binds
    references to already-constructed transports; the manager only
    initialises in-memory bookkeeping.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed components.
        telegram_bot: The Telegram client built by
            :func:`api_server.bootstrap.telegram_bot.wire`, or
            ``None`` when the bot token was missing.  When
            ``None``, vendor specs that require Telegram resolve
            to record-only mode.
        ops_logger: The ops DecisionCase logger built by
            :func:`api_server.bootstrap.ops_logger.wire`.  Always
            non-None — the logger handles a missing case_store as
            no-op internally.

    Returns:
        A tuple ``(registry, manager)``.  The manager's
        ``close_all()`` must be awaited on shutdown — that
        teardown stays in ``server.lifespan`` for now.
    """
    registry = VendorChannelRegistry(
        telegram_bot=telegram_bot,
        whatsapp_client=None,
    )
    application.state.vendor_channels = registry

    manager = NegotiationSessionManager(
        ops_logger=ops_logger,
        send_resolver=registry,
    )
    application.state.negotiation_manager = manager

    logger.info(
        "NegotiationSessionManager initialized (telegram=%s, "
        "whatsapp=%s)",
        "yes" if telegram_bot is not None else "no",
        "no",
    )
    return registry, manager
