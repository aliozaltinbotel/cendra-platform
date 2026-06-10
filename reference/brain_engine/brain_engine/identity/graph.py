"""In-memory identity graph.

The graph stores :class:`GuestIdentity` records and a hash → id
lookup table.  Inserting a :class:`ChannelHandle` either:

- attaches it to an existing identity when the handle's hashes
  match a known identity (deterministic merge);
- mints a fresh identity otherwise.

Production wiring will swap the in-memory map for a Postgres
table keyed on the handle hashes; the API surface stays the same.
"""

from __future__ import annotations

import secrets
from dataclasses import replace
from typing import Final

import structlog

from brain_engine.identity.models import (
    ChannelHandle,
    GuestIdentity,
)


__all__ = ["DEFAULT_IDENTITY_PREFIX", "IdentityGraph"]


DEFAULT_IDENTITY_PREFIX: Final[str] = "gid_"


logger = structlog.get_logger(__name__)


class IdentityGraph:
    """Per-process :class:`GuestIdentity` + hash lookup.

    Two indexes are maintained:

    - ``_identities``: identity_id → :class:`GuestIdentity`.
    - ``_by_hash``: HMAC hash → identity_id.

    Inserting a handle whose hash already maps to an identity
    appends the handle to that identity (no merge).  Inserting a
    handle whose two hashes (email + phone) point to *different*
    identities triggers a merge — the surviving identity records
    the merge in :attr:`GuestIdentity.merged_from`.
    """

    def __init__(
        self,
        *,
        identity_prefix: str = DEFAULT_IDENTITY_PREFIX,
    ) -> None:
        if not identity_prefix:
            raise ValueError("identity_prefix required")
        self._identities: dict[str, GuestIdentity] = {}
        self._by_hash: dict[str, str] = {}
        self._prefix = identity_prefix
        self._log = logger.bind(component="identity_graph")

    def get(self, identity_id: str) -> GuestIdentity | None:
        """Return the identity record for ``identity_id`` or ``None``."""
        return self._identities.get(identity_id)

    def lookup_by_hash(self, handle_hash: str) -> str | None:
        """Return the identity_id keyed off ``handle_hash`` or ``None``."""
        return self._by_hash.get(handle_hash)

    def insert(
        self,
        *,
        handle: ChannelHandle,
    ) -> tuple[str, bool]:
        """Insert ``handle``; return ``(identity_id, merged)``.

        ``merged`` is ``True`` when the insertion collapsed two
        previously-distinct identities into one.
        """
        candidates = self._candidate_identity_ids(handle)
        if not candidates:
            new_id = self._mint_id()
            identity = GuestIdentity(
                identity_id=new_id,
                handles=(handle,),
            )
            self._identities[new_id] = identity
            self._index(handle, new_id)
            self._log.info(
                "identity.minted",
                identity_id=new_id,
                channel=handle.channel.value,
            )
            return new_id, False
        if len(candidates) == 1:
            target_id = next(iter(candidates))
            self._append_handle(target_id, handle)
            return target_id, False
        return self._merge(candidates, handle), True

    # ── internals ─────────────────────────────────────────────── #

    def _candidate_identity_ids(
        self,
        handle: ChannelHandle,
    ) -> set[str]:
        ids: set[str] = set()
        for hash_value in (handle.email_hash, handle.phone_hash):
            if hash_value is None:
                continue
            existing = self._by_hash.get(hash_value)
            if existing is not None:
                ids.add(existing)
        return ids

    def _append_handle(
        self,
        identity_id: str,
        handle: ChannelHandle,
    ) -> None:
        existing = self._identities[identity_id]
        updated = replace(
            existing,
            handles=(*existing.handles, handle),
        )
        self._identities[identity_id] = updated
        self._index(handle, identity_id)

    def _merge(
        self,
        candidates: set[str],
        handle: ChannelHandle,
    ) -> str:
        ordered = sorted(candidates)
        survivor_id = ordered[0]
        merged_handles: list[ChannelHandle] = []
        merged_from: list[str] = []
        for cand in ordered:
            record = self._identities[cand]
            merged_handles.extend(record.handles)
            merged_from.extend(record.merged_from)
            if cand != survivor_id:
                merged_from.append(cand)
                del self._identities[cand]
        merged_handles.append(handle)
        merged_record = GuestIdentity(
            identity_id=survivor_id,
            handles=tuple(merged_handles),
            merged_from=tuple(merged_from),
        )
        self._identities[survivor_id] = merged_record
        self._reindex_for(survivor_id)
        self._index(handle, survivor_id)
        self._log.info(
            "identity.merged",
            survivor=survivor_id,
            absorbed=ordered[1:],
        )
        return survivor_id

    def _index(
        self,
        handle: ChannelHandle,
        identity_id: str,
    ) -> None:
        for hash_value in (handle.email_hash, handle.phone_hash):
            if hash_value is not None:
                self._by_hash[hash_value] = identity_id

    def _reindex_for(self, identity_id: str) -> None:
        record = self._identities[identity_id]
        for handle in record.handles:
            self._index(handle, identity_id)

    def _mint_id(self) -> str:
        return self._prefix + secrets.token_urlsafe(12)
