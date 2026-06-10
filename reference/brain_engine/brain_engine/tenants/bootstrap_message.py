"""Serialisable bootstrap intent — the producer↔worker contract.

`request_bootstrap` hands every dispatcher a
:class:`BootstrapIntentMessage` alongside the in-process workload.
The default :class:`AsyncioBootstrapDispatcher` ignores it (it runs
the workload in the serving process); the Stage 2
:class:`ServiceBusBootstrapDispatcher` serialises it onto the queue
and discards the workload, because a coroutine bound to this pod's
pipeline cannot cross the process boundary to the out-of-process
worker.

Keeping the message in its own module means the Stage 2 worker can
import the deserialiser without dragging in the dedup machinery, the
state store, or any Azure dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

__all__ = ["BootstrapIntentMessage"]


@dataclass(frozen=True, slots=True)
class BootstrapIntentMessage:
    """The data a worker needs to run one bootstrap, on its own.

    Mirrors the :func:`request_bootstrap` arguments that survive a
    process hop: the tenant scope (so the worker can rebuild a
    ``TenantContext``), the archive window, the observability reason
    and the job id that ties the queue message back to the
    ``property_state`` row's ``current_job_id``.

    Attributes:
        property_channel_id: Short Cendra channel id (the
            ``property_state`` primary key).
        customer_id: Cendra customer UUID owning the property.
        provider_type: Upper-case PMS identifier
            (``"HOSTAWAY"`` / ``"LODGIFY"`` …).
        window_days: Archive look-back the worker should pull.
        reason: Observability tag (``ui_select`` / ``first_touch``
            / ``stale_refresh`` / ``webhook``).
        job_id: Bootstrap job id; matches the row's
            ``current_job_id``.
        org_id: Optional Cendra workspace UUID.  ``None`` drops the
            optional GraphQL filter rather than sending NULL —
            same semantics as ``TenantContext.org_id``.
    """

    property_channel_id: str
    customer_id: str
    provider_type: str
    window_days: int
    reason: str
    job_id: str
    org_id: str | None = None

    def to_json(self) -> str:
        """Serialise to a compact, key-sorted JSON queue body."""

        return json.dumps(
            {
                "property_channel_id": self.property_channel_id,
                "customer_id": self.customer_id,
                "provider_type": self.provider_type,
                "window_days": self.window_days,
                "reason": self.reason,
                "job_id": self.job_id,
                "org_id": self.org_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> BootstrapIntentMessage:
        """Rebuild a message from a body produced by :meth:`to_json`.

        Raises:
            ValueError: when the payload is not a JSON object, a
                required field is missing/blank, or ``window_days``
                is not a positive integer.  The worker turns this
                into a dead-letter rather than a silent skip, so a
                malformed enqueue is never lost without a trace.
        """

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"bootstrap intent body is not JSON: {exc}",
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                "bootstrap intent body must be a JSON object",
            )
        window_days = _positive_int(data, "window_days")
        org_id = data.get("org_id")
        return cls(
            property_channel_id=_required(data, "property_channel_id"),
            customer_id=_required(data, "customer_id"),
            provider_type=_required(data, "provider_type"),
            window_days=window_days,
            reason=_required(data, "reason"),
            job_id=_required(data, "job_id"),
            org_id=(org_id if isinstance(org_id, str) and org_id else None),
        )


def _required(data: dict[str, Any], key: str) -> str:
    """Return a non-blank string field or raise ``ValueError``."""

    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or blank field {key!r}")
    return value


def _positive_int(data: dict[str, Any], key: str) -> int:
    """Return a positive int field or raise ``ValueError``.

    Booleans are rejected explicitly — ``bool`` is an ``int``
    subclass in Python, so ``True`` would otherwise pass as ``1``.
    """

    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"field {key!r} must be a positive integer")
    return value
