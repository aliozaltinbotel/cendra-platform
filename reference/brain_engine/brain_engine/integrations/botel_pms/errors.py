"""Exception hierarchy for the Botel/Bookly.Pms MySQL integration.

Every error raised by this subsystem derives from
:class:`BotelPmsError` so callers can catch the whole subsystem
with a single ``except`` clause while still discriminating between
configuration failures (raised eagerly at engine-build time) and
live transport failures (raised at first use).
"""

from __future__ import annotations

from typing import Any

from brain_engine.exceptions import BrainEngineError

__all__ = [
    "BotelPmsConfigError",
    "BotelPmsConnectionError",
    "BotelPmsError",
]


class BotelPmsError(BrainEngineError):
    """Base exception for the Botel/Bookly.Pms MySQL integration."""


class BotelPmsConfigError(BotelPmsError):
    """Required Botel-PMS env vars are missing or malformed.

    Raised synchronously at engine-build time, never during a live
    query.  The ``field`` attribute names the offending variable so
    operators can fix the deployment manifest without diving into
    the stack trace.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
    ) -> None:
        super().__init__(message, code=400, field=field)
        self.field = field


class BotelPmsConnectionError(BotelPmsError):
    """Engine could not establish a session against the PMS server.

    Wraps the underlying SQLAlchemy / asyncmy failure so call sites
    never have to import driver-level exception types.  The original
    error is attached as ``__cause__`` for stack-trace forensics.
    """

    def __init__(
        self,
        message: str,
        *,
        host: str | None = None,
        **context: Any,
    ) -> None:
        super().__init__(message, code=503, host=host, **context)
        self.host = host
