"""Env-driven settings for the missing-info extractor.

Pulled into a focused module so ``missing_info_extractor.py`` stays
thin and so the operator-tunable knobs are listed in one place an
SRE can grep for.  Read on every call — flipping the env value does
not require a pod bounce.
"""

from __future__ import annotations

import logging
import os

__all__ = [
    "GUEST_MSG_MAX_TOKENS_DEFAULT",
    "GUEST_MSG_MAX_TOKENS_ENV",
    "guest_message_token_budget",
]


logger = logging.getLogger(__name__)


GUEST_MSG_MAX_TOKENS_ENV = "BRAIN_EXTRACTOR_GUEST_MSG_MAX_TOKENS"
# Token-equivalent (at the project-wide ~4 chars/token estimate) of
# the legacy ``content[:300]`` cap that lived inline in
# ``missing_info_extractor._last_guest_message``.  Keeping the
# default at 75 preserves prompt budget for prod traffic until the
# operator chooses to widen or narrow it via the env override.
GUEST_MSG_MAX_TOKENS_DEFAULT = 75


def guest_message_token_budget() -> int:
    """Return the per-call token cap for the latest guest message.

    Reads :data:`GUEST_MSG_MAX_TOKENS_ENV` and falls back to
    :data:`GUEST_MSG_MAX_TOKENS_DEFAULT` on missing / blank /
    non-integer / non-positive values.  Bad operator input is
    logged at WARN so a typo surfaces in deploy review without
    crashing the conversation pipeline.
    """
    raw = os.environ.get(GUEST_MSG_MAX_TOKENS_ENV, "").strip()
    if not raw:
        return GUEST_MSG_MAX_TOKENS_DEFAULT
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not an int -- using default %d",
            GUEST_MSG_MAX_TOKENS_ENV,
            raw,
            GUEST_MSG_MAX_TOKENS_DEFAULT,
        )
        return GUEST_MSG_MAX_TOKENS_DEFAULT
    if parsed <= 0:
        logger.warning(
            "%s=%d is non-positive -- using default %d",
            GUEST_MSG_MAX_TOKENS_ENV,
            parsed,
            GUEST_MSG_MAX_TOKENS_DEFAULT,
        )
        return GUEST_MSG_MAX_TOKENS_DEFAULT
    return parsed
