"""LiteLLM implementation of the :class:`LLMClassifierClient` Protocol.

The IntelligentClassifier's final-pick layer needs a transport-
agnostic LLM client.  This module ships the production
implementation — a thin async wrapper around ``litellm.acompletion``
that:

* Frames the candidate list as a structured JSON-mode prompt so
  the LLM has no room to hallucinate ids outside the offered
  set.
* Tags the prompt with the detected language so the model stays
  grounded ("answer about this Turkish message about access
  codes" vs "answer about this generic message").
* Never raises — every transport error or JSON parse failure
  returns a :class:`LLMClassificationResult` with
  ``confidence=0.0`` so the caller's fallback path triggers.

This module is *deliberately small* — the prompt engineering and
the JSON parsing are the entire surface.  Everything else
(retries, caching, telemetry) lives in higher layers so this
client stays easy to swap for a stub in tests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Final

import litellm
import structlog

from brain_engine.patterns.intelligent_classifier import (
    LLMClassificationResult,
)
from brain_engine.patterns.scenario_matcher import ScenarioCandidate

__all__ = [
    "DEFAULT_LLM_MODEL",
    "DEFAULT_LLM_TEMPERATURE",
    "LiteLLMClassifierClient",
]


DEFAULT_LLM_MODEL: Final[str] = "gpt-4o-mini"
DEFAULT_LLM_TEMPERATURE: Final[float] = 0.1
DEFAULT_MAX_TOKENS: Final[int] = 400

_VALID_DECISION_TYPES: Final[frozenset[str]] = frozenset(
    {
        "approve",
        "deny",
        "defer",
        "inform",
        "quote",
        "offer",
        "ask",
        "charge",
        "block",
        "release",
        "fetch_live_data",
        "dispatch",
        "escalate",
    }
)


logger = structlog.get_logger(__name__)


_SYSTEM_PROMPT: Final[str] = (
    "You classify hospitality guest messages into a fixed set of "
    "operational scenarios and decision types.  You will be given:\n"
    "  * the detected message language\n"
    "  * a list of candidate scenarios (each with an id and a "
    "    canonical trigger description)\n"
    "  * the guest message text\n"
    "Return a JSON object with exactly four fields:\n"
    "  {\n"
    "    \"scenario_id\": <one of the offered ids, or empty>,\n"
    "    \"decision_type\": <one of approve|deny|defer|inform|"
    "quote|offer|ask|charge|block|release|fetch_live_data|"
    "dispatch|escalate>,\n"
    "    \"confidence\": <float in [0.0, 1.0]>,\n"
    "    \"rationale\": <one-sentence explanation>\n"
    "  }\n"
    "You MUST NOT invent scenario ids.  If the message does not "
    "fit any candidate, return an empty scenario_id and the most "
    "appropriate decision_type."
)


def _format_candidates(
    candidates: Sequence[ScenarioCandidate],
) -> str:
    """Render the candidate list as a bullet block for the prompt."""
    lines: list[str] = []
    for cand in candidates:
        lines.append(
            f"* {cand.scenario_id}: {cand.text}",
        )
    return "\n".join(lines)


def _parse_response(text: str) -> LLMClassificationResult:
    """Translate the LLM's JSON output to :class:`LLMClassificationResult`.

    Tolerant of markdown fences and stray prose around the JSON.
    Returns a zero-confidence result when parsing fails — the
    caller's fallback path takes over.
    """
    if not text:
        return LLMClassificationResult(
            scenario_id="",
            decision_type="",
            confidence=0.0,
            rationale="empty LLM response",
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return LLMClassificationResult(
                scenario_id="",
                decision_type="",
                confidence=0.0,
                rationale="LLM response not valid JSON",
            )
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return LLMClassificationResult(
                scenario_id="",
                decision_type="",
                confidence=0.0,
                rationale="LLM response not valid JSON",
            )

    scenario_id = str(data.get("scenario_id") or "").strip()
    decision_type = (
        str(data.get("decision_type") or "").strip().lower()
    )
    if decision_type and decision_type not in _VALID_DECISION_TYPES:
        decision_type = ""
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    rationale = str(data.get("rationale") or "").strip()[:200]
    return LLMClassificationResult(
        scenario_id=scenario_id,
        decision_type=decision_type,
        confidence=confidence,
        rationale=rationale,
    )


class LiteLLMClassifierClient:
    """Production :class:`LLMClassifierClient` over ``litellm``.

    Stateless across calls.  The model + temperature can be tuned
    at construction time; the prompt template is fixed because
    the JSON schema the caller depends on is part of the contract.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_LLM_MODEL,
        temperature: float = DEFAULT_LLM_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if not model:
            raise ValueError("model required")
        if not 0.0 <= temperature <= 2.0:
            raise ValueError(
                "temperature must be in [0.0, 2.0]"
            )
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._log = logger.bind(
            component="litellm_classifier_client",
        )

    async def classify(
        self,
        *,
        message: str,
        language: str,
        candidates: Sequence[ScenarioCandidate],
    ) -> LLMClassificationResult:
        """Pick a scenario + decision_type from ``candidates``.

        Returns a zero-confidence result on any transport or
        parse error — the IntelligentClassifier then falls back
        to the highest-similarity candidate from Layer 2.
        """
        if not candidates:
            return LLMClassificationResult(
                scenario_id="",
                decision_type="",
                confidence=0.0,
                rationale="no candidates supplied",
            )
        user_prompt = (
            f"Language: {language}\n"
            f"Candidate scenarios:\n"
            f"{_format_candidates(candidates)}\n\n"
            f"Guest message:\n"
            f"\"\"\"\n{message}\n\"\"\""
        )
        try:
            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": _SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            self._log.warning(
                "classify.transport_error",
                error=str(exc),
            )
            return LLMClassificationResult(
                scenario_id="",
                decision_type="",
                confidence=0.0,
                rationale=(
                    f"LLM transport error: "
                    f"{type(exc).__name__}"
                ),
            )
        text = response.choices[0].message.content or ""
        return _parse_response(text)
