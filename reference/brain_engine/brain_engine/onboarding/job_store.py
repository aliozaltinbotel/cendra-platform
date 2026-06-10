"""Cross-replica persistence for the bootstrap job registry.

The HTTP layer (:mod:`brain_engine.api.onboarding_endpoints`)
originally stored every :class:`BootstrapJobState` in a
module-level ``dict``.  That worked on a single-replica dev
cluster but broke the moment the deployment scaled out: a
``POST /bootstrap/property/{id}/async`` and the follow-up
``GET /bootstrap/{job_id}`` would frequently land on different
pods, and the GET would return ``404`` because the second pod
had never seen the job id.

Mümin hit exactly this on dev (2026-05-12) — pods rolled in
parallel after PR #C, and his polling loop randomly saw
``404`` for jobs the audit-log endpoints could still serve.

This module restores the contract by routing every state
mutation through a transport-agnostic
:class:`BootstrapJobStore`.  The Redis backend uses a single
JSON-serialised hash key per job id with a 24-hour TTL — the
same retention budget the audit-log Streams already use, so
both surfaces age out in lock-step.

Architectural surface
---------------------

* :class:`BootstrapJobStore` — Protocol every backend honours.
* :class:`RedisBootstrapJobStore` — production backend backed by
  the same Redis client the audit bus uses.
* :class:`InMemoryBootstrapJobStore` — single-process backend
  used by tests and by single-replica dev environments without
  Redis.
* :class:`NullBootstrapJobStore` — disabled fallback that fails
  every ``get`` so the HTTP layer can detect a misconfigured
  deployment instead of silently 404-ing.

Honest scope
------------

* The store persists :meth:`BootstrapJobState.as_dict` output
  verbatim; reconstruction back into the full dataclass is
  *not* attempted because the report types nest several
  dataclasses (``EpisodeStats``, ``PatternMiningReport``, …)
  that callers do not need on the wire.  The status endpoint
  returns the raw dict.
* All Redis writes are best-effort: a transient outage
  degrades the registry to "no visibility", not "ingestion
  stops".  The pipeline never blocks on a write.
"""

from __future__ import annotations

import json
from typing import Any, Final, Protocol

import structlog


__all__ = [
    "BOOTSTRAP_JOB_STATE_TTL_SECONDS",
    "BootstrapJobStore",
    "InMemoryBootstrapJobStore",
    "NullBootstrapJobStore",
    "RedisBootstrapJobStore",
]


# Job registry entries are kept for the same 24-hour window as
# the audit-log Streams so the operator's two views of a job
# (outer state vs. event log) age out together.
BOOTSTRAP_JOB_STATE_TTL_SECONDS: Final[int] = 24 * 60 * 60

_REDIS_KEY_PREFIX: Final[str] = "bootstrap:job_state:"


logger = structlog.get_logger(__name__)


class BootstrapJobStore(Protocol):
    """Transport-agnostic interface for the per-job state registry."""

    async def put(self, job_id: str, state: dict[str, Any]) -> None:
        """Persist the latest snapshot of ``job_id``."""
        ...

    async def get(self, job_id: str) -> dict[str, Any] | None:
        """Return the latest snapshot of ``job_id`` (``None`` if unknown)."""
        ...

    async def delete(self, job_id: str) -> None:
        """Drop a job snapshot (used for explicit cleanup)."""
        ...


class InMemoryBootstrapJobStore:
    """Per-process job-state registry suitable for tests + single-replica dev.

    Mirrors the legacy module-level ``dict`` so existing
    behaviour stays bit-for-bit identical for callers that do
    not opt into Redis.
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    async def put(self, job_id: str, state: dict[str, Any]) -> None:
        self._entries[job_id] = dict(state)

    async def get(self, job_id: str) -> dict[str, Any] | None:
        snapshot = self._entries.get(job_id)
        return dict(snapshot) if snapshot is not None else None

    async def delete(self, job_id: str) -> None:
        self._entries.pop(job_id, None)


class NullBootstrapJobStore:
    """No-op store used when no transport is wired.

    Every :meth:`get` returns ``None`` and every :meth:`put` is
    a silent drop.  Wiring this lets the HTTP layer return
    ``404`` deterministically when the deployment forgot to
    configure a backend, instead of accidentally serving stale
    in-process state.
    """

    async def put(self, job_id: str, state: dict[str, Any]) -> None:
        return None

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return None

    async def delete(self, job_id: str) -> None:
        return None


class RedisBootstrapJobStore:
    """Production store backed by Redis ``SET``/``GET`` with a TTL.

    The state payload is serialised as JSON under
    ``bootstrap:job_state:{job_id}``; the TTL matches the
    audit-log stream so both views of a job age out together.

    The client API surface used here (``set``, ``get``) is the
    smallest possible — any Redis client (``redis.asyncio.Redis``,
    ``fakeredis.aioredis.FakeRedis``) honours it.

    Writes never raise:

    * A Redis outage logs a warning and returns ``None`` from
      :meth:`get`.  Callers should treat ``None`` as "job not
      visible across replicas" — the in-process task continues
      either way, so a transient outage degrades the registry
      but does not abort the bootstrap run.
    """

    def __init__(
        self,
        client: Any,
        *,
        ttl_seconds: int = BOOTSTRAP_JOB_STATE_TTL_SECONDS,
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._log = logger.bind(component="redis_bootstrap_job_store")

    async def put(self, job_id: str, state: dict[str, Any]) -> None:
        key = _REDIS_KEY_PREFIX + job_id
        try:
            payload = json.dumps(dict(state), default=str)
        except (TypeError, ValueError) as exc:
            self._log.warning(
                "job_store.serialise_failed",
                job_id=job_id,
                error=str(exc),
            )
            return
        try:
            await self._client.set(key, payload, ex=self._ttl)
        except Exception as exc:  # noqa: BLE001 - best-effort write
            self._log.warning(
                "job_store.write_failed",
                job_id=job_id,
                error=str(exc),
            )

    async def get(self, job_id: str) -> dict[str, Any] | None:
        key = _REDIS_KEY_PREFIX + job_id
        try:
            raw = await self._client.get(key)
        except Exception as exc:  # noqa: BLE001 - best-effort read
            self._log.warning(
                "job_store.read_failed",
                job_id=job_id,
                error=str(exc),
            )
            return None
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            self._log.warning(
                "job_store.parse_failed",
                job_id=job_id,
                error=str(exc),
            )
            return None

    async def delete(self, job_id: str) -> None:
        key = _REDIS_KEY_PREFIX + job_id
        try:
            await self._client.delete(key)
        except Exception as exc:  # noqa: BLE001 - best-effort delete
            self._log.warning(
                "job_store.delete_failed",
                job_id=job_id,
                error=str(exc),
            )
