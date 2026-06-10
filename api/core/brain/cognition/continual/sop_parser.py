"""SOP Parser — Converts SOP documents into procedural rules.

When a customer uploads SOP documents via /knowledge/sync with
category="sop", this parser extracts actionable rules and stores
them in ProceduralMemory with source="sop".

SOP rules get the same protection as manual rules:
- Not pruned by nightly consolidation
- Not overridden by learned rules
- Priority: immutable > manual > sop > learned

Two extraction modes:
1. LLM-based: Uses AI to extract structured rules from SOP text
2. Pattern-based: Falls back to regex for simple rule extraction
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Common SOP action patterns
_ACTION_PATTERNS: list[tuple[str, str]] = [
    (r"(?:eğer|if).+?(?:escalate|yönlendir|bildir)", "escalation"),
    (r"(?:fotoğraf|photo).+?(?:zorunlu|required|must)", "operations"),
    (r"(?:check.?out|checkout).+?(?:temizlik|clean)", "cleaning"),
    (r"(?:check.?in|checkin).+?(?:hatırlat|remind)", "timing"),
    (r"(?:yanıt|response|reply).+?(?:gelmezse|yoksa|no.?response)", "escalation"),
    (r"(?:max|maximum|azami).+?(?:saat|hour|dakika|minute)", "timing"),
    (r"(?:indirim|discount).+?(?:max|en fazla|limit)", "pricing"),
    (r"(?:yangın|fire|su baskını|flood|gaz|gas)", "safety"),
    (r"(?:owner|sahip).+?(?:bilgilendir|notify|onay|approve)", "escalation"),
]


class SOPParser:
    """Extracts procedural rules from SOP document text.

    Args:
        procedural_memory: ProceduralMemory instance for storing rules.
        llm_model: LLM model for intelligent extraction.
    """

    def __init__(
        self,
        procedural_memory: Any,
        llm_model: str = "gpt-4o-mini",
        completion: Callable[[str], str] | None = None,
    ) -> None:
        self._memory = procedural_memory
        self._llm_model = llm_model
        # completion: prompt -> model text (litellm retired; Dify
        # llm_generator adapter binds here)
        self._completion = completion

    def parse_and_store(
        self,
        sop_text: str,
        property_id: str,
        created_by: str = "sop_parser",
    ) -> list[dict[str, Any]]:
        """Parse SOP text and store extracted rules.

        Tries LLM extraction first, falls back to pattern-based.

        Args:
            sop_text: Raw SOP document text.
            property_id: Property this SOP belongs to.
            created_by: Creator identifier.

        Returns:
            List of created rule dicts.
        """
        rules = self._extract_with_llm(sop_text)
        if not rules:
            rules = self._extract_with_patterns(sop_text)

        created: list[dict[str, Any]] = []
        for rule_data in rules:
            proc = self._memory.store_manual_rule(
                property_id=property_id,
                category=rule_data.get("category", "operations"),
                rule_text=rule_data["rule"],
                confidence=1.0,
                source="sop",
                immutable=False,
                priority=rule_data.get("priority", "medium"),
                tags=["sop", rule_data.get("category", "operations")],
                created_by=created_by,
            )
            created.append(proc.to_dict())

        logger.info(
            "SOP parsed: %d rules extracted for property %s",
            len(created),
            property_id,
        )
        return created

    def _extract_with_llm(
        self,
        sop_text: str,
    ) -> list[dict[str, Any]]:
        """Use LLM to extract structured rules from SOP text.

        Args:
            sop_text: Raw SOP text.

        Returns:
            List of rule dicts with category, rule, priority.
        """
        if self._completion is None:
            return []
        try:
            text = self._completion(f"{_SOP_EXTRACTION_SYSTEM}\n\n{sop_text[:3000]}") or ""
            data = json.loads(text)
            return data.get("rules", [])
        except Exception:
            logger.debug("LLM SOP extraction failed", exc_info=True)
            return []

    @staticmethod
    def _extract_with_patterns(
        sop_text: str,
    ) -> list[dict[str, Any]]:
        """Extract rules using regex patterns (fallback).

        Args:
            sop_text: Raw SOP text.

        Returns:
            List of rule dicts.
        """
        sentences = re.split(r"[.。\n]+", sop_text)
        rules: list[dict[str, Any]] = []

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 10:
                continue

            category = _detect_category(sentence)
            if category:
                rules.append(
                    {
                        "rule": sentence,
                        "category": category,
                        "priority": "high" if category == "safety" else "medium",
                    }
                )

        return rules


def _detect_category(text: str) -> str:
    """Detect rule category from text using patterns.

    Args:
        text: Sentence to classify.

    Returns:
        Category string or empty if no match.
    """
    text_lower = text.lower()
    for pattern, category in _ACTION_PATTERNS:
        if re.search(pattern, text_lower):
            return category
    return ""


_SOP_EXTRACTION_SYSTEM = (
    "You are a rule extraction engine. Given an SOP (Standard Operating "
    "Procedure) document, extract individual actionable rules.\n\n"
    "Return valid JSON with this structure:\n"
    '{"rules": [{"rule": "rule text", "category": "category", "priority": "medium"}]}\n\n'
    "Valid categories: guest_communication, escalation, operations, timing, "
    "pricing, automation, vendor, safety, upsell, cleaning\n"
    "Valid priorities: low, medium, high, critical\n\n"
    "Extract ONLY actionable behavioral rules. Skip informational content."
)
