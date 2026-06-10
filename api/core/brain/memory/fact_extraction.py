"""Conversation fact extraction (mem0ai replaced per Batch 3 decision).

The reference wrapped the ``mem0ai`` library (``memory/mem0_extractor.py``
@a761e29) to pull durable facts out of guest dialogues.  Per the Batch 3
session decision the dependency is dropped: extraction now runs through
Dify's own LLM surface.  The kernel keeps the value objects and the
:class:`FactExtractor` Protocol; :class:`LLMFactExtractor` does the
prompt + parse work over an injectable ``completion`` callable, and the
thin adapter that binds it to Dify's ``llm_generator`` / model manager
(tenant + model config live there) lands with the runtime wiring in
Batch 4/5.  :class:`NullFactExtractor` preserves the reference's
defensive degraded mode (mem0 missing → empty results, never a crash).

Fact categories (``preference`` / ``rule`` / ``info`` / ``incident``)
are mechanism vocabulary shared with the fact store and stay in the
kernel.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol, runtime_checkable

__all__ = [
    "DEFAULT_FACT_TYPE",
    "FACT_TYPES",
    "ExtractedFact",
    "FactExtractor",
    "LLMFactExtractor",
    "MemoryUpdateResult",
    "NullFactExtractor",
]

logger = logging.getLogger(__name__)


FACT_TYPES: Final[frozenset[str]] = frozenset({"preference", "rule", "info", "incident"})
DEFAULT_FACT_TYPE: Final[str] = "info"


@dataclass(frozen=True, slots=True)
class ExtractedFact:
    """One fact extracted from a conversation.

    Attributes:
        fact_id: Unique fact identifier (UUID).
        content: Textual content of the fact.
        fact_type: Category — ``preference``, ``rule``, ``info`` or
            ``incident``.
        entity_id: Entity the fact concerns (guest, property, booking).
        confidence: Confidence in ``[0.0, 1.0]``.
        source: Source identifier (e.g. episode_id).
        extracted_at: ISO timestamp of extraction.
        keywords: Keywords associated with the fact.
    """

    fact_id: str
    content: str
    fact_type: str
    entity_id: str = ""
    confidence: float = 1.0
    source: str = ""
    extracted_at: str = ""
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryUpdateResult:
    """Result of one memory update pass.

    Attributes:
        added / updated / deleted / unchanged: Operation counts.
        facts: Facts touched by the update.
    """

    added: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    facts: tuple[ExtractedFact, ...] = ()


@runtime_checkable
class FactExtractor(Protocol):
    """Extraction seam consumed by consolidation / recall paths."""

    def is_available(self) -> bool:
        """Whether the extractor can run (degrades gracefully when not)."""
        ...

    def extract_facts(
        self,
        messages: Sequence[dict[str, str]],
        *,
        entity_id: str = "",
        source: str = "",
    ) -> list[ExtractedFact]:
        """Return durable facts found in ``messages`` (possibly empty)."""
        ...


class NullFactExtractor:
    """Degraded-mode extractor — mirrors the reference's mem0-absent path."""

    def is_available(self) -> bool:
        return False

    def extract_facts(
        self,
        messages: Sequence[dict[str, str]],
        *,
        entity_id: str = "",
        source: str = "",
    ) -> list[ExtractedFact]:
        return []


_EXTRACTION_PROMPT: Final[str] = """\
You extract durable operational facts from a property-management conversation.

Return a JSON array. Each element:
  {{"content": "<one self-contained fact>",
    "fact_type": "preference|rule|info|incident",
    "confidence": <0.0-1.0>,
    "keywords": ["..."]}}

Only include facts worth remembering across future conversations
(stable preferences, standing rules, verified information, incidents).
Ignore greetings, transient chatter and anything already implied by the
booking record. Return [] when nothing qualifies.

Conversation:
{conversation}
"""


class LLMFactExtractor:
    """LLM-backed :class:`FactExtractor` over an injectable completion seam.

    ``completion`` maps a prompt string to the model's text response —
    in production a thin adapter over Dify's ``llm_generator`` (Batch
    4/5); in tests, a stub.  All failures degrade to an empty result,
    matching the reference's defensive posture.
    """

    def __init__(self, completion: Callable[[str], str]) -> None:
        self._completion = completion

    def is_available(self) -> bool:
        return True

    def extract_facts(
        self,
        messages: Sequence[dict[str, str]],
        *,
        entity_id: str = "",
        source: str = "",
    ) -> list[ExtractedFact]:
        if not messages:
            return []
        conversation = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
        prompt = _EXTRACTION_PROMPT.format(conversation=conversation)
        try:
            raw = self._completion(prompt)
        except Exception:
            logger.exception("fact extraction completion failed — degrading to empty result")
            return []
        return self._parse(raw, entity_id=entity_id, source=source)

    def _parse(self, raw: str, *, entity_id: str, source: str) -> list[ExtractedFact]:
        payload = _extract_json_array(raw)
        if payload is None:
            logger.warning("fact extraction returned unparseable output — degrading to empty result")
            return []
        facts: list[ExtractedFact] = []
        now = datetime.now(UTC).isoformat()
        for item in payload:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            fact_type = str(item.get("fact_type", DEFAULT_FACT_TYPE)).strip().lower()
            if fact_type not in FACT_TYPES:
                fact_type = DEFAULT_FACT_TYPE
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 1.0))))
            except (TypeError, ValueError):
                confidence = 1.0
            raw_keywords = item.get("keywords") or ()
            keywords = tuple(str(k) for k in raw_keywords) if isinstance(raw_keywords, list | tuple) else ()
            facts.append(
                ExtractedFact(
                    fact_id=str(uuid.uuid4()),
                    content=content,
                    fact_type=fact_type,
                    entity_id=entity_id,
                    confidence=confidence,
                    source=source,
                    extracted_at=now,
                    keywords=keywords,
                )
            )
        return facts


def _extract_json_array(raw: str) -> list[Any] | None:
    """Pull the first JSON array out of a (possibly fenced) LLM response."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match is None:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) else None
