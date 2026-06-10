"""Undo executor for :class:`ActionEnvelope`.

The :class:`UndoExecutor` answers "can this action still be undone,
and if so, how?"  It implements the three-tier reversibility model:

- **GREEN** — delete the side effect locally (e.g. rescind an unsent
  draft) within 60 s.  No downstream call is required.
- **AMBER** — send a compensating call to the downstream system
  (e.g. cancel a PMS message) within 10 min.  Requires a
  ``CompensatingTransport`` and an ``external_reference`` on the
  envelope.
- **RED** — no Undo; audit log only.

The executor is Protocol-driven (:class:`ActionStore`,
:class:`CompensatingTransport`) so production and test wiring differ
only in the injected implementations.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import structlog

from brain_engine.actions.errors import (
    ActionNotFound,
    AlreadyUndone,
    CompensationFailed,
    NotYetExecuted,
    UndoNotAllowed,
    UndoWindowExpired,
)
from brain_engine.actions.models import ActionEnvelope, ActionStatus
from brain_engine.cards.models import ReversibilityTier


logger = structlog.get_logger(__name__)


@runtime_checkable
class ActionStore(Protocol):
    """Persistence for :class:`ActionEnvelope`."""

    async def save(self, envelope: ActionEnvelope) -> None:
        """Upsert an envelope."""
        ...

    async def get(self, action_id: str) -> ActionEnvelope | None:
        """Fetch an envelope by id, returning ``None`` if missing."""
        ...


@runtime_checkable
class CompensatingTransport(Protocol):
    """Sends AMBER compensating calls to the downstream system."""

    async def compensate(self, envelope: ActionEnvelope) -> None:
        """Execute the compensating action or raise on failure."""
        ...


class InMemoryActionStore:
    """Dev / test implementation of :class:`ActionStore`."""

    def __init__(self) -> None:
        self._data: dict[str, ActionEnvelope] = {}

    async def save(self, envelope: ActionEnvelope) -> None:
        self._data[envelope.action_id] = envelope

    async def get(self, action_id: str) -> ActionEnvelope | None:
        return self._data.get(action_id)


class UndoExecutor:
    """Validates and applies Undo for executed :class:`ActionEnvelope`s."""

    def __init__(
        self,
        *,
        store: ActionStore,
        transport: CompensatingTransport | None = None,
    ) -> None:
        self._store = store
        self._transport = transport
        self._log = logger.bind(component="undo_executor")

    async def record_execution(
        self,
        envelope: ActionEnvelope,
        *,
        external_reference: str | None = None,
    ) -> ActionEnvelope:
        """Persist the post-execution state of an envelope.

        Updates status → EXECUTED, stamps ``executed_at``, stores the
        external reference used by AMBER compensation.
        """
        executed = replace(
            envelope,
            status=ActionStatus.EXECUTED,
            executed_at=datetime.now(timezone.utc),
            external_reference=external_reference
            or envelope.external_reference,
        )
        await self._store.save(executed)
        return executed

    async def undo(
        self,
        action_id: str,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> ActionEnvelope:
        """Reverse an executed action.

        Raises:
            ActionNotFound: envelope missing.
            NotYetExecuted: status is PENDING.
            AlreadyUndone: status is UNDONE.
            UndoNotAllowed: reversibility tier is RED.
            UndoWindowExpired: GREEN/AMBER window has closed.
            CompensationFailed: AMBER compensating call raised.
        """
        envelope = await self._load(action_id)
        self._guard_status(envelope)
        self._guard_tier(envelope)
        self._guard_window(envelope, now=now)

        if envelope.reversibility is ReversibilityTier.AMBER:
            await self._compensate(envelope)

        reversed_env = replace(
            envelope,
            status=ActionStatus.UNDONE,
            undone_at=datetime.now(timezone.utc),
            undo_reason=reason,
        )
        await self._store.save(reversed_env)
        self._log.info(
            "action.undone",
            action_id=action_id,
            tier=envelope.reversibility.value,
            reason=reason,
        )
        return reversed_env

    # ------------------------------------------------------------------
    # Internal guards
    # ------------------------------------------------------------------

    async def _load(self, action_id: str) -> ActionEnvelope:
        envelope = await self._store.get(action_id)
        if envelope is None:
            raise ActionNotFound(action_id)
        return envelope

    @staticmethod
    def _guard_status(envelope: ActionEnvelope) -> None:
        if envelope.status is ActionStatus.PENDING:
            raise NotYetExecuted(envelope.action_id)
        if envelope.status is ActionStatus.UNDONE:
            raise AlreadyUndone(envelope.action_id)

    @staticmethod
    def _guard_tier(envelope: ActionEnvelope) -> None:
        if envelope.reversibility is ReversibilityTier.RED:
            raise UndoNotAllowed(envelope.action_id)

    @staticmethod
    def _guard_window(
        envelope: ActionEnvelope,
        *,
        now: datetime | None,
    ) -> None:
        if not envelope.within_undo_window(now=now):
            raise UndoWindowExpired(envelope.action_id)

    async def _compensate(self, envelope: ActionEnvelope) -> None:
        if self._transport is None:
            raise CompensationFailed(
                f"no transport configured for amber action "
                f"{envelope.action_id}",
            )
        try:
            await self._transport.compensate(envelope)
        except Exception as exc:  # noqa: BLE001 — wrap and re-raise.
            raise CompensationFailed(str(exc)) from exc
