"""Blocker engine — prevents sensitive actions until preconditions are met.

A Blocker is a precondition that the runtime must satisfy before a
sensitive action can proceed.  Blockers sit in the execution-priority
stack *above* learned PatternRules but *below* immutable safety rules:
any active hard blocker wins over a learned high-confidence rule.

Public surface:

- ``BlockerType`` / ``BlockerSeverity`` — taxonomy enums.
- ``Blocker`` — immutable value object with resolution helpers.
- ``BlockerStore`` — storage Protocol.
- ``InMemoryBlockerStore`` — dev / test implementation.
- ``BlockerEngine`` — runtime evaluator + lifecycle manager.
- ``DEFAULT_BLOCKER_ACTIONS`` / ``DEFAULT_SEVERITY`` — policy tables.
"""

from __future__ import annotations

from brain_engine.blockers.engine import (
    BlockerEngine,
    BlockerStore,
    InMemoryBlockerStore,
)
from brain_engine.blockers.models import (
    DEFAULT_BLOCKER_ACTIONS,
    DEFAULT_SEVERITY,
    Blocker,
    BlockerSeverity,
    BlockerType,
)
from brain_engine.blockers.postgres_store import (
    PgBlockerStore,
    create_blockers_pool,
)

__all__ = [
    "DEFAULT_BLOCKER_ACTIONS",
    "DEFAULT_SEVERITY",
    "Blocker",
    "BlockerEngine",
    "BlockerSeverity",
    "BlockerStore",
    "BlockerType",
    "InMemoryBlockerStore",
    "PgBlockerStore",
    "create_blockers_pool",
]
