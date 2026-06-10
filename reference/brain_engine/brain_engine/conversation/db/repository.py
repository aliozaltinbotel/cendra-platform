"""Conversation history repository — CRUD operations.

Async data access layer for conversation and message persistence.
All operations use SQLAlchemy async sessions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from brain_engine.conversation.db.models import (
    Conversation,
    ConversationMessage,
    ConversationPhase,
    MessageRole,
)

logger = logging.getLogger(__name__)


class ConversationRepository:
    """Async CRUD for conversation history.

    Args:
        session: SQLAlchemy async session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_conversation(
        self,
        customer_id: str,
        org_id: str = "",
        agent_type: str = "conversation",
        initial_context: str = "",
    ) -> Conversation:
        """Create a new conversation record.

        Args:
            customer_id: Tenant identifier.
            org_id: Organization identifier.
            agent_type: Type of agent (conversation, rule_creator).
            initial_context: Initial context text.

        Returns:
            Created Conversation with generated workflow_id.
        """
        conv = Conversation(
            workflow_id=f"wf-{uuid.uuid4().hex[:12]}",
            customer_id=customer_id,
            org_id=org_id,
            agent_type=agent_type,
            initial_context=initial_context,
            phase=ConversationPhase.GREETING.value,
        )
        self._session.add(conv)
        await self._session.flush()
        logger.info("Created conversation %s for %s", conv.workflow_id, customer_id)
        return conv

    async def get_by_workflow_id(
        self,
        workflow_id: str,
    ) -> Conversation | None:
        """Fetch a conversation by workflow ID with messages.

        Args:
            workflow_id: Unique workflow identifier.

        Returns:
            Conversation with eager-loaded messages, or None.
        """
        stmt = (
            select(Conversation)
            .where(Conversation.workflow_id == workflow_id)
            .where(Conversation.deleted_at.is_(None))
            .options(selectinload(Conversation.messages))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_conversations(
        self,
        customer_id: str,
        agent_type: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[Conversation]:
        """List conversations for a customer.

        Args:
            customer_id: Tenant identifier.
            agent_type: Filter by agent type (empty = all).
            limit: Max results.
            offset: Skip first N.

        Returns:
            List of Conversation objects (without messages).
        """
        stmt = (
            select(Conversation)
            .where(Conversation.customer_id == customer_id)
            .where(Conversation.deleted_at.is_(None))
        )
        if agent_type:
            stmt = stmt.where(Conversation.agent_type == agent_type)

        stmt = stmt.order_by(Conversation.updated_at.desc())
        stmt = stmt.limit(limit).offset(offset)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def add_message(
        self,
        workflow_id: str,
        role: MessageRole,
        content: str,
        phase: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        """Add a message to an existing conversation.

        Also increments turn_count and updates updated_at.

        Args:
            workflow_id: Conversation workflow ID.
            role: Message sender role.
            content: Message text.
            phase: Current conversation phase.
            metadata: Optional metadata dict.

        Returns:
            Created ConversationMessage.
        """
        conv = await self.get_by_workflow_id(workflow_id)
        if not conv:
            raise ValueError(f"Conversation {workflow_id} not found")

        msg = ConversationMessage(
            conversation_id=conv.id,
            workflow_id=workflow_id,
            message_id=f"msg-{uuid.uuid4().hex[:12]}",
            role=role.value,
            content=content,
            phase=phase,
            metadata_json=metadata,
        )
        self._session.add(msg)

        conv.turn_count += 1
        conv.updated_at = datetime.now(timezone.utc)

        await self._session.flush()
        return msg

    async def update_phase(
        self,
        workflow_id: str,
        phase: ConversationPhase,
        is_complete: bool = False,
        context_summary: str = "",
    ) -> None:
        """Update conversation phase and completion status.

        Args:
            workflow_id: Conversation workflow ID.
            phase: New phase.
            is_complete: Whether conversation is done.
            context_summary: Updated context summary.
        """
        values: dict[str, Any] = {
            "phase": phase.value,
            "is_complete": is_complete,
            "updated_at": datetime.now(timezone.utc),
        }
        if context_summary:
            values["context_summary"] = context_summary

        stmt = (
            update(Conversation)
            .where(Conversation.workflow_id == workflow_id)
            .values(**values)
        )
        await self._session.execute(stmt)

    async def soft_delete(self, workflow_id: str) -> None:
        """Soft-delete a conversation.

        Args:
            workflow_id: Conversation to delete.
        """
        stmt = (
            update(Conversation)
            .where(Conversation.workflow_id == workflow_id)
            .values(deleted_at=datetime.now(timezone.utc))
        )
        await self._session.execute(stmt)

    async def commit(self) -> None:
        """Commit the current transaction."""
        await self._session.commit()

    async def rollback(self) -> None:
        """Rollback the current transaction."""
        await self._session.rollback()
