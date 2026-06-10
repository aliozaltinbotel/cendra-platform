"""Botel/Bookly.Pms MySQL integration.

Connection-layer facade over the shared Azure MySQL server that
backs the CORA inbox stack.  The Botel PMS database is the
canonical home of ``MessageItem``, ``Conversation`` and the rest of
the inbox schema owned by the .NET Bookly.Pms service; this package
exposes the async SQLAlchemy engine, session factory, and a small
health-check so downstream analytics modules (e.g. the
``past_conversation`` core analysis) can read those tables without
re-implementing connection handling.

The package is intentionally read-only by convention — no ORM
metadata is ever pushed against this database, since the upstream
schema is owned elsewhere.

Configuration is env-driven; see ``config/.env.example`` for the
``BOTEL_MYSQL_*`` variables.
"""

from __future__ import annotations

from brain_engine.integrations.botel_pms.engine import (
    build_botel_pms_url,
    dispose_engine,
    get_engine,
    get_session,
    get_session_factory,
    ping,
)
from brain_engine.integrations.botel_pms.errors import (
    BotelPmsConfigError,
    BotelPmsConnectionError,
    BotelPmsError,
)
from brain_engine.integrations.botel_pms.models import (
    Booking,
    BotelPmsBase,
    MessageHeader,
    MessageItem,
    Task,
)
from brain_engine.integrations.botel_pms.readers import (
    DEFAULT_RECENT_LIMIT,
    MAX_RECENT_LIMIT,
    BookingReader,
    BookingRecord,
    MessageHeaderReader,
    MessageHeaderRecord,
    MessageItemReader,
    MessageItemRecord,
    TaskReader,
    TaskRecord,
)

__all__ = [
    "Booking",
    "BookingReader",
    "BookingRecord",
    "BotelPmsBase",
    "BotelPmsConfigError",
    "BotelPmsConnectionError",
    "BotelPmsError",
    "DEFAULT_RECENT_LIMIT",
    "MAX_RECENT_LIMIT",
    "MessageHeader",
    "MessageHeaderReader",
    "MessageHeaderRecord",
    "MessageItem",
    "MessageItemReader",
    "MessageItemRecord",
    "Task",
    "TaskReader",
    "TaskRecord",
    "build_botel_pms_url",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_session_factory",
    "ping",
]
