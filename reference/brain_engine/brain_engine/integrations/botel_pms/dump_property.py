"""Property-scoped data dump to a single chronological CSV.

Pulls every row tied to one ``property_id`` over a configurable
window (default: 6 months) and emits one CSV that interleaves
thread headers, individual messages, AI / manual tasks, and
reservations sorted by event time.  The CSV is the canonical input
for the past-conversation core analyser; downstream notebooks can
re-hydrate the full upstream row from the ``raw_json`` column.

The join chain mirrors the Bookly.Pms schema exactly:

.. code-block:: text

    MessageHeader.property_id == X
        ↳ MessageHeader.id == MessageItem.message_id   (thread)
    Task.property_id == X
        ↳ Task.message_id == MessageHeader.id          (cross-ref)
    Booking.property_id == X                           (direct)

CLI::

    python -m brain_engine.integrations.botel_pms.dump_property \\
        --property-id "channel:123/property:abc" \\
        --months 6 \\
        --output ./property_dump.csv

Programmatic::

    from brain_engine.integrations.botel_pms.dump_property import (
        dump_property,
    )
    counts = await dump_property(
        property_id="channel:123/property:abc",
        since=datetime.now(tz=UTC) - timedelta(days=180),
        output_path=Path("./property_dump.csv"),
    )

Notes:
    * Bookings filter by ``created_at`` so a 6-month window
      surfaces "reservations booked in the last 6 months", not
      "reservations whose stay falls in the next 6 months".
    * Tasks are fetched only when ``property_id`` parses as a UUID
      because Bookly.Pms stores ``Task.PropertyId`` as a Guid while
      Booking / MessageHeader use a string column.
    * Per-table fetch is capped at :data:`MAX_RECENT_LIMIT`; the
      script logs a warning whenever the cap is hit so callers can
      narrow the window or paginate.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import structlog

from brain_engine.integrations.botel_pms.engine import (
    dispose_engine,
    get_session,
)
from brain_engine.integrations.botel_pms.readers import (
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
    "dump_property",
    "main",
]


logger = structlog.get_logger(__name__)


_DEFAULT_MONTHS: Final[int] = 6
# Conservative month-to-day approximation; the dump window is
# coarse-grained by design (analysts re-filter the CSV downstream).
_DAYS_PER_MONTH: Final[int] = 30
_CSV_FIELDS: Final[tuple[str, ...]] = (
    "event_time",
    "source",
    "id",
    "property_id",
    "thread_id",
    "booking_id",
    "title",
    "body",
    "status",
    "sentiment",
    "channel",
    "sender_or_actor",
    "amount",
    "check_in",
    "check_out",
    "is_deleted",
    "created_at",
    "raw_json",
)


# ── Row builders ────────────────────────────────────────────────────


def _json_default(obj: Any) -> Any:
    # ``date`` covers ``datetime`` too — DATE columns
    # (BookingCheckIn / BookingCheckOut) come back as plain
    # ``datetime.date``, not ``datetime``.  ``timedelta`` covers
    # MySQL ``TIME`` columns (Task.EstimatedTime), which asyncmy
    # surfaces as :class:`datetime.timedelta`.
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return str(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"not JSON-serialisable: {type(obj)!r}")


def _iso(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def _dump_raw(record: Any) -> str:
    """Serialise a frozen reader record to JSON for the CSV."""
    return json.dumps(
        asdict(record),
        default=_json_default,
        ensure_ascii=False,
    )


def _row_from_header(h: MessageHeaderRecord) -> dict[str, Any]:
    # CreatedAt is a real DATETIME column upstream while
    # LastMessageReceivedAt is a varchar string serialised by
    # Pomelo .NET — prefer the proper datetime for sortable
    # timeline ordering, fall back to the string when missing.
    if h.created_at is not None:
        event_time = h.created_at.isoformat()
    else:
        event_time = h.last_message_received_at or ""
    return {
        "event_time": event_time,
        "source": "header",
        "id": str(h.id),
        "property_id": h.property_id or "",
        "thread_id": str(h.id),
        "booking_id": str(h.booking_id) if h.booking_id else "",
        "title": h.title or "",
        "body": (
            f"closed={h.is_closed}, "
            f"messages={h.message_count}, "
            f"language={h.response_language or ''}"
        ),
        "status": h.ai_reply_status or "",
        "sentiment": (
            "" if h.sentiment is None else str(h.sentiment)
        ),
        "channel": h.provider or h.provider_pms or "",
        "sender_or_actor": h.assigned_user or "",
        "amount": "",
        "check_in": _iso(h.booking_check_in),
        "check_out": _iso(h.booking_check_out),
        "is_deleted": str(h.is_deleted),
        "created_at": h.created_at.isoformat(),
        "raw_json": _dump_raw(h),
    }


def _row_from_message(
    m: MessageItemRecord,
    *,
    header: MessageHeaderRecord,
) -> dict[str, Any]:
    return {
        "event_time": m.created_at.isoformat(),
        "source": "message",
        "id": str(m.id),
        "property_id": header.property_id or "",
        "thread_id": str(m.message_id),
        "booking_id": (
            str(header.booking_id) if header.booking_id else ""
        ),
        "title": m.message_type or "",
        "body": m.message or "",
        "status": m.ai_mode or "",
        "sentiment": (
            "" if m.sentiment is None else str(m.sentiment)
        ),
        "channel": m.communication_type or "",
        "sender_or_actor": m.sender or m.created_by_name or "",
        "amount": "",
        "check_in": "",
        "check_out": "",
        "is_deleted": str(m.is_deleted),
        "created_at": m.created_at.isoformat(),
        "raw_json": _dump_raw(m),
    }


def _row_from_task(t: TaskRecord) -> dict[str, Any]:
    return {
        "event_time": t.created_at.isoformat(),
        "source": "task",
        "id": str(t.id),
        "property_id": str(t.property_id),
        "thread_id": str(t.message_id) if t.message_id else "",
        "booking_id": "",
        "title": t.title or "",
        "body": t.description or "",
        "status": t.status or "",
        "sentiment": (
            "" if t.sentiment is None else str(t.sentiment)
        ),
        "channel": t.ai_mode or "",
        "sender_or_actor": t.created_by or "",
        "amount": (
            "" if t.hourly_rate is None else str(t.hourly_rate)
        ),
        "check_in": "",
        "check_out": "",
        "is_deleted": str(t.is_deleted),
        "created_at": t.created_at.isoformat(),
        "raw_json": _dump_raw(t),
    }


def _row_from_booking(b: BookingRecord) -> dict[str, Any]:
    return {
        "event_time": b.created_at.isoformat(),
        "source": "booking",
        "id": str(b.id),
        "property_id": b.property_id,
        "thread_id": "",
        "booking_id": str(b.id),
        "title": b.unique_id or b.channel_booking_id or "",
        "body": b.notes or b.host_notes or "",
        "status": b.status or "",
        "sentiment": "",
        "channel": b.channel_code or b.ota_name or "",
        "sender_or_actor": b.created_by or "",
        "amount": "" if b.amount is None else str(b.amount),
        "check_in": _iso(b.check_in_date),
        "check_out": _iso(b.check_out_date),
        "is_deleted": str(b.is_deleted),
        "created_at": b.created_at.isoformat(),
        "raw_json": _dump_raw(b),
    }


# ── Orchestration ───────────────────────────────────────────────────


async def dump_property(
    *,
    property_id: str,
    since: datetime,
    output_path: Path,
    include_deleted: bool = False,
    include_playground: bool = False,
) -> dict[str, int]:
    """Run the full property-scoped join and write the CSV.

    Args:
        property_id: Upstream property handle.  Strings work
            against MessageHeader / Booking; UUID-shaped values
            additionally feed the Task lookup.
        since: Inclusive lower bound on every fetched row's
            ``created_at``.
        output_path: Destination CSV path.  Parent directories
            are created as needed.
        include_deleted: Pass ``True`` to keep tombstoned rows.
        include_playground: Pass ``True`` to keep
            ``IsPlayground = 1`` rows.

    Returns:
        Per-source row counters (``header``, ``message``,
        ``task``, ``booking``).  Useful for smoke-testing pipeline
        integrations.

    Raises:
        BotelPmsConnectionError: On driver-level failure.
        BotelPmsConfigError: On missing connection configuration.
    """
    counts = {"header": 0, "message": 0, "task": 0, "booking": 0}
    rows: list[dict[str, Any]] = []

    async with get_session() as session:
        header_reader = MessageHeaderReader(session)
        msg_reader = MessageItemReader(session)
        task_reader = TaskReader(session)
        booking_reader = BookingReader(session)

        # 1. Thread headers anchor the property → conversation map.
        headers = await header_reader.list_by_property_id(
            property_id,
            since=since,
            include_deleted=include_deleted,
            include_playground=include_playground,
            limit=MAX_RECENT_LIMIT,
        )
        if len(headers) >= MAX_RECENT_LIMIT:
            logger.warning(
                "botel_pms.dump.headers.cap_hit",
                property_id=property_id,
                cap=MAX_RECENT_LIMIT,
            )
        for h in headers:
            rows.append(_row_from_header(h))
        counts["header"] = len(headers)

        # 2. Walk each thread to capture individual messages.
        for h in headers:
            thread = await msg_reader.list_thread(
                h.id, include_deleted=include_deleted,
            )
            for m in thread:
                if m.created_at < since:
                    continue
                rows.append(_row_from_message(m, header=h))
                counts["message"] += 1

        # 3. Tasks — only when property_id parses as a Guid.
        try:
            prop_uuid = uuid.UUID(property_id)
        except ValueError:
            logger.warning(
                "botel_pms.dump.tasks.skipped",
                property_id=property_id,
                reason="property_id is not a UUID",
            )
        else:
            tasks = await task_reader.list_by_property_id(
                prop_uuid,
                include_deleted=include_deleted,
                limit=MAX_RECENT_LIMIT,
            )
            if len(tasks) >= MAX_RECENT_LIMIT:
                logger.warning(
                    "botel_pms.dump.tasks.cap_hit",
                    property_id=str(prop_uuid),
                    cap=MAX_RECENT_LIMIT,
                )
            for t in tasks:
                if t.created_at < since:
                    continue
                rows.append(_row_from_task(t))
                counts["task"] += 1

        # 4. Bookings — direct property_id match.
        bookings = await booking_reader.list_by_property_id(
            property_id,
            include_deleted=include_deleted,
            include_playground=include_playground,
            limit=MAX_RECENT_LIMIT,
        )
        if len(bookings) >= MAX_RECENT_LIMIT:
            logger.warning(
                "botel_pms.dump.bookings.cap_hit",
                property_id=property_id,
                cap=MAX_RECENT_LIMIT,
            )
        for b in bookings:
            if b.created_at < since:
                continue
            rows.append(_row_from_booking(b))
            counts["booking"] += 1

    rows.sort(key=lambda r: r["event_time"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(
        "botel_pms.dump.complete",
        property_id=property_id,
        output=str(output_path),
        total=sum(counts.values()),
        **counts,
    )
    return counts


# ── CLI ─────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dump_property",
        description=(
            "Dump every Bookly.Pms row tied to one property over "
            "the last N months as a single chronological CSV."
        ),
    )
    parser.add_argument(
        "--property-id",
        required=True,
        help=(
            "Upstream property handle.  Use the same string the "
            "MessageHeader / Booking tables store; a UUID-shaped "
            "value additionally enables the Task lookup."
        ),
    )
    parser.add_argument(
        "--months",
        type=int,
        default=_DEFAULT_MONTHS,
        help=(
            f"Window size in months (default: {_DEFAULT_MONTHS}). "
            "Filters every fetched row's created_at."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "CSV output path.  Defaults to "
            "./botel_pms_dump_<id>_<since>.csv in the current "
            "working directory."
        ),
    )
    parser.add_argument(
        "--include-deleted",
        action="store_true",
        help="Include rows with is_deleted=True.",
    )
    parser.add_argument(
        "--include-playground",
        action="store_true",
        help="Include rows with is_playground=True.",
    )
    return parser.parse_args(argv)


def _default_output_path(
    property_id: str, since: datetime
) -> Path:
    safe = "".join(
        c if c.isalnum() else "_" for c in property_id
    )[:80]
    stamp = since.strftime("%Y%m%d")
    return Path.cwd() / f"botel_pms_dump_{safe}_{stamp}.csv"


async def _amain(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.months < 1:
        print("--months must be >= 1", file=sys.stderr)
        return 2

    # The Bookly.Pms schema stores DATETIME columns as naive UTC;
    # comparing them to an aware datetime raises TypeError, so we
    # compute the window cut-off in naive UTC to match the DB.
    since = (
        datetime.now(timezone.utc) - timedelta(
            days=args.months * _DAYS_PER_MONTH
        )
    ).replace(tzinfo=None)
    output_path: Path = (
        args.output
        if args.output is not None
        else _default_output_path(args.property_id, since)
    )

    try:
        counts = await dump_property(
            property_id=args.property_id,
            since=since,
            output_path=output_path,
            include_deleted=args.include_deleted,
            include_playground=args.include_playground,
        )
    finally:
        await dispose_engine()

    print(
        f"wrote {sum(counts.values())} rows to {output_path} "
        f"(headers={counts['header']}, "
        f"messages={counts['message']}, "
        f"tasks={counts['task']}, "
        f"bookings={counts['booking']})",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Synchronous entry point for ``python -m`` invocation.

    On Windows the default ``ProactorEventLoop`` triggers a
    ``WinError 87`` inside asyncmy's SSL handshake (the MySQL
    driver registers its socket with IOCP before the TLS layer is
    fully up).  Switching to ``SelectorEventLoop`` here keeps the
    fix scoped to the CLI entry point — library callers running
    inside FastAPI / Temporal workers retain their own loop.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
