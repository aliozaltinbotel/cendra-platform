"""Deterministic rendering of a :class:`TemporalContext` to prompt text.

Pure and LLM-free: turns the fused past+present context (Phase 2) into a
compact, sectioned text block the analysis core embeds in its user
message.  Keeping this separate from the LLM orchestration means the
exact ground truth the model sees is unit-testable without a model.

The block keeps the two temporal axes visible to the model — ``LIVE NOW``
and ``UPCOMING`` (operational-time) ahead of ``HISTORY`` (record-time,
oldest-first) — so the model can tell the present from the past.  Each
entry reuses its own one-line ``content`` summary; this module never
re-derives tier-specific semantics.

Callers bound the size via :func:`build_temporal_context`'s ``limit`` /
``since``; this renderer faithfully shows whatever it is given.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from brain_engine.memory.memory_timeline import (
        TimelineEntry,
        TimelineScope,
    )
    from brain_engine.memory.temporal_context import TemporalContext

__all__ = ["format_context"]


def format_context(context: TemporalContext) -> str:
    """Render ``context`` as a sectioned, deterministic text block."""
    lines: list[str] = [
        f"CLIENT TEMPORAL CONTEXT (as of {context.as_of.isoformat()})",
    ]
    scope = _render_scope(context.scope)
    if scope:
        lines.append(f"Scope: {scope}")
    lines.extend(
        (
            "",
            _section("LIVE NOW", context.live),
            "",
            _section("UPCOMING", context.upcoming),
            "",
            _section("HISTORY (oldest first)", context.history),
        ),
    )
    return "\n".join(lines)


def _section(title: str, entries: Sequence[TimelineEntry]) -> str:
    """One titled section; ``"<title>: none."`` when empty."""
    if not entries:
        return f"{title}: none."
    body = "\n".join(_render_entry(entry) for entry in entries)
    return f"{title} ({len(entries)}):\n{body}"


def _render_entry(entry: TimelineEntry) -> str:
    """One timeline entry as ``- <date> [<tier>/<kind>] <content>``."""
    line = (
        f"- {entry.at.date().isoformat()} "
        f"[{entry.tier}/{entry.kind}] {entry.content}"
    )
    if entry.confidence is not None:
        line += f" (confidence {entry.confidence:.2f})"
    return line


def _render_scope(scope: TimelineScope) -> str:
    """Space-joined ``label=value`` for the non-empty scope ids."""
    pairs = (
        ("property", scope.property_id),
        ("guest", scope.guest_id),
        ("customer", scope.customer_id),
        ("workspace", scope.workspace_id),
    )
    return " ".join(f"{label}={value}" for label, value in pairs if value)
