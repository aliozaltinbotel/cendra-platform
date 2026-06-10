"""Intent Classifier using LLM-based classification.

Uses async LiteLLM calls to classify user input into predefined intents.
The classifier loads a prompt template and sends it to the configured LLM,
parsing the structured JSON response into an IntentResult.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import litellm

from brain_engine.intent_controller.intents import Intent

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "intent_prompt.txt"


@dataclass(frozen=True, slots=True)
class IntentResult:
    """Result of an intent classification pass.

    Attributes:
        intent: The classified intent.
        confidence: Confidence score between 0.0 and 1.0.
        reasoning: Brief explanation of why this intent was chosen.
        raw_response: The raw LLM response string for debugging.
    """

    intent: Intent
    confidence: float
    reasoning: str
    raw_response: str = field(default="", repr=False)


class IntentClassifier:
    """Classifies user text input into actionable intents using an LLM.

    The classifier uses a prompt template to instruct the LLM, then parses
    the structured JSON response. It supports custom intent enums and
    configurable context windows.

    Args:
        model: The LiteLLM model identifier (e.g., "gpt-4o", "claude-3-sonnet").
        intent_enum: The Intent enum class to use for classification.
        context_window: Number of recent conversation turns to include.
        temperature: LLM sampling temperature (lower = more deterministic).
        prompt_template_path: Optional override for the prompt template file.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        intent_enum: type[Intent] = Intent,
        context_window: int = 5,
        temperature: float = 0.1,
        prompt_template_path: Path | None = None,
    ) -> None:
        self.model = model
        self.intent_enum = intent_enum
        self.context_window = context_window
        self.temperature = temperature
        self._prompt_template = self._load_template(
            prompt_template_path or _PROMPT_TEMPLATE_PATH
        )

    @staticmethod
    def _load_template(path: Path) -> str:
        """Load the prompt template from disk."""
        return path.read_text(encoding="utf-8")

    def _build_prompt(
        self,
        user_message: str,
        conversation_context: Sequence[dict[str, str]] | None = None,
    ) -> str:
        """Build the classification prompt from the template.

        Args:
            user_message: The current user message to classify.
            conversation_context: Recent conversation turns as
                [{"role": "user"|"assistant", "content": "..."}].

        Returns:
            The fully rendered prompt string.
        """
        intent_list = "\n".join(
            f"- {v}" for v in self.intent_enum.all_values()
        )

        if conversation_context:
            ctx_lines = []
            for turn in conversation_context[-self.context_window :]:
                role = turn.get("role", "unknown")
                content = turn.get("content", "")
                ctx_lines.append(f"  [{role}]: {content}")
            context_str = "\n".join(ctx_lines)
        else:
            context_str = "  (no prior context)"

        return self._prompt_template.format(
            intent_list=intent_list,
            user_message=user_message,
            context_window=self.context_window,
            conversation_context=context_str,
        )

    def _parse_response(self, raw: str) -> IntentResult:
        """Parse the raw LLM JSON response into an IntentResult.

        Handles malformed responses gracefully by falling back to UNKNOWN.
        """
        try:
            # Strip markdown code fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1]
                cleaned = cleaned.rsplit("```", 1)[0]
                cleaned = cleaned.strip()

            data = json.loads(cleaned)
            intent = self.intent_enum.from_string(data.get("intent", "unknown"))
            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            reasoning = str(data.get("reasoning", ""))

            return IntentResult(
                intent=intent,
                confidence=confidence,
                reasoning=reasoning,
                raw_response=raw,
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Failed to parse intent response: %s — %s", exc, raw[:200])
            return IntentResult(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                reasoning=f"Parse error: {exc}",
                raw_response=raw,
            )

    async def classify(
        self,
        user_message: str,
        conversation_context: Sequence[dict[str, str]] | None = None,
    ) -> IntentResult:
        """Classify a user message into an intent.

        Args:
            user_message: The message to classify.
            conversation_context: Optional recent conversation history.

        Returns:
            An IntentResult with the classified intent, confidence, and reasoning.

        Raises:
            litellm.exceptions.APIError: If the LLM API call fails after retries.
        """
        prompt = self._build_prompt(user_message, conversation_context)

        response = await litellm.acompletion(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise intent classification engine. Respond only with valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
            max_tokens=256,
        )

        raw_content = response.choices[0].message.content or ""
        result = self._parse_response(raw_content)

        logger.info(
            "Classified intent=%s confidence=%.2f for message: %s",
            result.intent.value,
            result.confidence,
            user_message[:80],
        )

        return result
