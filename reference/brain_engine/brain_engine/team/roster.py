"""Team roster + duty-aware routing.

:class:`TeamRoster` owns the list of :class:`TeamMember` records and
answers two operational questions:

- **Who is on duty right now for role R at property P?** — used by
  urgent dispatch (returns the first match ordered by role priority).
- **Who is the escalation chain for role R at property P?** — used
  by the PM-correction loop when the primary member cannot respond.

The roster is a simple in-memory aggregate with Protocol-ready
persistence (add a store later without touching callers).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import structlog

from brain_engine.team.models import TeamMember, TeamRole

logger = structlog.get_logger(__name__)


class TeamRoster:
    """In-memory roster of :class:`TeamMember` records."""

    def __init__(
        self,
        members: tuple[TeamMember, ...] = (),
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._members: dict[str, TeamMember] = {
            m.member_id: m for m in members
        }
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._log = logger.bind(component="team_roster")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def upsert(self, member: TeamMember) -> None:
        """Insert or replace a member."""
        self._members[member.member_id] = member

    def remove(self, member_id: str) -> bool:
        """Delete a member. Return ``True`` if something was removed."""
        return self._members.pop(member_id, None) is not None

    def all_members(self) -> tuple[TeamMember, ...]:
        """Return every active member in insertion order."""
        return tuple(
            m for m in self._members.values() if m.is_active
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def for_role(
        self,
        role: TeamRole,
        *,
        property_id: str | None = None,
    ) -> tuple[TeamMember, ...]:
        """Return every active member in ``role`` covering ``property_id``."""
        out: list[TeamMember] = []
        for member in self.all_members():
            if member.role is not role:
                continue
            if property_id is not None and not member.covers_property(
                property_id,
            ):
                continue
            out.append(member)
        return tuple(out)

    def on_duty_for_role(
        self,
        role: TeamRole,
        *,
        property_id: str | None = None,
        at: datetime | None = None,
    ) -> tuple[TeamMember, ...]:
        """Members of ``role`` currently on duty for ``property_id``."""
        when = at or self._clock()
        return tuple(
            m for m in self.for_role(role, property_id=property_id)
            if m.on_duty(at=when)
        )

    def primary_for(
        self,
        role: TeamRole,
        *,
        property_id: str | None = None,
        at: datetime | None = None,
    ) -> TeamMember | None:
        """Return the first on-duty member, or fall back to any cover."""
        on_duty = self.on_duty_for_role(
            role, property_id=property_id, at=at,
        )
        if on_duty:
            return on_duty[0]
        any_cover = self.for_role(role, property_id=property_id)
        if any_cover:
            self._log.warning(
                "team.no_on_duty",
                role=role.value,
                property_id=property_id,
                fallback_member=any_cover[0].member_id,
            )
            return any_cover[0]
        return None

    def escalation_chain(
        self,
        role: TeamRole,
        *,
        property_id: str | None = None,
        at: datetime | None = None,
    ) -> tuple[TeamMember, ...]:
        """On-duty first, then the remaining property covers."""
        when = at or self._clock()
        on_duty = self.on_duty_for_role(
            role, property_id=property_id, at=when,
        )
        remaining = tuple(
            m for m in self.for_role(role, property_id=property_id)
            if m not in on_duty
        )
        return on_duty + remaining
