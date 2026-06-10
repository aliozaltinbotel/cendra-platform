"""Memory-prompt extraction + aggregation.

The aggregator is the read-side fan-out that turns heterogeneous
memory stores into a single ranked list of :class:`MemoryPrompt`
objects for a :class:`GestureContext`.

Each concrete memory store (customer memory, guest history, facts,
PMS snapshot) supplies a small :class:`MemoryPromptExtractor` that
knows how to interrogate that store and emit prompts.  The
aggregator calls every extractor concurrently with
``asyncio.gather(..., return_exceptions=True)`` so that a single
flaky source never kills the whole pack assembly.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

import structlog

from brain_engine.gestures.models import (
    GestureContext,
    MemoryPrompt,
)

logger = structlog.get_logger(__name__)


_DEFAULT_PROMPT_LIMIT: int = 5


@runtime_checkable
class MemoryPromptExtractor(Protocol):
    """Pull prompts from one memory store.

    Implementations must be side-effect free and safe to call
    concurrently.  Raising is allowed — the aggregator records the
    exception and continues with the remaining extractors.
    """

    async def extract(
        self,
        context: GestureContext,
    ) -> tuple[MemoryPrompt, ...]:
        ...


class MemoryPromptAggregator:
    """Fan out across extractors, dedupe, and rank prompts."""

    def __init__(
        self,
        extractors: tuple[MemoryPromptExtractor, ...] = (),
        *,
        limit: int = _DEFAULT_PROMPT_LIMIT,
    ) -> None:
        self._extractors = tuple(extractors)
        self._limit = max(1, limit)
        self._log = logger.bind(component="memory_prompts")

    async def collect(
        self,
        context: GestureContext,
    ) -> tuple[MemoryPrompt, ...]:
        """Return the top-N ranked prompts for ``context``."""
        if not self._extractors:
            return ()
        raw = await asyncio.gather(
            *(e.extract(context) for e in self._extractors),
            return_exceptions=True,
        )
        collected: list[MemoryPrompt] = []
        for extractor, result in zip(self._extractors, raw):
            if isinstance(result, BaseException):
                self._log.warning(
                    "memory_prompts.extractor_failed",
                    extractor=type(extractor).__name__,
                    error=str(result),
                )
                continue
            collected.extend(result)
        return self._rank(self._dedupe(collected))

    # ------------------------------------------------------------------
    # Ranking + deduplication
    # ------------------------------------------------------------------

    def _dedupe(
        self,
        prompts: list[MemoryPrompt],
    ) -> list[MemoryPrompt]:
        """Remove duplicate prompts by ``(source, text)`` pair.

        When duplicates exist the one with the highest relevance wins.
        """
        best: dict[tuple[str, str], MemoryPrompt] = {}
        for p in prompts:
            key = (p.source.value, p.text)
            existing = best.get(key)
            if existing is None or p.relevance > existing.relevance:
                best[key] = p
        return list(best.values())

    def _rank(
        self,
        prompts: list[MemoryPrompt],
    ) -> tuple[MemoryPrompt, ...]:
        """Sort by urgency then relevance then creation time."""
        ordered = sorted(
            prompts,
            key=lambda p: (
                not p.is_urgent,
                -p.relevance,
                p.created_at,
            ),
        )
        return tuple(ordered[: self._limit])
