"""Team package — roles, duty shifts, and duty-aware roster routing.

Public surface:

- :class:`TeamRole` — categorical role key used by ops routing.
- :class:`Weekday` — ISO-ordered weekday enum.
- :class:`DutyShift` — single recurring on-duty window.
- :class:`TeamMember` — immutable person record with property coverage.
- :class:`TeamRoster` — in-memory aggregate + duty-aware queries.
- :func:`weekday_of` — convert a :class:`datetime` to a :class:`Weekday`.
"""

from __future__ import annotations

from brain_engine.team.handoff import (
    Handoff,
    HandoffNotFoundError,
    HandoffStatus,
    HandoffStore,
    InMemoryHandoffStore,
    InMemoryMentionStore,
    Mention,
    MentionStore,
    new_handoff_id,
    new_mention_id,
)
from brain_engine.team.models import (
    DutyShift,
    TeamMember,
    TeamRole,
    Weekday,
    weekday_of,
)
from brain_engine.team.roster import TeamRoster

__all__ = [
    "DutyShift",
    "Handoff",
    "HandoffNotFoundError",
    "HandoffStatus",
    "HandoffStore",
    "InMemoryHandoffStore",
    "InMemoryMentionStore",
    "Mention",
    "MentionStore",
    "TeamMember",
    "TeamRole",
    "TeamRoster",
    "Weekday",
    "new_handoff_id",
    "new_mention_id",
    "weekday_of",
]
