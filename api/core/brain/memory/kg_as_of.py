"""As-of reconstruction for the temporal knowledge graph.

Pure, Redis-free logic answering "what did the graph hold for this node at
wall-clock time ``T``" — transaction-time time-travel over a single
:class:`~brain_engine.memory.knowledge_graph.KnowledgeNode`.  Kept out of
:mod:`knowledge_graph` so the reconstruction rules are unit-testable without
a Redis backend and so the graph module grows by exactly one call site.

Semantics (transaction-time as-of — "as the system knew it at ``T``"):

* **Visibility.** A node is visible at ``at`` only once the system had
  recorded it (``record_time <= at``); before that the engine did not yet
  know the fact.  ``valid_from`` is used as a fallback when ``record_time``
  is missing (older rows), and when neither parses the node is treated as
  always-known rather than hidden.
* **Value.** The value at ``at`` is reconstructed from ``previous_values``.
  Each archived entry holds the content/confidence that was current *before*
  its ``changed_at`` (see ``update_knowledge`` / ``invalidate_knowledge`` in
  :mod:`knowledge_graph`), so the earliest archived entry whose ``changed_at``
  is strictly after ``at`` is the value that was live at ``at``.  If ``at``
  is at or after every change, the node's current value applies.
* **Invalidation.** If the node was invalidated by ``at`` (``valid_until``
  set and ``valid_until <= at``) it is treated as gone.  Because
  ``invalidate_knowledge`` archives the pre-invalidation value into
  ``previous_values`` first, a query for an earlier ``at`` still reconstructs
  the real value — only ``at >= valid_until`` yields ``None``.

``superseded_by`` is intentionally **not** time-filtered: nothing in the
engine sets it (there is no supersession timestamp to compare against), so
the caller's existing "skip superseded" rule is left to the caller.

The functions operate structurally on the node (attribute access +
:func:`dataclasses.replace`), so this module does not import
:mod:`knowledge_graph` at runtime and no import cycle is introduced.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.brain.memory.knowledge_graph import KnowledgeNode

__all__ = ["reconstruct_as_of"]


def reconstruct_as_of(
    node: KnowledgeNode,
    at: datetime,
) -> KnowledgeNode | None:
    """Return ``node`` as it stood at ``at``, or ``None`` if not yet known.

    Args:
        node: The current (latest) knowledge node.
        at: The wall-clock instant to reconstruct.  Naive datetimes are
            treated as UTC.

    Returns:
        A node carrying the content/confidence that was live at ``at``
        (a copy when an earlier value is reconstructed, the original when
        ``at`` falls in the current segment), or ``None`` when the node was
        not yet recorded at ``at`` or had been invalidated by then.
    """

    at = _as_utc(at)

    recorded = _parse_iso(node.record_time) or _parse_iso(node.valid_from)
    if recorded is not None and at < recorded:
        return None

    # The earliest archived value whose change happened strictly after
    # ``at`` is the value that was current at ``at``.
    for entry in node.previous_values:
        changed = _parse_iso(entry.get("changed_at"))
        if changed is not None and changed > at:
            return dataclasses.replace(
                node,
                content=str(entry.get("content", node.content)),
                confidence=_coerce_confidence(
                    entry.get("confidence"),
                    node.confidence,
                ),
            )

    # ``at`` is at or after every recorded change → the current value,
    # unless the fact had been invalidated by then.
    invalid_at = _parse_iso(node.valid_until)
    if invalid_at is not None and at >= invalid_at:
        return None
    return node


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string to an aware UTC datetime, or ``None``."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    """Coerce a datetime to aware UTC (naive is assumed UTC)."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _coerce_confidence(value: Any, fallback: float) -> float:
    """Best-effort float for an archived confidence, else ``fallback``."""

    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
