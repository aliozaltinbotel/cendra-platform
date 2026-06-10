"""Deterministic text renderer for property timelines.

Produces a plain-text narrative from a chronologically-sorted tuple of
:class:`TimelineEvent` objects.  The output is safe for both chat
delivery and TTS ingestion: no Markdown, no emoji, and no
speaker-dependent punctuation.

Two verbosity levels are supported:

- :attr:`RenderStyle.CONCISE` caps each group at three bullet lines
  and adds a "+ N more" suffix when exceeded.
- :attr:`RenderStyle.FULL` emits every event.

Events are grouped by calendar month.  Grouping keeps narratives
readable for long windows without re-running an LLM.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Final, Iterable, Sequence

from brain_engine.narrative.models import (
    EventKind,
    RenderStyle,
    TimelineEvent,
    TimelineRange,
)

__all__ = ["TextRenderer"]


_DEFAULT_CONCISE_CAP: Final[int] = 3
_MONTH_FORMAT: Final[str] = "%B %Y"


class TextRenderer:
    """Deterministic text renderer.

    The renderer is pure: the same inputs always produce the same
    output.  No LLM calls, no network, no randomness.
    """

    def __init__(
        self,
        *,
        style: RenderStyle = RenderStyle.CONCISE,
        concise_cap: int = _DEFAULT_CONCISE_CAP,
    ) -> None:
        self._style = style
        self._concise_cap = max(concise_cap, 1)

    @property
    def style(self) -> RenderStyle:
        return self._style

    def with_style(self, style: RenderStyle) -> TextRenderer:
        """Return a renderer with the same config but a new style."""
        if style is self._style:
            return self
        return TextRenderer(style=style, concise_cap=self._concise_cap)

    def render(
        self,
        events: Sequence[TimelineEvent],
        *,
        property_label: str,
        range: TimelineRange,
    ) -> str:
        """Render ``events`` into a plain-text narrative."""
        label = property_label or "this property"
        if not events:
            return (
                f"No recorded events for {label} "
                f"in the last {range.span_days} days."
            )

        lines: list[str] = [self._opening_line(label, events, range)]
        for month, bucket in _group_by_month(events):
            lines.append("")
            lines.append(f"{month}:")
            lines.extend(self._render_bucket(bucket))
        lines.append("")
        lines.append(self._totals_line(events))
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _render_bucket(self, bucket: Sequence[TimelineEvent]) -> list[str]:
        if self._style is RenderStyle.FULL or len(bucket) <= self._concise_cap:
            return [_format_bullet(event) for event in bucket]

        visible = bucket[: self._concise_cap]
        hidden = len(bucket) - self._concise_cap
        rendered = [_format_bullet(event) for event in visible]
        rendered.append(f"  + {hidden} more event{'s' if hidden != 1 else ''}")
        return rendered

    @staticmethod
    def _opening_line(
        label: str,
        events: Sequence[TimelineEvent],
        range: TimelineRange,
    ) -> str:
        count = len(events)
        noun = "event" if count == 1 else "events"
        return (
            f"Over the last {range.span_days} days {label} had "
            f"{count} recorded {noun}."
        )

    @staticmethod
    def _totals_line(events: Sequence[TimelineEvent]) -> str:
        counts = Counter(event.kind for event in events)
        ordered = sorted(counts.items(), key=lambda pair: pair[0].value)
        parts = [f"{count} {kind.value}" for kind, count in ordered]
        return "Totals: " + ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _group_by_month(
    events: Iterable[TimelineEvent],
) -> list[tuple[str, list[TimelineEvent]]]:
    """Group events into calendar-month buckets, preserving order."""
    buckets: dict[str, list[TimelineEvent]] = {}
    for event in events:
        key = event.occurred_at.strftime(_MONTH_FORMAT)
        buckets.setdefault(key, []).append(event)
    return list(buckets.items())


def _format_bullet(event: TimelineEvent) -> str:
    """Format one event as a single line."""
    date = event.occurred_at.strftime("%Y-%m-%d")
    kind = _kind_label(event.kind)
    summary = event.summary.strip() or "(no summary)"
    return f"  - {date} {kind}: {summary}"


def _kind_label(kind: EventKind) -> str:
    """Return a human-friendly label for a kind."""
    return kind.value.replace("_", " ")
