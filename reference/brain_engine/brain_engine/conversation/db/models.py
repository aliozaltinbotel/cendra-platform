"""SQLAlchemy ORM models for conversation history persistence.

Two tables:
- conversations: tracks workflow sessions (rule creation, guest threads)
- conversation_messages: individual messages within a conversation

Uses async SQLAlchemy 2.0 with MySQL (asyncmy driver).
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""

    pass


class MessageRole(str, enum.Enum):
    """Who sent the message in a conversation."""

    USER = "user"
    AGENT = "agent"


class ConversationPhase(str, enum.Enum):
    """Phase of an agentic conversation workflow."""

    GREETING = "greeting"
    INTENT_DISCOVERY = "intent_discovery"
    TYPE_DETERMINATION = "type_determination"
    DETAIL_COLLECTION = "detail_collection"
    CONFIRMATION = "confirmation"
    FINALIZED = "finalized"
    CANCELLED = "cancelled"


class Conversation(Base):
    """Persistent conversation session.

    Tracks a multi-turn workflow: guest threads, rule creation
    sessions, or any stateful conversation.
    """

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    org_id: Mapped[str] = mapped_column(String(64), default="")
    agent_id: Mapped[str] = mapped_column(String(64), default="")
    agent_type: Mapped[str] = mapped_column(String(32), default="conversation")

    phase: Mapped[str] = mapped_column(
        String(32), default=ConversationPhase.GREETING.value,
    )
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)

    bundle_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    initial_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at",
    )

    __table_args__ = (
        Index(
            "ix_conv_customer_type_deleted",
            "customer_id", "agent_type", "deleted_at",
        ),
    )


class ConversationMessage(Base):
    """Single message within a conversation.

    Stores the full text content, role (user/agent), phase at
    time of message, and optional metadata JSON.
    """

    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id", ondelete="CASCADE"),
    )
    workflow_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[str] = mapped_column(String(64), unique=True)

    role: Mapped[str] = mapped_column(
        SAEnum(MessageRole), default=MessageRole.USER.value,
    )
    content: Mapped[str] = mapped_column(Text, default="")
    phase: Mapped[str] = mapped_column(String(32), default="")
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_msg_workflow_id", "workflow_id"),
        Index("ix_msg_conversation_id", "conversation_id"),
    )
