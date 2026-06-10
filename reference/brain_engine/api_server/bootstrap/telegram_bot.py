"""Lifespan wiring for the Telegram bot client.

The bot client backs both ops communication paths: the polling loop
that receives cleaner photos / commands (`/start`, `/register`,
`/approve`, `/deny`, `/done`) and the approval notifier that pushes
side-effect requests to operators.  When the bot token is missing
those readers see ``None`` and the corresponding feature degrades
silently — endpoints simply skip Telegram delivery rather than the
whole process refusing to start.

The wire entry point is synchronous because :class:`TelegramBot`
only allocates an :class:`httpx.AsyncClient` at construction (no
network I/O until the first request).  The polling-task lifecycle
(``delete_webhook`` + ``create_task``) and the shutdown contract
(``await bot.close()``) still live in ``server.lifespan``: those
depend on module-level handlers and the polling loop coroutine that
read other globals — moving them belongs to a later, broader PR.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from brain_engine.integrations.messaging.telegram_bot import TelegramBot
from config.settings import Settings

logger = logging.getLogger(__name__)


def wire(
    application: FastAPI,
    *,
    settings: Settings,
) -> TelegramBot | None:
    """Construct the Telegram bot client and attach it to app state.

    On success ``application.state.telegram_bot`` is populated so
    that future readers migrated off the module global can resolve
    it through the FastAPI request lifecycle.

    When ``settings.telegram_bot_token`` is empty the section logs a
    warning and returns ``None`` — Telegram-backed endpoints handle
    the ``None`` client and surface "Telegram unavailable" instead
    of 500.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed client.
        settings: The loaded :class:`Settings` instance providing
            the bot token.

    Returns:
        The :class:`TelegramBot` instance, or ``None`` when the
        token is missing.  ``bot.close()`` must be awaited on
        shutdown to release the underlying httpx pool — that
        teardown stays in ``server.lifespan`` for now.
    """
    if not settings.telegram_bot_token:
        logger.warning(
            "Telegram bot token not set — Telegram endpoints "
            "unavailable.",
        )
        return None

    bot = TelegramBot(token=settings.telegram_bot_token)
    application.state.telegram_bot = bot
    logger.info("Telegram bot initialized.")
    return bot
