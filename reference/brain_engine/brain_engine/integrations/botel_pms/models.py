"""Read-only SQLAlchemy ORM mapping for the Botel/Bookly.Pms schema.

The upstream tables are owned by the .NET ``Bookly.Pms`` service —
EF Core writes the columns with PascalCase names while pinning the
table identifiers to lowercase via ``builder.ToTable("...")``.  Each
mapped column therefore declares an explicit DB-side ``name`` so the
Python attribute can stay PEP 8 while still resolving to the right
identifier.

The package keeps its own :class:`BotelPmsBase` declarative base
intentionally separate from
:mod:`brain_engine.conversation.db.models`: that base owns the CORA
``cora_*`` tables and runs ``Base.metadata.create_all`` at engine
boot, which we must never do against a foreign schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
)
from sqlalchemy.types import TypeDecorator

__all__ = [
    "Booking",
    "BotelPmsBase",
    "MessageHeader",
    "MessageItem",
    "Task",
]


class BotelPmsBase(DeclarativeBase):
    """Declarative base for foreign Bookly.Pms tables (read-only).

    Kept separate from the brain_engine declarative base so any
    accidental ``metadata.create_all`` call here is scoped to this
    foreign-schema mapping and still cannot touch CORA-owned tables.
    """


class _DashedUuid(TypeDecorator):
    """UUID stored as ``varchar(36)`` with dashes.

    SQLAlchemy 2.0's :class:`sqlalchemy.Uuid` strips dashes when
    binding for MySQL (uses ``uuid.UUID.hex`` — 32 chars), but the
    Bookly.Pms / Pomelo .NET schema writes UUID strings *with*
    dashes (36 chars).  Equality queries from the engine would
    therefore never match.  This decorator binds ``str(uuid)``
    (canonical 36-char form) and parses results back into
    :class:`uuid.UUID` so the rest of the package keeps the Python
    UUID semantics it expects.
    """

    impl = String
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(36)

    def process_bind_param(
        self, value: Any, dialect: Any
    ) -> Any:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return str(value)
        return str(value)

    def process_result_value(
        self, value: Any, dialect: Any
    ) -> Any:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


# Public alias used in column declarations below.
Uuid = _DashedUuid


class MessageItem(BotelPmsBase):
    """One inbox message row, as written by the Bookly.Pms service.

    Notes:
        * ``id`` is the row primary key (``BaseEntity.Id``).
        * ``message_id`` is the thread / conversation grouping key
          carried by every reply that belongs to the same exchange —
          analytics readers filter on this column to assemble a
          past conversation.
        * ``is_deleted`` follows the upstream soft-delete convention;
          callers should always filter ``is_deleted == False`` unless
          they explicitly want tombstoned rows.
    """

    __tablename__ = "messageitem"

    # ── BaseEntity ──────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        "Id", Uuid(), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime, nullable=False
    )
    modified_at: Mapped[Optional[datetime]] = mapped_column(
        "ModifiedAt", DateTime, nullable=True
    )
    created_by: Mapped[str] = mapped_column(
        "CreatedBy", String(255), nullable=False
    )
    modified_by: Mapped[Optional[str]] = mapped_column(
        "ModifiedBy", String(255), nullable=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        "IsDeleted", Boolean, nullable=False, default=False
    )

    # ── MessageItem-specific ────────────────────────────────────────
    message_id: Mapped[uuid.UUID] = mapped_column(
        "MessageId", Uuid(), nullable=False
    )
    message: Mapped[Optional[str]] = mapped_column(
        "Message", Text, nullable=True
    )
    sender: Mapped[Optional[str]] = mapped_column(
        "Sender", String(255), nullable=True
    )
    created_by_name: Mapped[Optional[str]] = mapped_column(
        "CreatedByName", String(255), nullable=True
    )
    is_need_attention: Mapped[bool] = mapped_column(
        "IsNeedAttention", Boolean, nullable=False, default=False
    )
    message_type: Mapped[Optional[str]] = mapped_column(
        "MessageType", String(64), nullable=True
    )
    communication_type: Mapped[Optional[str]] = mapped_column(
        "CommunicationType", String(64), nullable=True
    )
    send_by_ai: Mapped[bool] = mapped_column(
        "SendByAI", Boolean, nullable=False, default=False
    )
    ai_tag: Mapped[Optional[str]] = mapped_column(
        "AITag", String(255), nullable=True
    )
    sources: Mapped[Optional[str]] = mapped_column(
        "Sources", Text, nullable=True
    )
    sentiment: Mapped[Optional[int]] = mapped_column(
        "Sentiment", Integer, nullable=True
    )
    was_helpful: Mapped[Optional[int]] = mapped_column(
        "WasHelpful", Integer, nullable=True
    )
    was_helpful_completeness: Mapped[Optional[str]] = mapped_column(
        "WasHelpfulCompleteness", Text, nullable=True
    )
    tasks: Mapped[Optional[str]] = mapped_column(
        "Tasks", Text, nullable=True
    )
    ai_mode: Mapped[Optional[str]] = mapped_column(
        "AIMode", String(64), nullable=True
    )
    provider_message_id: Mapped[Optional[str]] = mapped_column(
        "ProviderMessageId", String(500), nullable=True
    )
    email_metadata: Mapped[Optional[Any]] = mapped_column(
        "EmailMetadata", JSON, nullable=True
    )

    __table_args__ = (
        Index(
            "IX_MessageItem_ProviderMessageId",
            "ProviderMessageId",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MessageItem id={self.id} "
            f"message_id={self.message_id} "
            f"sender={self.sender!r} "
            f"created_at={self.created_at.isoformat()}>"
        )


class Task(BotelPmsBase):
    """One ``TaskManagementModel`` row from the Bookly.Pms task table.

    Notes:
        * The upstream C# class is ``TaskManagementModel`` but the
          table is pinned to ``task`` via ``builder.ToTable("task")``.
        * ``message_id`` is the optional thread key that links a task
          back to the originating ``MessageItem`` exchange — readers
          use it to attach AI-extracted tasks to a past conversation.
        * Two soft-delete-ish flags coexist: ``is_active`` is the
          domain-level "task still relevant" boolean, while
          ``is_deleted`` (from BaseEntity) is the canonical filter.
          Per upstream convention, queries should always exclude
          ``is_deleted == True`` rows.
        * ``estimated_time`` is stored as the upstream MySQL
          ``TIME(6)`` literal serialized as a string; downstream
          consumers parse it on demand to keep this layer driver-
          agnostic.
        * ``org_id`` is the Auth0 Organization ID (``org_xxx``) on
          this entity — not a workspace GUID.  See the Bookly.Pms
          OrgId semantic map before passing this value to any other
          subsystem.
    """

    __tablename__ = "task"

    # ── BaseEntity ──────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        "Id", Uuid(), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime, nullable=False
    )
    modified_at: Mapped[Optional[datetime]] = mapped_column(
        "ModifiedAt", DateTime, nullable=True
    )
    created_by: Mapped[str] = mapped_column(
        "CreatedBy", String(255), nullable=False
    )
    modified_by: Mapped[Optional[str]] = mapped_column(
        "ModifiedBy", String(255), nullable=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        "IsDeleted", Boolean, nullable=False, default=False
    )

    # ── Task-specific ───────────────────────────────────────────────
    property_id: Mapped[uuid.UUID] = mapped_column(
        "PropertyId", Uuid(), nullable=False
    )
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        "CustomerId", Uuid(), nullable=True
    )
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        "DepartmentId", Uuid(), nullable=True
    )
    title: Mapped[Optional[str]] = mapped_column(
        "Title", String(500), nullable=True
    )
    description: Mapped[Optional[str]] = mapped_column(
        "Description", Text, nullable=True
    )
    priority: Mapped[Optional[str]] = mapped_column(
        "Priority", String(32), nullable=True
    )
    status: Mapped[Optional[str]] = mapped_column(
        "Status", String(32), nullable=True
    )
    assign_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        "AssignTo", Uuid(), nullable=True
    )
    hourly_rate: Mapped[Optional[Decimal]] = mapped_column(
        "HourlyRate", Numeric(18, 2), nullable=True
    )
    estimated_time: Mapped[Optional[str]] = mapped_column(
        "EstimatedTime", String(64), nullable=True
    )
    tags: Mapped[Optional[str]] = mapped_column(
        "Tags", Text, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        "IsActive", Boolean, nullable=False, default=True
    )
    sub_category: Mapped[Optional[str]] = mapped_column(
        "SubCategory", String(255), nullable=True
    )
    main_category: Mapped[Optional[str]] = mapped_column(
        "MainCategory", String(255), nullable=True
    )
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        "MessageId", Uuid(), nullable=True
    )
    due_date: Mapped[Optional[datetime]] = mapped_column(
        "DueDate", DateTime, nullable=True
    )
    ai_tag: Mapped[Optional[str]] = mapped_column(
        "AITag", String(255), nullable=True
    )
    related_message_ids: Mapped[Optional[str]] = mapped_column(
        "RelatedMessageIds", Text, nullable=True
    )
    guest_messages: Mapped[Optional[str]] = mapped_column(
        "GuestMessages", Text, nullable=True
    )
    booking_status: Mapped[Optional[str]] = mapped_column(
        "BookingStatus", String(64), nullable=True
    )
    sources: Mapped[Optional[str]] = mapped_column(
        "Sources", Text, nullable=True
    )
    ai_messages: Mapped[Optional[str]] = mapped_column(
        "AIMessages", Text, nullable=True
    )
    ai_mode: Mapped[Optional[str]] = mapped_column(
        "AIMode", String(64), nullable=True
    )
    sentiment: Mapped[Optional[int]] = mapped_column(
        "Sentiment", Integer, nullable=True
    )
    org_id: Mapped[Optional[str]] = mapped_column(
        "OrgId", String(64), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<Task id={self.id} "
            f"property_id={self.property_id} "
            f"status={self.status!r} "
            f"title={self.title!r}>"
        )


class Booking(BotelPmsBase):
    """One ``BookingModel`` row from the Bookly.Pms ``booking`` table.

    Notes:
        * ``property_id`` is stored as a string (not Guid) because
          OTA channel ids feed the same column unchanged.
        * ``unique_id`` is the canonical reservation handle exposed
          to downstream surfaces; ``channel_booking_id`` is the
          upstream OTA primary key.
        * ``is_playground`` rows belong to the .NET sandbox stack
          and are typically excluded from analytical sweeps —
          readers expose an ``include_playground`` toggle.
        * Numeric fields use ``DECIMAL(18,4)`` to round-trip Stripe
          / OTA cents safely; the upstream EF mapping is implicit
          (Pomelo default) so this is the safe upper bound.
    """

    __tablename__ = "booking"

    # ── BaseEntity ──────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        "Id", Uuid(), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime, nullable=False
    )
    modified_at: Mapped[Optional[datetime]] = mapped_column(
        "ModifiedAt", DateTime, nullable=True
    )
    created_by: Mapped[str] = mapped_column(
        "CreatedBy", String(255), nullable=False
    )
    modified_by: Mapped[Optional[str]] = mapped_column(
        "ModifiedBy", String(255), nullable=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        "IsDeleted", Boolean, nullable=False, default=False
    )

    # ── Booking-specific ────────────────────────────────────────────
    property_id: Mapped[Optional[str]] = mapped_column(
        "PropertyId", String(255), nullable=False
    )
    check_in_date: Mapped[Optional[datetime]] = mapped_column(
        "CheckInDate", DateTime, nullable=True
    )
    check_out_date: Mapped[Optional[datetime]] = mapped_column(
        "CheckOutDate", DateTime, nullable=True
    )
    status: Mapped[Optional[str]] = mapped_column(
        "Status", String(64), nullable=True
    )
    acknowledge_status: Mapped[Optional[str]] = mapped_column(
        "AcknowledgeStatus", String(64), nullable=True
    )
    currency: Mapped[Optional[str]] = mapped_column(
        "Currency", String(8), nullable=True
    )
    amount: Mapped[Optional[Decimal]] = mapped_column(
        "Amount", Numeric(18, 4), nullable=True
    )
    remaining_amount: Mapped[Optional[Decimal]] = mapped_column(
        "RemainingAmount", Numeric(18, 4), nullable=True
    )
    unique_id: Mapped[Optional[str]] = mapped_column(
        "UniqueId", String(255), nullable=True
    )
    ota_name: Mapped[Optional[str]] = mapped_column(
        "OtaName", String(64), nullable=True
    )
    ota_commission: Mapped[Optional[Decimal]] = mapped_column(
        "OtaCommission", Numeric(18, 4), nullable=True
    )
    ota_reservation_code: Mapped[Optional[str]] = mapped_column(
        "OtaReservationCode", String(255), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(
        "Notes", Text, nullable=True
    )
    payment_type: Mapped[Optional[str]] = mapped_column(
        "PaymentType", String(64), nullable=True
    )
    payment_collect: Mapped[Optional[str]] = mapped_column(
        "PaymentCollect", String(64), nullable=True
    )
    revision_id: Mapped[Optional[str]] = mapped_column(
        "RevisionId", String(255), nullable=True
    )
    channel_booking_id: Mapped[Optional[str]] = mapped_column(
        "ChannelBookingId", String(255), nullable=True
    )
    channel_code: Mapped[Optional[str]] = mapped_column(
        "ChannelCode", String(64), nullable=True
    )
    channel_conversation_id: Mapped[Optional[str]] = mapped_column(
        "ChannelConversationId", String(255), nullable=True
    )
    guest_portal_url: Mapped[Optional[str]] = mapped_column(
        "GuestPortalUrl", String(1024), nullable=True
    )
    cancellation_policy_id: Mapped[Optional[str]] = mapped_column(
        "CancellationPolicyId", String(255), nullable=True
    )
    cancellation_policy_data: Mapped[Optional[str]] = mapped_column(
        "CancellationPolicyData", Text, nullable=True
    )
    is_playground: Mapped[bool] = mapped_column(
        "IsPlayground", Boolean, nullable=False, default=False
    )
    cancellation_date: Mapped[Optional[datetime]] = mapped_column(
        "CancellationDate", DateTime, nullable=True
    )
    cancelled_by: Mapped[Optional[str]] = mapped_column(
        "CancelledBy", String(255), nullable=True
    )
    coupon_id: Mapped[Optional[str]] = mapped_column(
        "CouponId", String(255), nullable=True
    )
    agreement: Mapped[Optional[str]] = mapped_column(
        "Agreement", Text, nullable=True
    )
    agreement_url: Mapped[Optional[str]] = mapped_column(
        "AgreementUrl", String(1024), nullable=True
    )
    insurance_policy_id: Mapped[Optional[str]] = mapped_column(
        "InsurancePolicyId", String(255), nullable=True
    )
    insurance_status: Mapped[Optional[str]] = mapped_column(
        "InsuranceStatus", String(64), nullable=True
    )
    comment: Mapped[Optional[str]] = mapped_column(
        "Comment", Text, nullable=True
    )
    host_notes: Mapped[Optional[str]] = mapped_column(
        "HostNotes", Text, nullable=True
    )
    door_code: Mapped[Optional[str]] = mapped_column(
        "DoorCode", String(64), nullable=True
    )
    door_code_vendor: Mapped[Optional[str]] = mapped_column(
        "DoorCodeVendor", String(64), nullable=True
    )
    door_code_instruction: Mapped[Optional[str]] = mapped_column(
        "DoorCodeInstruction", Text, nullable=True
    )
    channel_pms: Mapped[Optional[str]] = mapped_column(
        "ChannelPms", String(64), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<Booking id={self.id} "
            f"property_id={self.property_id!r} "
            f"unique_id={self.unique_id!r} "
            f"status={self.status!r}>"
        )


class MessageHeader(BotelPmsBase):
    """One thread header from the ``messageheader`` table.

    The header is the canonical bridge between a property and its
    message thread:

    * ``id`` (BaseEntity primary key) is the thread identifier and
      equals every linked :class:`MessageItem.message_id`.
    * ``property_id`` carries the upstream property handle (string,
      same convention as :class:`Booking.property_id`) so a single
      property → all-its-conversations query goes through this
      table, never through ``messageitem`` directly (which has no
      property column).
    * ``booking_id`` denormalises the linked reservation, with
      ``booking_check_in`` / ``booking_check_out`` /
      ``booking_status`` mirrored for cheap sweep queries.
    * ``parent_id`` chains forks (e.g. a thread split out of a
      master inbox); analytics that walk the tree should follow it.
    """

    __tablename__ = "messageheader"

    # ── BaseEntity ──────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        "Id", Uuid(), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAt", DateTime, nullable=False
    )
    modified_at: Mapped[Optional[datetime]] = mapped_column(
        "ModifiedAt", DateTime, nullable=True
    )
    created_by: Mapped[str] = mapped_column(
        "CreatedBy", String(255), nullable=False
    )
    modified_by: Mapped[Optional[str]] = mapped_column(
        "ModifiedBy", String(255), nullable=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        "IsDeleted", Boolean, nullable=False, default=False
    )

    # ── MessageHeader-specific ──────────────────────────────────────
    title: Mapped[Optional[str]] = mapped_column(
        "Title", String(500), nullable=True
    )
    provider: Mapped[Optional[str]] = mapped_column(
        "Provider", String(64), nullable=True
    )
    is_closed: Mapped[int] = mapped_column(
        "IsClosed", Integer, nullable=False, default=0
    )
    # Stored as varchar(100) in the upstream schema — Pomelo .NET
    # serialises DateTime values as ISO-ish strings ("2025-11-06
    # 15:18:49.000000") rather than using a native DATETIME column.
    # Keeping the Python type as ``str`` avoids forcing every
    # caller through a parse step they may not need.
    last_message_received_at: Mapped[Optional[str]] = mapped_column(
        "LastMessageReceivedAt", String(100), nullable=True
    )
    message_count: Mapped[int] = mapped_column(
        "MessageCount", Integer, nullable=False, default=0
    )
    property_id: Mapped[Optional[str]] = mapped_column(
        "PropertyId", String(255), nullable=True
    )
    property_name: Mapped[Optional[str]] = mapped_column(
        "PropertyName", String(255), nullable=True
    )
    booking_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        "BookingId", Uuid(), nullable=True
    )
    booking_status: Mapped[Optional[str]] = mapped_column(
        "BookingStatus", String(64), nullable=True
    )
    booking_occupancy: Mapped[Optional[int]] = mapped_column(
        "BookingOccupancy", Integer, nullable=True
    )
    booking_check_in: Mapped[Optional[datetime]] = mapped_column(
        "BookingCheckIn", DateTime, nullable=True
    )
    booking_check_out: Mapped[Optional[datetime]] = mapped_column(
        "BookingCheckOut", DateTime, nullable=True
    )
    source: Mapped[Optional[str]] = mapped_column(
        "Source", String(64), nullable=True
    )
    ai_reply_status: Mapped[Optional[str]] = mapped_column(
        "AIReplyStatus", String(64), nullable=True
    )
    assigned_user: Mapped[Optional[str]] = mapped_column(
        "AssignedUser", String(255), nullable=True
    )
    is_need_attention: Mapped[bool] = mapped_column(
        "IsNeedAttention", Boolean, nullable=False, default=False
    )
    view_at: Mapped[datetime] = mapped_column(
        "ViewAt", DateTime, nullable=False
    )
    from_sync: Mapped[bool] = mapped_column(
        "FromSync", Boolean, nullable=False, default=False
    )
    customer_id: Mapped[Optional[str]] = mapped_column(
        "CustomerId", String(64), nullable=True
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        "ParentId", Uuid(), nullable=True
    )
    is_playground: Mapped[bool] = mapped_column(
        "IsPlayground", Boolean, nullable=False, default=False
    )
    customer_ai_id: Mapped[Optional[str]] = mapped_column(
        "CustomerAiId", String(64), nullable=True
    )
    sentiment: Mapped[Optional[int]] = mapped_column(
        "Sentiment", Integer, nullable=True
    )
    response_language: Mapped[Optional[str]] = mapped_column(
        "ResponseLanguage", String(16), nullable=True
    )
    provider_pms: Mapped[Optional[str]] = mapped_column(
        "ProviderPms", String(64), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<MessageHeader id={self.id} "
            f"property_id={self.property_id!r} "
            f"title={self.title!r} "
            f"message_count={self.message_count}>"
        )
