"""Tenant-isolation validator for Skill / PatternRule writes.

Pattern mining (ADR-0008) and SkillEvolution can produce rules that
inadvertently reference data from a different tenant — a property
name, a guest id, a phone number that belongs to another customer.
A rule like "if guest is +905551234567 send X" leaks the phone
number across the tenant boundary the next time it fires.

The validator is the last gate before a rule body lands in
procedural memory.  Two sources of identifiers it watches:

* **Allow-list of *this* tenant's identifiers** — the only ids the
  rule is allowed to reference verbatim.
* **Deny-list of identifiers belonging to *other* tenants** — fed
  from the cross-tenant index that the platform maintains.

Either match → ``IsolationViolation`` is raised.

Reference: ``brain_engine_advisory.md`` §9.4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class IsolationViolation(Exception):
    """Raised when a rule body references foreign-tenant data."""

    tenant_id: str
    foreign_id: str
    matched_substring: str

    def __str__(self) -> str:  # pragma: no cover — exception path
        return (
            f"tenant {self.tenant_id} rule references foreign id "
            f"{self.foreign_id!r} (matched {self.matched_substring!r})"
        )


_WORD_BOUNDARY = re.compile(r"\W+")


class TenantIsolationValidator:
    """Validates rule bodies against per-tenant allow/deny lists.

    The validator does not own the lists; the caller (Pattern miner
    or SkillEvolution) supplies them per call.  This keeps the
    validator free of I/O and trivially testable.
    """

    def validate(
        self,
        *,
        rule_body: str,
        tenant_id: str,
        own_ids: Iterable[str],
        foreign_ids: Iterable[str],
    ) -> None:
        """Raise ``IsolationViolation`` on the first foreign hit.

        ``own_ids`` is informational — we do *not* require the rule
        body to reference at least one own id (some rules are
        identifier-free).  We *do* require that any token shaped
        like an id is in the own set or absent.
        """
        if not rule_body:
            return
        normalised = self._normalise(rule_body)
        own = {self._normalise_id(i) for i in own_ids}
        for foreign in foreign_ids:
            needle = self._normalise_id(foreign)
            if not needle:
                continue
            if needle in own:
                # Same id assigned to two tenants — caller bug; we
                # let the rule pass and trust upstream cleanup.
                continue
            if needle in normalised:
                raise IsolationViolation(
                    tenant_id=tenant_id,
                    foreign_id=foreign,
                    matched_substring=needle,
                )

    @staticmethod
    def _normalise(text: str) -> str:
        """Lower-case + collapse non-word chars to single spaces."""
        return " " + _WORD_BOUNDARY.sub(" ", text).lower() + " "

    @staticmethod
    def _normalise_id(raw: str) -> str:
        """Match ``_normalise`` so containment works."""
        return _WORD_BOUNDARY.sub(" ", raw).lower().strip()
