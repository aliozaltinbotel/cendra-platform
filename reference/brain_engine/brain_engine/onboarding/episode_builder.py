"""Split an ArchivedConversation into one-DecisionCase episodes.

A reservation thread typically contains several Q&A cycles
(``guest asks → PM replies → guest asks again``).  The default
:class:`HistoricalCaseExtractor` only reads the first guest message
and the first PM response, so a naïve bootstrap loses every cycle
after the first.

:class:`EpisodeBuilder` walks the message stream once and emits one
derivative :class:`ArchivedConversation` per Q&A cycle.  Each
derivative carries a suffixed ``conversation_id``
(``"<reservation_id>#<index>"``) so downstream dedupe keeps both the
original reservation scope and the per-episode granularity.

The builder is pure compute — no I/O, no mutation.  It is safe to
cache a single instance per process.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from brain_engine.onboarding.models import (
    ArchivedConversation,
    ArchivedMessage,
    MessageSender,
)

__all__ = [
    "DEFAULT_MAX_GAP_HOURS",
    "EpisodeBuilder",
    "EpisodeStats",
]


DEFAULT_MAX_GAP_HOURS: Final[int] = 72


@dataclass(frozen=True, slots=True)
class EpisodeStats:
    """Counters emitted by one :meth:`EpisodeBuilder.split` call.

    Attributes:
        total_messages: Number of messages in the source conversation.
        emitted_episodes: Number of derivative conversations returned.
        skipped_leading: Messages discarded before the first guest
            question (PM preambles, system notices, bot greetings).
        skipped_trailing: Messages discarded after the last closed
            episode — typically unanswered guest messages or open
            threads that did not receive a PM reply.
    """

    total_messages: int = 0
    emitted_episodes: int = 0
    skipped_leading: int = 0
    skipped_trailing: int = 0


@dataclass(frozen=True, slots=True)
class _Split:
    """Internal representation of a single detected episode."""

    messages: tuple[ArchivedMessage, ...]


class EpisodeBuilder:
    """Partition an ``ArchivedConversation`` into Q&A episodes.

    Args:
        max_gap_hours: When the elapsed time between two consecutive
            messages exceeds this threshold, the current episode is
            closed even if no new guest message has arrived.  This
            reflects the learning intuition that a long silence
            separates independent operational moments.  Use ``0`` to
            disable gap splitting (guest-message boundary only).
    """

    def __init__(
        self,
        *,
        max_gap_hours: int = DEFAULT_MAX_GAP_HOURS,
    ) -> None:
        if max_gap_hours < 0:
            raise ValueError("max_gap_hours must be >= 0")
        self._max_gap = (
            timedelta(hours=max_gap_hours) if max_gap_hours else None
        )

    def split(
        self,
        conversation: ArchivedConversation,
    ) -> tuple[tuple[ArchivedConversation, ...], EpisodeStats]:
        """Split ``conversation`` into zero-or-more derivative threads.

        Returns:
            Tuple of ``(episodes, stats)``.  ``episodes`` is empty when
            the conversation contains no closed Q&A cycle; ``stats``
            carries counters suitable for the onboarding report.
        """
        messages = conversation.messages
        if not messages:
            return (), EpisodeStats()
        splits, skipped_leading, skipped_trailing = self._scan(messages)
        if not splits:
            return (), EpisodeStats(
                total_messages=len(messages),
                emitted_episodes=0,
                skipped_leading=skipped_leading,
                skipped_trailing=skipped_trailing,
            )
        episodes = tuple(
            _derive_conversation(conversation, split.messages, index)
            for index, split in enumerate(splits)
        )
        stats = EpisodeStats(
            total_messages=len(messages),
            emitted_episodes=len(episodes),
            skipped_leading=skipped_leading,
            skipped_trailing=skipped_trailing,
        )
        return episodes, stats

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _scan(
        self,
        messages: tuple[ArchivedMessage, ...],
    ) -> tuple[list[_Split], int, int]:
        """Run the episode-boundary state machine over ``messages``."""
        splits: list[_Split] = []
        current: list[ArchivedMessage] = []
        skipped_leading = 0
        has_response = False
        for msg in messages:
            if not current:
                if msg.sender is not MessageSender.GUEST:
                    skipped_leading += 1
                    continue
                current = [msg]
                has_response = False
                continue
            if self._should_split_on_gap(current[-1], msg):
                if has_response:
                    splits.append(_Split(messages=tuple(current)))
                if msg.sender is MessageSender.GUEST:
                    current = [msg]
                    has_response = False
                else:
                    current = []
                    has_response = False
                continue
            if msg.sender is MessageSender.GUEST and has_response:
                splits.append(_Split(messages=tuple(current)))
                current = [msg]
                has_response = False
                continue
            current.append(msg)
            if msg.sender is not MessageSender.GUEST:
                has_response = True
        skipped_trailing = len(current) if not has_response else 0
        if current and has_response:
            splits.append(_Split(messages=tuple(current)))
        return splits, skipped_leading, skipped_trailing

    def _should_split_on_gap(
        self,
        previous: ArchivedMessage,
        nxt: ArchivedMessage,
    ) -> bool:
        """Return ``True`` when the gap between two messages is too large."""
        if self._max_gap is None:
            return False
        return (nxt.sent_at - previous.sent_at) > self._max_gap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_conversation(
    source: ArchivedConversation,
    messages: tuple[ArchivedMessage, ...],
    index: int,
) -> ArchivedConversation:
    """Build an :class:`ArchivedConversation` rooted at ``source`` metadata."""
    suffix = f"#{index}"
    return ArchivedConversation(
        conversation_id=f"{source.conversation_id}{suffix}",
        property_id=source.property_id,
        reservation_id=source.reservation_id,
        guest_id=source.guest_id,
        guest_name=source.guest_name,
        owner_id=source.owner_id,
        channel=source.channel,
        messages=messages,
        started_at=messages[0].sent_at,
        ended_at=messages[-1].sent_at,
        arrival_date=source.arrival_date,
        departure_date=source.departure_date,
        reservation_data=source.reservation_data,
    )
