"""High-level :class:`GestureService` orchestrator.

Composes :class:`MemoryPromptAggregator` and :class:`PatternGestureBuilder`
into a single ``assemble`` coroutine that returns a fully populated
:class:`GesturePack` for a :class:`GestureContext`.  Callers (the V2
decision-card endpoint, the chat surface, the vendor-dispatch flow)
hit this service once per decision slot.
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog

from brain_engine.gestures.gestures import PatternGestureBuilder
from brain_engine.gestures.models import (
    GestureContext,
    GesturePack,
)
from brain_engine.gestures.prompts import MemoryPromptAggregator
from brain_engine.patterns.models import PatternRule

logger = structlog.get_logger(__name__)


class GestureService:
    """Assemble prompts + gestures into a single pack."""

    def __init__(
        self,
        *,
        aggregator: MemoryPromptAggregator,
        builder: PatternGestureBuilder,
    ) -> None:
        self._aggregator = aggregator
        self._builder = builder
        self._log = logger.bind(component="gesture_service")

    async def assemble(
        self,
        *,
        context: GestureContext,
        rules: Iterable[PatternRule] = (),
    ) -> GesturePack:
        """Return a :class:`GesturePack` for the given context."""
        prompts = await self._aggregator.collect(context)
        gestures = self._builder.build(rules, context=context)
        pack = GesturePack(
            context=context,
            prompts=prompts,
            gestures=gestures,
        )
        self._log.debug(
            "gesture_pack.assembled",
            property_id=context.property_id,
            scenario=context.scenario.value,
            prompt_count=len(prompts),
            gesture_count=len(gestures),
        )
        return pack
