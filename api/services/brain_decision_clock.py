"""Decision-time clock — the run's inbound-event timestamp (CEN-15 / CEN-27).

Bi-temporal anchoring (``docs/product/cen-15-bitemporal-anchoring-design.md``,
Part A Path 2) needs every kernel-bound External-Knowledge retrieve request to
carry the **decision-time ``T``** of the run that triggered retrieval.  Per the
adjudicated ruling (CEN-15 §E1), ``T`` is the run's *inbound-event timestamp*
(when the guest message arrived), **not** wall-clock at retrieval, so a
delayed or queued run reconstructs belief as of the event it answers.

This module is the chassis-side carrier for that timestamp:

- **Set** by the dispatch path that owns the run's inbound event — per the
  design's change ledger (§A.5 row 1) the stamping itself is kernel-side work
  riding the existing T1/T3 hooks (CEN-28, Porter).  The contextvar seam here
  is deliberately import-free so both the kernel adapters and chassis services
  can use it without layering violations.
- **Read** at the External-Knowledge request boundary
  (``services/external_knowledge_service.py``, the marked T6 block) via
  :func:`inject_as_of`.

Degenerate behavior is upstream-identical by construction: with no decision
time set for the current context, or for any endpoint other than the
configured brain-kernel endpoint, :func:`inject_as_of` leaves the request
untouched — third-party external-knowledge providers never see the field, and
the kernel serves current belief exactly as the published contract specifies
for an omitted ``as_of``.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

# Configured in docker/envs/core-services/brain.env.example (T8 env surface).
# Unset ⇒ as_of threading is inert everywhere.
KERNEL_KNOWLEDGE_ENDPOINT_ENV = "BRAIN_KERNEL_KNOWLEDGE_ENDPOINT"

_decision_time: ContextVar[datetime | None] = ContextVar("cendra_decision_time", default=None)


def set_decision_time(moment: datetime) -> Token[datetime | None]:
    """Stamp decision-time ``T`` for the current context (ruling §E1 semantics).

    ``moment`` must be timezone-aware — the wire format is RFC3339 UTC and a
    naive datetime would silently anchor to server-local time.  Returns the
    contextvar token; callers that stamp around a run must reset with
    :func:`reset_decision_time` in a ``finally`` block.
    """
    if moment.tzinfo is None:
        raise ValueError("decision time must be timezone-aware (RFC3339 UTC on the wire)")
    return _decision_time.set(moment.astimezone(UTC))


def get_decision_time() -> datetime | None:
    return _decision_time.get()


def reset_decision_time(token: Token[datetime | None]) -> None:
    _decision_time.reset(token)


def decision_time_rfc3339() -> str | None:
    """The stamped decision time as an RFC3339 UTC string, or None if unset."""
    moment = _decision_time.get()
    if moment is None:
        return None
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def kernel_knowledge_endpoint() -> str | None:
    """The configured brain-kernel External-Knowledge endpoint, normalized."""
    raw = os.environ.get(KERNEL_KNOWLEDGE_ENDPOINT_ENV, "").strip()
    normalized = raw.rstrip("/")
    return normalized or None


def is_kernel_knowledge_endpoint(endpoint: str | None) -> bool:
    kernel = kernel_knowledge_endpoint()
    if kernel is None or not endpoint:
        return False
    return endpoint.strip().rstrip("/") == kernel


def inject_as_of(request_params: dict[str, Any], endpoint: str | None) -> None:
    """Add ``as_of`` to a kernel-bound External-Knowledge retrieve request.

    Mutates ``request_params`` in place.  No-op — the request stays
    byte-identical to upstream — unless BOTH hold: the decision clock is set
    for this context AND ``endpoint`` is the configured kernel endpoint.
    """
    as_of = decision_time_rfc3339()
    if as_of is None or not is_kernel_knowledge_endpoint(endpoint):
        return
    request_params["as_of"] = as_of
