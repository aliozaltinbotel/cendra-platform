"""Read-side adapters for the Botel/Bookly.Pms inbox tables.

Each reader owns one foreign entity and returns small frozen
dataclasses so downstream Brain Engine modules (e.g. the
``past_conversation`` core analyser) can depend on a stable shape
without reaching into raw SQLAlchemy rows.

Design notes:

- All methods filter ``is_deleted == False`` by default, matching
  the upstream soft-delete convention (see Bookly.Pms CLAUDE.md).
  Callers that need tombstoned rows for forensic analysis pass
  ``include_deleted=True`` explicitly.
- Driver / connection failures raised by the engine layer are
  wrapped in :class:`BotelPmsConnectionError` so the calling layer
  never needs to import :mod:`sqlalchemy.exc`.
- Readers never mutate the session, the engine, or any cache; this
  package is read-only by convention.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Optional

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from brain_engine.integrations.botel_pms.errors import (
    BotelPmsConnectionError,
)
from brain_engine.integrations.botel_pms.models import (
    Booking,
    MessageHeader,
    MessageItem,
    Task,
)

__all__ = [
    "BookingReader",
    "BookingRecord",
    "DEFAULT_RECENT_LIMIT",
    "MAX_RECENT_LIMIT",
    "MessageHeaderReader",
    "MessageHeaderRecord",
    "MessageItemReader",
    "MessageItemRecord",
    "TaskReader",
    "TaskRecord",
]


logger = structlog.get_logger(__name__)


DEFAULT_RECENT_LIMIT: Final[int] = 100
MAX_RECENT_LIMIT: Final[int] = 1000


@dataclass(frozen=True, slots=True)
class MessageItemRecord:
    """Frozen, framework-agnostic snapshot of one ``messageitem`` row.

    The fields mirror :class:`MessageItem` but exclude SQLAlchemy
    machinery so this object is safe to cache, pickle, or hand to
    LLM-facing layers without leaking the ORM.
    """

    id: uuid.UUID
    message_id: uuid.UUID
    message: str | None
    sender: str | None
    created_by_name: str | None
    is_need_attention: bool
    message_type: str | None
    communication_type: str | None
    send_by_ai: bool
    ai_tag: str | None
    sources: str | None
    sentiment: int | None
    was_helpful: int | None
    was_helpful_completeness: str | None
    tasks: str | None
    ai_mode: str | None
    provider_message_id: str | None
    email_metadata: Any | None
    created_at: datetime
    modified_at: datetime | None
    created_by: str
    modified_by: str | None
    is_deleted: bool

    @classmethod
    def from_orm(cls, row: MessageItem) -> "MessageItemRecord":
        """Project a hydrated :class:`MessageItem` into a frozen record."""
        return cls(
            id=row.id,
            message_id=row.message_id,
            message=row.message,
            sender=row.sender,
            created_by_name=row.created_by_name,
            is_need_attention=row.is_need_attention,
            message_type=row.message_type,
            communication_type=row.communication_type,
            send_by_ai=row.send_by_ai,
            ai_tag=row.ai_tag,
            sources=row.sources,
            sentiment=row.sentiment,
            was_helpful=row.was_helpful,
            was_helpful_completeness=row.was_helpful_completeness,
            tasks=row.tasks,
            ai_mode=row.ai_mode,
            provider_message_id=row.provider_message_id,
            email_metadata=row.email_metadata,
            created_at=row.created_at,
            modified_at=row.modified_at,
            created_by=row.created_by,
            modified_by=row.modified_by,
            is_deleted=row.is_deleted,
        )


class MessageItemReader:
    """Read-only adapter over the ``messageitem`` table.

    The reader does not own the session — callers pass an
    :class:`AsyncSession` from
    :func:`brain_engine.integrations.botel_pms.engine.get_session`
    so connection scoping stays under the consumer's control.

    Usage::

        async with get_session() as session:
            reader = MessageItemReader(session)
            thread = await reader.list_thread(message_id)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Single-row lookups ──────────────────────────────────────────
    async def get_by_id(
        self,
        item_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Optional[MessageItemRecord]:
        """Fetch one row by primary key.

        Args:
            item_id: ``MessageItem.Id`` (BaseEntity primary key).
            include_deleted: Set ``True`` to bypass the soft-delete
                filter; default returns ``None`` for tombstoned rows.

        Returns:
            Frozen record, or ``None`` if no live row matches.

        Raises:
            BotelPmsConnectionError: Driver-level failure.
        """
        stmt = select(MessageItem).where(MessageItem.id == item_id)
        if not include_deleted:
            stmt = stmt.where(MessageItem.is_deleted.is_(False))
        row = await self._scalar_one_or_none(stmt)
        return MessageItemRecord.from_orm(row) if row else None

    async def get_by_provider_message_id(
        self,
        provider_message_id: str,
        *,
        include_deleted: bool = False,
    ) -> Optional[MessageItemRecord]:
        """Fetch one row by upstream provider message identifier.

        Backed by ``IX_MessageItem_ProviderMessageId``, so this is
        the cheap path for reconciling against an external mailbox
        / channel feed.
        """
        stmt = select(MessageItem).where(
            MessageItem.provider_message_id == provider_message_id
        )
        if not include_deleted:
            stmt = stmt.where(MessageItem.is_deleted.is_(False))
        row = await self._scalar_one_or_none(stmt)
        return MessageItemRecord.from_orm(row) if row else None

    # ── Thread / multi-row reads ────────────────────────────────────
    async def list_thread(
        self,
        message_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> list[MessageItemRecord]:
        """Return every row that shares ``message_id``, oldest first.

        ``message_id`` is the upstream conversation grouping key, so
        this is the canonical query for past-conversation analysis.

        Args:
            message_id: Thread / grouping key.
            include_deleted: Set ``True`` to include tombstoned rows.

        Returns:
            Records ordered by ``created_at`` ascending.  Empty list
            when no rows match.

        Raises:
            BotelPmsConnectionError: Driver-level failure.
        """
        stmt = (
            select(MessageItem)
            .where(MessageItem.message_id == message_id)
            .order_by(MessageItem.created_at.asc())
        )
        if not include_deleted:
            stmt = stmt.where(MessageItem.is_deleted.is_(False))
        rows = await self._scalars_all(stmt)
        return [MessageItemRecord.from_orm(r) for r in rows]

    async def list_recent(
        self,
        *,
        limit: int = DEFAULT_RECENT_LIMIT,
        since: datetime | None = None,
        sender: str | None = None,
        include_deleted: bool = False,
    ) -> list[MessageItemRecord]:
        """Return the most recent rows, newest first.

        Args:
            limit: Maximum rows to fetch; clamped to
                :data:`MAX_RECENT_LIMIT`.
            since: Only rows with ``created_at >= since``.  Naive vs
                aware timestamps are forwarded verbatim — the caller
                owns the timezone contract.
            sender: Optional case-sensitive ``sender`` filter.
            include_deleted: Set ``True`` to include tombstoned rows.

        Returns:
            Records ordered by ``created_at`` descending.

        Raises:
            BotelPmsConnectionError: Driver-level failure.
        """
        clamped = max(1, min(limit, MAX_RECENT_LIMIT))
        stmt = (
            select(MessageItem)
            .order_by(MessageItem.created_at.desc())
            .limit(clamped)
        )
        if not include_deleted:
            stmt = stmt.where(MessageItem.is_deleted.is_(False))
        if since is not None:
            stmt = stmt.where(MessageItem.created_at >= since)
        if sender is not None:
            stmt = stmt.where(MessageItem.sender == sender)
        rows = await self._scalars_all(stmt)
        return [MessageItemRecord.from_orm(r) for r in rows]

    async def count_thread(
        self,
        message_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> int:
        """Return the row count for one thread.

        Cheaper than :meth:`list_thread` when callers only need the
        size — e.g. analysis pipelines deciding whether to summarise
        or skip a conversation.
        """
        stmt = select(func.count()).select_from(MessageItem).where(
            MessageItem.message_id == message_id
        )
        if not include_deleted:
            stmt = stmt.where(MessageItem.is_deleted.is_(False))
        try:
            result = await self._session.execute(stmt)
            return int(result.scalar_one())
        except SQLAlchemyError as exc:
            raise self._wrap("count_thread", exc) from exc

    # ── Internal helpers ────────────────────────────────────────────
    async def _scalar_one_or_none(
        self, stmt: Any
    ) -> MessageItem | None:
        return await _scalar_one_or_none(self._session, stmt)

    async def _scalars_all(
        self, stmt: Any
    ) -> Sequence[MessageItem]:
        return await _scalars_all(self._session, stmt)

    @staticmethod
    def _wrap(
        operation: str, exc: SQLAlchemyError
    ) -> BotelPmsConnectionError:
        return _wrap_driver_error(operation, exc)


# ── MessageHeader ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MessageHeaderRecord:
    """Frozen snapshot of one ``messageheader`` row.

    Mirrors :class:`MessageHeader` but excludes SQLAlchemy machinery
    so this object is safe to cache, pickle, or hand to LLM-facing
    layers without leaking the ORM.  Notably the ``id`` is the
    thread key shared with :class:`MessageItem.message_id`.
    """

    id: uuid.UUID
    title: str | None
    provider: str | None
    is_closed: int
    last_message_received_at: str | None
    message_count: int
    property_id: str | None
    property_name: str | None
    booking_id: uuid.UUID | None
    booking_status: str | None
    booking_occupancy: int | None
    booking_check_in: datetime | None
    booking_check_out: datetime | None
    source: str | None
    ai_reply_status: str | None
    assigned_user: str | None
    is_need_attention: bool
    view_at: datetime
    from_sync: bool
    customer_id: str | None
    parent_id: uuid.UUID | None
    is_playground: bool
    customer_ai_id: str | None
    sentiment: int | None
    response_language: str | None
    provider_pms: str | None
    created_at: datetime
    modified_at: datetime | None
    created_by: str
    modified_by: str | None
    is_deleted: bool

    @classmethod
    def from_orm(cls, row: MessageHeader) -> "MessageHeaderRecord":
        """Project a hydrated :class:`MessageHeader` into a record."""
        return cls(
            id=row.id,
            title=row.title,
            provider=row.provider,
            is_closed=row.is_closed,
            last_message_received_at=row.last_message_received_at,
            message_count=row.message_count,
            property_id=row.property_id,
            property_name=row.property_name,
            booking_id=row.booking_id,
            booking_status=row.booking_status,
            booking_occupancy=row.booking_occupancy,
            booking_check_in=row.booking_check_in,
            booking_check_out=row.booking_check_out,
            source=row.source,
            ai_reply_status=row.ai_reply_status,
            assigned_user=row.assigned_user,
            is_need_attention=row.is_need_attention,
            view_at=row.view_at,
            from_sync=row.from_sync,
            customer_id=row.customer_id,
            parent_id=row.parent_id,
            is_playground=row.is_playground,
            customer_ai_id=row.customer_ai_id,
            sentiment=row.sentiment,
            response_language=row.response_language,
            provider_pms=row.provider_pms,
            created_at=row.created_at,
            modified_at=row.modified_at,
            created_by=row.created_by,
            modified_by=row.modified_by,
            is_deleted=row.is_deleted,
        )


class MessageHeaderReader:
    """Read-only adapter over the ``messageheader`` table.

    The header is the canonical bridge from a property handle to
    the threads it owns — ``MessageItem`` itself carries no
    ``property_id``, so a property-scoped conversation sweep starts
    here, then walks the thread via
    :meth:`MessageItemReader.list_thread`.

    Default filters mirror the upstream conventions: tombstoned
    rows (``is_deleted == True``) and ``IsPlayground = 1`` rows are
    excluded unless the caller opts in explicitly.

    Usage::

        async with get_session() as session:
            headers = await MessageHeaderReader(session).list_by_property_id(
                "channel:123/property:abc",
                since=six_months_ago,
            )
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        thread_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        include_playground: bool = False,
    ) -> Optional[MessageHeaderRecord]:
        """Fetch one header by primary key (= thread key)."""
        stmt = select(MessageHeader).where(
            MessageHeader.id == thread_id
        )
        if not include_deleted:
            stmt = stmt.where(MessageHeader.is_deleted.is_(False))
        if not include_playground:
            stmt = stmt.where(
                MessageHeader.is_playground.is_(False)
            )
        row = await _scalar_one_or_none(self._session, stmt)
        return MessageHeaderRecord.from_orm(row) if row else None

    async def list_by_property_id(
        self,
        property_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        include_deleted: bool = False,
        include_playground: bool = False,
        limit: int = DEFAULT_RECENT_LIMIT,
    ) -> list[MessageHeaderRecord]:
        """Return thread headers for one property, newest first.

        Args:
            property_id: Upstream property handle (string — the
                column accepts OTA channel ids).
            since: Inclusive lower bound on
                ``last_message_received_at`` so the dump window
                tracks actual conversational activity rather than
                administrative metadata.
            until: Inclusive upper bound on
                ``last_message_received_at``.
            include_deleted: Include tombstoned rows.
            include_playground: Include ``IsPlayground = 1`` rows.
            limit: Max rows; clamped to :data:`MAX_RECENT_LIMIT`.

        Returns:
            Records ordered by ``last_message_received_at``
            descending — most-recently-active threads come first.
        """
        clamped = max(1, min(limit, MAX_RECENT_LIMIT))
        stmt = (
            select(MessageHeader)
            .where(MessageHeader.property_id == property_id)
            .order_by(
                MessageHeader.last_message_received_at.desc()
            )
            .limit(clamped)
        )
        if not include_deleted:
            stmt = stmt.where(MessageHeader.is_deleted.is_(False))
        if not include_playground:
            stmt = stmt.where(
                MessageHeader.is_playground.is_(False)
            )
        if since is not None:
            stmt = stmt.where(
                MessageHeader.last_message_received_at >= since
            )
        if until is not None:
            stmt = stmt.where(
                MessageHeader.last_message_received_at <= until
            )
        rows = await _scalars_all(self._session, stmt)
        return [MessageHeaderRecord.from_orm(r) for r in rows]

    async def list_by_booking_id(
        self,
        booking_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        include_playground: bool = False,
    ) -> list[MessageHeaderRecord]:
        """Return all thread headers attached to one booking."""
        stmt = (
            select(MessageHeader)
            .where(MessageHeader.booking_id == booking_id)
            .order_by(
                MessageHeader.last_message_received_at.desc()
            )
        )
        if not include_deleted:
            stmt = stmt.where(MessageHeader.is_deleted.is_(False))
        if not include_playground:
            stmt = stmt.where(
                MessageHeader.is_playground.is_(False)
            )
        rows = await _scalars_all(self._session, stmt)
        return [MessageHeaderRecord.from_orm(r) for r in rows]


# ── Task ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """Frozen snapshot of one ``task`` row.

    Mirrors :class:`Task` but excludes SQLAlchemy machinery so this
    object is safe to cache, pickle, or hand to LLM-facing layers
    without leaking the ORM.
    """

    id: uuid.UUID
    property_id: uuid.UUID
    customer_id: uuid.UUID | None
    department_id: uuid.UUID | None
    title: str | None
    description: str | None
    priority: str | None
    status: str | None
    assign_to: uuid.UUID | None
    hourly_rate: Decimal | None
    estimated_time: str | None
    tags: str | None
    is_active: bool
    sub_category: str | None
    main_category: str | None
    message_id: uuid.UUID | None
    due_date: datetime | None
    ai_tag: str | None
    related_message_ids: str | None
    guest_messages: str | None
    booking_status: str | None
    sources: str | None
    ai_messages: str | None
    ai_mode: str | None
    sentiment: int | None
    org_id: str | None
    created_at: datetime
    modified_at: datetime | None
    created_by: str
    modified_by: str | None
    is_deleted: bool

    @classmethod
    def from_orm(cls, row: Task) -> "TaskRecord":
        """Project a hydrated :class:`Task` into a frozen record."""
        return cls(
            id=row.id,
            property_id=row.property_id,
            customer_id=row.customer_id,
            department_id=row.department_id,
            title=row.title,
            description=row.description,
            priority=row.priority,
            status=row.status,
            assign_to=row.assign_to,
            hourly_rate=row.hourly_rate,
            estimated_time=row.estimated_time,
            tags=row.tags,
            is_active=row.is_active,
            sub_category=row.sub_category,
            main_category=row.main_category,
            message_id=row.message_id,
            due_date=row.due_date,
            ai_tag=row.ai_tag,
            related_message_ids=row.related_message_ids,
            guest_messages=row.guest_messages,
            booking_status=row.booking_status,
            sources=row.sources,
            ai_messages=row.ai_messages,
            ai_mode=row.ai_mode,
            sentiment=row.sentiment,
            org_id=row.org_id,
            created_at=row.created_at,
            modified_at=row.modified_at,
            created_by=row.created_by,
            modified_by=row.modified_by,
            is_deleted=row.is_deleted,
        )


class TaskReader:
    """Read-only adapter over the ``task`` table.

    Use ``list_by_message_id`` to attach AI-extracted tasks to a
    past conversation thread (the ``MessageId`` column on
    ``TaskManagementModel`` references the same thread key as
    :class:`MessageItem.message_id`).

    Usage::

        async with get_session() as session:
            reader = TaskReader(session)
            tasks = await reader.list_by_message_id(thread_id)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        task_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Optional[TaskRecord]:
        """Fetch one task row by primary key."""
        stmt = select(Task).where(Task.id == task_id)
        if not include_deleted:
            stmt = stmt.where(Task.is_deleted.is_(False))
        row = await _scalar_one_or_none(self._session, stmt)
        return TaskRecord.from_orm(row) if row else None

    async def list_by_message_id(
        self,
        message_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> list[TaskRecord]:
        """Return tasks linked to a ``MessageItem`` thread.

        Args:
            message_id: Thread / grouping key shared with
                :class:`MessageItem.message_id`.
            include_deleted: Include tombstoned rows.

        Returns:
            Records ordered by ``created_at`` ascending — same
            convention as :meth:`MessageItemReader.list_thread` so
            callers can interleave the two streams.
        """
        stmt = (
            select(Task)
            .where(Task.message_id == message_id)
            .order_by(Task.created_at.asc())
        )
        if not include_deleted:
            stmt = stmt.where(Task.is_deleted.is_(False))
        rows = await _scalars_all(self._session, stmt)
        return [TaskRecord.from_orm(r) for r in rows]

    async def list_by_property_id(
        self,
        property_id: uuid.UUID,
        *,
        status: str | None = None,
        limit: int = DEFAULT_RECENT_LIMIT,
        include_deleted: bool = False,
    ) -> list[TaskRecord]:
        """Return tasks for one property, newest first.

        Args:
            property_id: Property GUID.
            status: Optional case-sensitive ``status`` filter
                (e.g. ``"open"``, ``"done"``).
            limit: Maximum rows; clamped to
                :data:`MAX_RECENT_LIMIT`.
            include_deleted: Include tombstoned rows.
        """
        clamped = max(1, min(limit, MAX_RECENT_LIMIT))
        stmt = (
            select(Task)
            .where(Task.property_id == property_id)
            .order_by(Task.created_at.desc())
            .limit(clamped)
        )
        if not include_deleted:
            stmt = stmt.where(Task.is_deleted.is_(False))
        if status is not None:
            stmt = stmt.where(Task.status == status)
        rows = await _scalars_all(self._session, stmt)
        return [TaskRecord.from_orm(r) for r in rows]

    async def list_recent(
        self,
        *,
        limit: int = DEFAULT_RECENT_LIMIT,
        since: datetime | None = None,
        status: str | None = None,
        include_deleted: bool = False,
    ) -> list[TaskRecord]:
        """Return the most recent tasks across all properties.

        Args:
            limit: Maximum rows; clamped to
                :data:`MAX_RECENT_LIMIT`.
            since: Only rows with ``created_at >= since``.
            status: Optional ``status`` filter.
            include_deleted: Include tombstoned rows.
        """
        clamped = max(1, min(limit, MAX_RECENT_LIMIT))
        stmt = (
            select(Task)
            .order_by(Task.created_at.desc())
            .limit(clamped)
        )
        if not include_deleted:
            stmt = stmt.where(Task.is_deleted.is_(False))
        if since is not None:
            stmt = stmt.where(Task.created_at >= since)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        rows = await _scalars_all(self._session, stmt)
        return [TaskRecord.from_orm(r) for r in rows]


# ── Booking ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BookingRecord:
    """Frozen snapshot of one ``booking`` row.

    Mirrors :class:`Booking` but excludes SQLAlchemy machinery so
    this object is safe to cache, pickle, or hand to LLM-facing
    layers without leaking the ORM.
    """

    id: uuid.UUID
    property_id: str
    check_in_date: datetime | None
    check_out_date: datetime | None
    status: str | None
    acknowledge_status: str | None
    currency: str | None
    amount: Decimal | None
    remaining_amount: Decimal | None
    unique_id: str | None
    ota_name: str | None
    ota_commission: Decimal | None
    ota_reservation_code: str | None
    notes: str | None
    payment_type: str | None
    payment_collect: str | None
    revision_id: str | None
    channel_booking_id: str | None
    channel_code: str | None
    channel_conversation_id: str | None
    guest_portal_url: str | None
    cancellation_policy_id: str | None
    cancellation_policy_data: str | None
    is_playground: bool
    cancellation_date: datetime | None
    cancelled_by: str | None
    coupon_id: str | None
    agreement: str | None
    agreement_url: str | None
    insurance_policy_id: str | None
    insurance_status: str | None
    comment: str | None
    host_notes: str | None
    door_code: str | None
    door_code_vendor: str | None
    door_code_instruction: str | None
    channel_pms: str | None
    created_at: datetime
    modified_at: datetime | None
    created_by: str
    modified_by: str | None
    is_deleted: bool

    @classmethod
    def from_orm(cls, row: Booking) -> "BookingRecord":
        """Project a hydrated :class:`Booking` into a frozen record."""
        return cls(
            id=row.id,
            property_id=row.property_id,
            check_in_date=row.check_in_date,
            check_out_date=row.check_out_date,
            status=row.status,
            acknowledge_status=row.acknowledge_status,
            currency=row.currency,
            amount=row.amount,
            remaining_amount=row.remaining_amount,
            unique_id=row.unique_id,
            ota_name=row.ota_name,
            ota_commission=row.ota_commission,
            ota_reservation_code=row.ota_reservation_code,
            notes=row.notes,
            payment_type=row.payment_type,
            payment_collect=row.payment_collect,
            revision_id=row.revision_id,
            channel_booking_id=row.channel_booking_id,
            channel_code=row.channel_code,
            channel_conversation_id=row.channel_conversation_id,
            guest_portal_url=row.guest_portal_url,
            cancellation_policy_id=row.cancellation_policy_id,
            cancellation_policy_data=row.cancellation_policy_data,
            is_playground=row.is_playground,
            cancellation_date=row.cancellation_date,
            cancelled_by=row.cancelled_by,
            coupon_id=row.coupon_id,
            agreement=row.agreement,
            agreement_url=row.agreement_url,
            insurance_policy_id=row.insurance_policy_id,
            insurance_status=row.insurance_status,
            comment=row.comment,
            host_notes=row.host_notes,
            door_code=row.door_code,
            door_code_vendor=row.door_code_vendor,
            door_code_instruction=row.door_code_instruction,
            channel_pms=row.channel_pms,
            created_at=row.created_at,
            modified_at=row.modified_at,
            created_by=row.created_by,
            modified_by=row.modified_by,
            is_deleted=row.is_deleted,
        )


class BookingReader:
    """Read-only adapter over the ``booking`` table.

    The reader excludes ``IsPlayground = 1`` rows by default because
    they belong to the .NET sandbox stack and pollute analytical
    aggregates.  Pass ``include_playground=True`` to fetch them.

    Usage::

        async with get_session() as session:
            reader = BookingReader(session)
            booking = await reader.get_by_channel_booking_id(
                "abc-123",
            )
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        booking_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        include_playground: bool = False,
    ) -> Optional[BookingRecord]:
        """Fetch one booking row by primary key."""
        stmt = select(Booking).where(Booking.id == booking_id)
        if not include_deleted:
            stmt = stmt.where(Booking.is_deleted.is_(False))
        if not include_playground:
            stmt = stmt.where(Booking.is_playground.is_(False))
        row = await _scalar_one_or_none(self._session, stmt)
        return BookingRecord.from_orm(row) if row else None

    async def get_by_channel_booking_id(
        self,
        channel_booking_id: str,
        *,
        channel_code: str | None = None,
        include_deleted: bool = False,
        include_playground: bool = False,
    ) -> Optional[BookingRecord]:
        """Fetch one booking by upstream OTA reservation key.

        ``channel_booking_id`` is unique only within a channel; pass
        ``channel_code`` (e.g. ``"Hostaway"``) when collisions across
        OTAs are possible in the dataset.
        """
        stmt = select(Booking).where(
            Booking.channel_booking_id == channel_booking_id
        )
        if channel_code is not None:
            stmt = stmt.where(Booking.channel_code == channel_code)
        if not include_deleted:
            stmt = stmt.where(Booking.is_deleted.is_(False))
        if not include_playground:
            stmt = stmt.where(Booking.is_playground.is_(False))
        row = await _scalar_one_or_none(self._session, stmt)
        return BookingRecord.from_orm(row) if row else None

    async def list_by_property_id(
        self,
        property_id: str,
        *,
        check_in_from: datetime | None = None,
        check_in_to: datetime | None = None,
        status: str | None = None,
        limit: int = DEFAULT_RECENT_LIMIT,
        include_deleted: bool = False,
        include_playground: bool = False,
    ) -> list[BookingRecord]:
        """Return bookings for one property, newest by check-in.

        Args:
            property_id: Upstream property identifier (string —
                channel ids are not Guids).
            check_in_from: Inclusive lower bound on
                ``check_in_date``.
            check_in_to: Inclusive upper bound on ``check_in_date``.
            status: Optional ``status`` filter (e.g.
                ``"confirmed"``, ``"cancelled"``).
            limit: Maximum rows; clamped to
                :data:`MAX_RECENT_LIMIT`.
            include_deleted: Include tombstoned rows.
            include_playground: Include sandbox rows.
        """
        clamped = max(1, min(limit, MAX_RECENT_LIMIT))
        stmt = (
            select(Booking)
            .where(Booking.property_id == property_id)
            .order_by(Booking.check_in_date.desc())
            .limit(clamped)
        )
        if not include_deleted:
            stmt = stmt.where(Booking.is_deleted.is_(False))
        if not include_playground:
            stmt = stmt.where(Booking.is_playground.is_(False))
        if check_in_from is not None:
            stmt = stmt.where(
                Booking.check_in_date >= check_in_from
            )
        if check_in_to is not None:
            stmt = stmt.where(
                Booking.check_in_date <= check_in_to
            )
        if status is not None:
            stmt = stmt.where(Booking.status == status)
        rows = await _scalars_all(self._session, stmt)
        return [BookingRecord.from_orm(r) for r in rows]

    async def list_recent(
        self,
        *,
        limit: int = DEFAULT_RECENT_LIMIT,
        since: datetime | None = None,
        status: str | None = None,
        channel_code: str | None = None,
        include_deleted: bool = False,
        include_playground: bool = False,
    ) -> list[BookingRecord]:
        """Return the most recent bookings across all properties.

        Args:
            limit: Maximum rows; clamped to
                :data:`MAX_RECENT_LIMIT`.
            since: Only rows with ``created_at >= since``.
            status: Optional ``status`` filter.
            channel_code: Optional ``channel_code`` filter
                (e.g. ``"Guesty"``).
            include_deleted: Include tombstoned rows.
            include_playground: Include sandbox rows.
        """
        clamped = max(1, min(limit, MAX_RECENT_LIMIT))
        stmt = (
            select(Booking)
            .order_by(Booking.created_at.desc())
            .limit(clamped)
        )
        if not include_deleted:
            stmt = stmt.where(Booking.is_deleted.is_(False))
        if not include_playground:
            stmt = stmt.where(Booking.is_playground.is_(False))
        if since is not None:
            stmt = stmt.where(Booking.created_at >= since)
        if status is not None:
            stmt = stmt.where(Booking.status == status)
        if channel_code is not None:
            stmt = stmt.where(Booking.channel_code == channel_code)
        rows = await _scalars_all(self._session, stmt)
        return [BookingRecord.from_orm(r) for r in rows]


# ── Module-level driver helpers ─────────────────────────────────────


async def _scalar_one_or_none(
    session: AsyncSession, stmt: Any
) -> Any:
    """Run a scalar-or-none query and wrap driver errors."""
    try:
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise _wrap_driver_error(
            "scalar_one_or_none", exc
        ) from exc


async def _scalars_all(
    session: AsyncSession, stmt: Any
) -> Sequence[Any]:
    """Run a multi-row query and wrap driver errors."""
    try:
        result = await session.execute(stmt)
        return result.scalars().all()
    except SQLAlchemyError as exc:
        raise _wrap_driver_error("scalars_all", exc) from exc


def _wrap_driver_error(
    operation: str, exc: SQLAlchemyError
) -> BotelPmsConnectionError:
    """Translate a SQLAlchemy error into the Botel-PMS taxonomy."""
    logger.warning(
        "botel_pms.reader.driver_error",
        operation=operation,
        error=str(exc),
    )
    return BotelPmsConnectionError(
        f"Botel PMS reader '{operation}' failed: {exc}",
        operation=operation,
    )
