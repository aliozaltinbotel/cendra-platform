"""Team value objects: roles, members, duty shifts.

Cendra V2 routes every action to a *person* (or escalation chain) —
not to an abstract queue.  Three ingredients power the routing:

- :class:`TeamRole` — what the person does (PM, cleaner, handyman,
  finance, owner, account manager).
- :class:`TeamMember` — a concrete individual with properties they
  cover + contact surfaces.
- :class:`DutyShift` — when the member is on duty (day-of-week +
  local hour range).  Multiple shifts can stack for 24/7 coverage.

All three are immutable — roster edits produce new instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class TeamRole(StrEnum):
    """Role-based routing keys used by the engine."""

    PM = "pm"
    CLEANER = "cleaner"
    HANDYMAN = "handyman"
    FINANCE = "finance"
    OWNER = "owner"
    ACCOUNT_MANAGER = "account_manager"


class Weekday(StrEnum):
    """Seven-day week, ordered ISO (Mon=1 … Sun=7)."""

    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


_WEEKDAY_BY_ISO: dict[int, Weekday] = {
    1: Weekday.MON,
    2: Weekday.TUE,
    3: Weekday.WED,
    4: Weekday.THU,
    5: Weekday.FRI,
    6: Weekday.SAT,
    7: Weekday.SUN,
}


def weekday_of(moment: datetime) -> Weekday:
    """Return the :class:`Weekday` for a UTC datetime."""
    return _WEEKDAY_BY_ISO[moment.isoweekday()]


@dataclass(frozen=True, slots=True)
class DutyShift:
    """A single recurring duty window.

    Times are **local hours** — timezone resolution is the caller's
    job.  ``start_hour == end_hour`` means "not on duty this day";
    ``start_hour > end_hour`` spans midnight.
    """

    member_id: str
    weekday: Weekday
    start_hour: int
    end_hour: int

    def covers(self, *, weekday: Weekday, hour: int) -> bool:
        """Whether this shift covers ``(weekday, hour)``."""
        if weekday is not self.weekday:
            return False
        if self.start_hour == self.end_hour:
            return False
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour


@dataclass(frozen=True, slots=True)
class TeamMember:
    """A concrete person in the property-management organisation."""

    member_id: str
    name: str
    role: TeamRole
    property_ids: tuple[str, ...] = ()
    email: str | None = None
    phone: str | None = None
    shifts: tuple[DutyShift, ...] = ()
    is_active: bool = True
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def covers_property(self, property_id: str) -> bool:
        """Whether this member is assigned to a property.

        An empty ``property_ids`` means the member covers every
        property (portfolio-wide role such as ``FINANCE`` or
        ``OWNER``).
        """
        if not self.property_ids:
            return True
        return property_id in self.property_ids

    def on_duty(self, *, at: datetime) -> bool:
        """Whether the member is on duty at a UTC moment.

        Uses UTC hour for shift matching — production wiring is
        expected to translate timezone before passing ``at``.
        """
        if not self.is_active or not self.shifts:
            return False
        wd = weekday_of(at)
        hour = at.hour
        return any(s.covers(weekday=wd, hour=hour) for s in self.shifts)
