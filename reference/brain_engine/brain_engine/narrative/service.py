"""High-level narrative orchestrator.

:class:`NarrativeService` is the single entry point that FastAPI (or
any other caller) uses to build a property timeline.  It owns the
composer and the renderers; callers pick a format (``json`` / ``text``
/ ``voice``) and optionally ask for LLM rewriting.

The service is intentionally thin — composition, rendering, and voice
synthesis are delegated to their own classes so each piece stays unit
testable in isolation.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import structlog

from brain_engine.narrative.composition import TimelineComposer
from brain_engine.narrative.errors import VoiceSynthesisUnavailable
from brain_engine.narrative.llm_renderer import LLMNarrativeRenderer
from brain_engine.narrative.models import (
    Narrative,
    RenderStyle,
    TimelineEvent,
    TimelineRange,
)
from brain_engine.narrative.ownership import PropertyOwnershipResolver
from brain_engine.narrative.text_renderer import TextRenderer
from brain_engine.narrative.voice_renderer import VoiceRenderer

__all__ = ["NarrativeService"]


logger = structlog.get_logger(__name__)


class NarrativeService:
    """Orchestrator for property timeline narratives."""

    def __init__(
        self,
        *,
        composer: TimelineComposer,
        text_renderer: TextRenderer,
        voice_renderer: VoiceRenderer | None = None,
        llm_renderer: LLMNarrativeRenderer | None = None,
        ownership_resolver: PropertyOwnershipResolver | None = None,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._composer = composer
        self._text = text_renderer
        self._voice = voice_renderer
        self._llm = llm_renderer
        self._ownership = ownership_resolver
        self._logger = logger or globals()["logger"]

    async def _resolve_customer(
        self,
        property_id: str,
        customer_id: str | None,
    ) -> str | None:
        """Fall back to the ownership resolver when no customer is given."""
        if customer_id or self._ownership is None:
            return customer_id
        return await self._ownership.resolve(property_id)

    @property
    def voice_available(self) -> bool:
        return self._voice is not None

    @property
    def llm_available(self) -> bool:
        return self._llm is not None

    async def collect_events(
        self,
        *,
        property_id: str,
        range: TimelineRange,
        customer_id: str | None = None,
        reservation_id: str | None = None,
        guest_id: str | None = None,
        include_ops: bool = True,
    ) -> tuple[TimelineEvent, ...]:
        """Return just the composed timeline events for downstream use.

        Callers that need the raw events (for example the causal
        navigation endpoint from Gap #3) should use this method so they
        do not trigger text or voice rendering.
        """
        resolved = await self._resolve_customer(property_id, customer_id)
        return await self._composer.compose(
            property_id=property_id,
            range=range,
            customer_id=resolved,
            reservation_id=reservation_id,
            guest_id=guest_id,
            include_ops=include_ops,
        )

    async def build_json(
        self,
        *,
        property_id: str,
        range: TimelineRange,
        customer_id: str | None = None,
        reservation_id: str | None = None,
        guest_id: str | None = None,
        include_ops: bool = True,
        property_label: str = "",
        style: RenderStyle = RenderStyle.CONCISE,
        use_llm: bool = False,
    ) -> Narrative:
        """Compose the timeline and return a fully-populated :class:`Narrative`."""
        resolved = await self._resolve_customer(property_id, customer_id)
        events = await self._composer.compose(
            property_id=property_id,
            range=range,
            customer_id=resolved,
            reservation_id=reservation_id,
            guest_id=guest_id,
            include_ops=include_ops,
        )
        skeleton = self._text.with_style(style).render(
            events, property_label=property_label, range=range
        )
        text = skeleton
        if use_llm and self._llm is not None:
            text = await self._llm.rewrite(skeleton, events, style=style)
        return Narrative(
            text=text,
            events=events,
            range=range,
            meta={
                "style": style.value,
                "used_llm": bool(use_llm and self._llm is not None),
                "source_count": len(self._composer.sources),
                "reservation_id": reservation_id or "",
                "guest_id": guest_id or "",
                "customer_id": resolved or "",
                "owner_resolved": bool(
                    resolved and not customer_id
                ),
            },
        )

    async def build_text(
        self,
        *,
        property_id: str,
        range: TimelineRange,
        customer_id: str | None = None,
        reservation_id: str | None = None,
        guest_id: str | None = None,
        include_ops: bool = True,
        property_label: str = "",
        style: RenderStyle = RenderStyle.CONCISE,
        use_llm: bool = False,
    ) -> Narrative:
        """Alias of :meth:`build_json` — text is the primary rendering path."""
        return await self.build_json(
            property_id=property_id,
            range=range,
            customer_id=customer_id,
            reservation_id=reservation_id,
            guest_id=guest_id,
            include_ops=include_ops,
            property_label=property_label,
            style=style,
            use_llm=use_llm,
        )

    async def build_voice(
        self,
        *,
        property_id: str,
        range: TimelineRange,
        customer_id: str | None = None,
        reservation_id: str | None = None,
        guest_id: str | None = None,
        include_ops: bool = True,
        property_label: str = "",
        style: RenderStyle = RenderStyle.CONCISE,
        use_llm: bool = False,
    ) -> tuple[Narrative, bytes, str]:
        """Compose a narrative and render it to TTS audio.

        Raises :class:`VoiceSynthesisUnavailable` when the service was
        built without a :class:`VoiceRenderer`.
        """
        if self._voice is None:
            raise VoiceSynthesisUnavailable(
                "Voice renderer is not configured"
            )
        narrative = await self.build_text(
            property_id=property_id,
            range=range,
            customer_id=customer_id,
            reservation_id=reservation_id,
            guest_id=guest_id,
            include_ops=include_ops,
            property_label=property_label,
            style=style,
            use_llm=use_llm,
        )
        audio, content_type = await self._voice.synthesize(narrative.text)
        return narrative, audio, content_type

    async def stream_voice(
        self,
        *,
        property_id: str,
        range: TimelineRange,
        customer_id: str | None = None,
        reservation_id: str | None = None,
        guest_id: str | None = None,
        include_ops: bool = True,
        property_label: str = "",
        style: RenderStyle = RenderStyle.CONCISE,
        use_llm: bool = False,
    ) -> tuple[Narrative, AsyncIterator[bytes], str]:
        """Compose a narrative and return a TTS audio byte stream.

        Returns ``(narrative, audio_iterator, content_type)``.  Raises
        :class:`VoiceSynthesisUnavailable` when the voice renderer is
        not wired or the provider does not expose a streaming call.
        """
        if self._voice is None:
            raise VoiceSynthesisUnavailable(
                "Voice renderer is not configured"
            )
        narrative = await self.build_text(
            property_id=property_id,
            range=range,
            customer_id=customer_id,
            reservation_id=reservation_id,
            guest_id=guest_id,
            include_ops=include_ops,
            property_label=property_label,
            style=style,
            use_llm=use_llm,
        )
        return (
            narrative,
            self._voice.stream(narrative.text),
            self._voice.stream_content_type,
        )


