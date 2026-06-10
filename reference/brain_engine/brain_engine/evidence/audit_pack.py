"""Tamper-evident audit pack for evidence bundles.

Advisory §12 evidence calls for an "audit-ready format" that a
human can inspect *and* an external auditor can verify hasn't
been edited after the fact.  This module ships exactly that:

* :class:`AuditEntry` — one immutable row.  Each entry pins the
  hash of the previous entry; flipping any byte in row N breaks
  the chain hash on row N+1 (and every row after that).
* :class:`AuditPack` — a frozen tuple of entries plus the head
  ``chain_hash``.  Verifying integrity is a single pass.
* :class:`AuditPackBuilder` — collects entries in order and
  freezes them into a pack on ``.build()``.

Hashing uses ``blake2b`` with the canonical JSON encoding from
:func:`json.dumps(..., sort_keys=True, separators=(",", ":"))`,
so the same logical entry on two machines hashes to the same
value regardless of dict insertion order.

Pure compute, deterministic, stdlib-only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Final

__all__ = [
    "AUDIT_ROOT_HASH",
    "AuditEntry",
    "AuditIntegrityError",
    "AuditPack",
    "AuditPackBuilder",
]


AUDIT_ROOT_HASH: Final[str] = "0" * 64
"""Sentinel hash for the entry that opens a fresh chain."""


class AuditIntegrityError(RuntimeError):
    """Raised when a pack fails chain-hash verification."""


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One row in the audit chain.

    Attributes:
        sequence: Zero-based position inside the pack.
        occurred_at: Timezone-aware UTC timestamp.  Naive
            datetimes are rejected — auditors need a reliable
            timeline.
        actor: Identifier of who performed ``action``.  Free-form
            string; conventionally ``"agent:<name>"`` for
            machine actors and ``"user:<id>"`` for humans.
        action: Short verb describing what happened.
        payload: Structured detail.  Keys are sorted at hash
            time so caller insertion order does not affect the
            chain hash.
        previous_hash: Chain hash of the prior entry (or
            ``AUDIT_ROOT_HASH`` for the first row).
        entry_hash: Chain hash of *this* entry, computed as
            ``blake2b(canonical_json(self_without_hash))``.
            Always 64 lowercase hex chars.
    """

    sequence: int
    occurred_at: datetime
    actor: str
    action: str
    payload: Mapping[str, object]
    previous_hash: str
    entry_hash: str

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if not self.actor:
            raise ValueError("actor must not be empty")
        if not self.action:
            raise ValueError("action must not be empty")
        if not _is_hex64(self.previous_hash):
            raise ValueError(
                "previous_hash must be 64 lowercase hex chars",
            )
        if not _is_hex64(self.entry_hash):
            raise ValueError(
                "entry_hash must be 64 lowercase hex chars",
            )
        object.__setattr__(
            self,
            "payload",
            MappingProxyType(dict(self.payload)),
        )


def _canonical(blob: object) -> bytes:
    return json.dumps(
        blob,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError(
                "naive datetime cannot be hashed deterministically",
            )
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError(
        f"cannot serialise {type(value).__name__} for audit hashing",
    )


def _is_hex64(value: str) -> bool:
    if len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value)


def _hash_row(
    *,
    sequence: int,
    occurred_at: datetime,
    actor: str,
    action: str,
    payload: Mapping[str, object],
    previous_hash: str,
) -> str:
    body = {
        "sequence": sequence,
        "occurred_at": occurred_at,
        "actor": actor,
        "action": action,
        "payload": dict(payload),
        "previous_hash": previous_hash,
    }
    return hashlib.blake2b(
        _canonical(body),
        digest_size=32,
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditPack:
    """Immutable, hash-chained audit log.

    Attributes:
        entries: Tuple of :class:`AuditEntry` rows in chain order.
        head_hash: ``entries[-1].entry_hash`` for empty-pack
            checks; ``AUDIT_ROOT_HASH`` when ``entries`` is empty.
    """

    entries: tuple[AuditEntry, ...] = field(default_factory=tuple)
    head_hash: str = AUDIT_ROOT_HASH

    def __post_init__(self) -> None:
        if not _is_hex64(self.head_hash):
            raise ValueError(
                "head_hash must be 64 lowercase hex chars",
            )

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def verify(self) -> None:
        """Walk the chain.  Raise on any mismatch."""
        previous = AUDIT_ROOT_HASH
        for index, entry in enumerate(self.entries):
            if entry.sequence != index:
                raise AuditIntegrityError(
                    f"entry {index}: sequence drift "
                    f"(expected {index}, got {entry.sequence})",
                )
            if entry.previous_hash != previous:
                raise AuditIntegrityError(
                    f"entry {index}: previous_hash mismatch",
                )
            recomputed = _hash_row(
                sequence=entry.sequence,
                occurred_at=entry.occurred_at,
                actor=entry.actor,
                action=entry.action,
                payload=entry.payload,
                previous_hash=entry.previous_hash,
            )
            if recomputed != entry.entry_hash:
                raise AuditIntegrityError(
                    f"entry {index}: entry_hash mismatch",
                )
            previous = entry.entry_hash
        if self.head_hash != previous:
            raise AuditIntegrityError(
                "head_hash does not match last entry hash",
            )


class AuditPackBuilder:
    """Collect entries, then freeze into an :class:`AuditPack`."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._head: str = AUDIT_ROOT_HASH

    def append(
        self,
        *,
        actor: str,
        action: str,
        occurred_at: datetime,
        payload: Mapping[str, object] | None = None,
    ) -> AuditEntry:
        """Append a new entry and return it."""
        if not actor:
            raise ValueError("actor must not be empty")
        if not action:
            raise ValueError("action must not be empty")
        if occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        body_payload: Mapping[str, object] = payload or {}
        sequence = len(self._entries)
        entry_hash = _hash_row(
            sequence=sequence,
            occurred_at=occurred_at,
            actor=actor,
            action=action,
            payload=body_payload,
            previous_hash=self._head,
        )
        entry = AuditEntry(
            sequence=sequence,
            occurred_at=occurred_at,
            actor=actor,
            action=action,
            payload=body_payload,
            previous_hash=self._head,
            entry_hash=entry_hash,
        )
        self._entries.append(entry)
        self._head = entry_hash
        return entry

    def build(self) -> AuditPack:
        """Freeze the collected entries into an immutable pack."""
        return AuditPack(
            entries=tuple(self._entries),
            head_hash=self._head,
        )

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def head_hash(self) -> str:
        return self._head

    def entries(self) -> Sequence[AuditEntry]:
        """Snapshot of entries appended so far."""
        return tuple(self._entries)
