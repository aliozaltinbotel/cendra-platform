"""Causal graph builder (Gap #3 part 2).

Runs every registered :class:`CausalInferenceRule` concurrently, merges
their edges, and produces a single :class:`CausalGraph`.  One rule
crashing never sinks the build: :func:`asyncio.gather` is invoked with
``return_exceptions=True`` and failures are logged + tracked in the
resulting graph's ``meta`` mapping.
"""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import Sequence

import structlog

from brain_engine.causal.errors import CausalInferenceError
from brain_engine.causal.inference import CausalInferenceRule
from brain_engine.causal.models import CausalEdge, CausalGraph
from brain_engine.narrative.models import TimelineEvent

__all__ = ["CausalGraphBuilder"]


_LOGGER = structlog.get_logger(__name__)


class CausalGraphBuilder:
    """Compose a :class:`CausalGraph` from events and inference rules.

    Configuration:

    - ``min_confidence`` — edges below this score are dropped after
      dedupe.
    - ``max_edges`` — upper bound on the returned edge count; the
      top-N by confidence survive.  ``0`` means unlimited.
    """

    def __init__(
        self,
        rules: Sequence[CausalInferenceRule],
        *,
        min_confidence: float = 0.25,
        max_edges: int = 500,
    ) -> None:
        if min_confidence < 0.0 or min_confidence > 1.0:
            raise CausalInferenceError(
                "min_confidence must be within [0.0, 1.0]"
            )
        if max_edges < 0:
            raise CausalInferenceError("max_edges must be non-negative")
        self._rules = tuple(rules)
        self._min_confidence = float(min_confidence)
        self._max_edges = int(max_edges)

    async def build(
        self,
        events: Sequence[TimelineEvent],
    ) -> CausalGraph:
        """Run all rules and return a merged, deduped, capped graph."""
        if not self._rules or not events:
            return CausalGraph(
                events=tuple(events),
                edges=(),
                meta=_build_meta(
                    rule_tags=self._rule_tags(),
                    edge_count=0,
                    failed_rules=(),
                ),
            )

        raw = await asyncio.gather(
            *(rule.infer(events) for rule in self._rules),
            return_exceptions=True,
        )

        collected, failed = self._collect(raw)
        merged = _merge(collected)
        filtered = tuple(
            edge for edge in merged if edge.confidence >= self._min_confidence
        )
        ranked = sorted(filtered, key=lambda e: e.confidence, reverse=True)
        capped = tuple(ranked[: self._max_edges]) if self._max_edges else tuple(ranked)

        return CausalGraph(
            events=tuple(events),
            edges=capped,
            meta=_build_meta(
                rule_tags=self._rule_tags(),
                edge_count=len(capped),
                failed_rules=failed,
            ),
        )

    def _rule_tags(self) -> tuple[str, ...]:
        return tuple(getattr(rule, "tag", type(rule).__name__) for rule in self._rules)

    def _collect(
        self,
        raw: Sequence[object],
    ) -> tuple[list[CausalEdge], tuple[str, ...]]:
        collected: list[CausalEdge] = []
        failed: list[str] = []
        for rule, outcome in zip(self._rules, raw):
            tag = getattr(rule, "tag", type(rule).__name__)
            if isinstance(outcome, BaseException):
                _LOGGER.warning(
                    "causal.rule.failed",
                    tag=tag,
                    error=str(outcome),
                )
                failed.append(tag)
                continue
            for edge in outcome:  # type: ignore[assignment]
                if isinstance(edge, CausalEdge):
                    collected.append(edge)
        return collected, tuple(failed)


def _merge(edges: Sequence[CausalEdge]) -> tuple[CausalEdge, ...]:
    """Deduplicate by ``(source, target, kind)`` keeping the best score."""
    best: dict[tuple[str, str, str], CausalEdge] = {}
    for edge in edges:
        key = edge.dedupe_key
        current = best.get(key)
        if current is None or edge.confidence > current.confidence:
            best[key] = edge
    return tuple(best.values())


def _build_meta(
    *,
    rule_tags: tuple[str, ...],
    edge_count: int,
    failed_rules: tuple[str, ...],
) -> MappingProxyType[str, object]:
    return MappingProxyType(
        {
            "rule_tags": rule_tags,
            "edge_count": edge_count,
            "failed_rules": failed_rules,
        }
    )
