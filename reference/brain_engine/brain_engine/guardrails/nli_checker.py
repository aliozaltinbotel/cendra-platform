"""NLI Checker — Natural Language Inference for logical contradiction detection.

Layer 3 of the Neuro-Symbolic 4-layer validation cascade.
Uses DeBERTa-v3-based NLI model to classify premise/hypothesis pairs as:
  - ENTAILMENT: hypothesis follows from premise
  - CONTRADICTION: hypothesis conflicts with premise
  - NEUTRAL: no clear logical relationship

When a local DeBERTa model is not available, falls back to GPT-4o Mini
served by the tenant's Azure OpenAI deployment as a lightweight NLI
classifier.  Public ``api.openai.com`` is never called.

Latency: ~50ms (local GPU) or ~500ms (Azure GPT-4o Mini).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class NLILabel(StrEnum):
    """NLI classification labels."""

    ENTAILMENT = "entailment"
    CONTRADICTION = "contradiction"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class NLIResult:
    """Result of an NLI classification.

    Attributes:
        premise: The premise statement.
        hypothesis: The hypothesis to classify.
        label: Predicted NLI label.
        confidence: Confidence score (0-1).
        method: Which model was used (deberta, gpt4o_mini, rules).
    """

    premise: str
    hypothesis: str
    label: NLILabel
    confidence: float
    method: str = "rules"


# Rule-based contradiction patterns for fast pre-screening
RULE_PATTERNS: list[dict[str, Any]] = [
    {
        "premise_contains": ["available", "free", "confirmed"],
        "hypothesis_contains": ["unavailable", "busy", "cancelled"],
        "label": NLILabel.CONTRADICTION,
        "description": "Availability conflict",
    },
    {
        "premise_contains": ["checkout", "leaving", "departing"],
        "hypothesis_contains": ["just arrived", "checking in", "checked in"],
        "label": NLILabel.CONTRADICTION,
        "description": "Check-in/checkout state conflict",
    },
    {
        "premise_contains": ["no damage", "clean", "perfect condition"],
        "hypothesis_contains": ["damaged", "broken", "cracked", "stained"],
        "label": NLILabel.CONTRADICTION,
        "description": "Property condition conflict",
    },
    {
        "premise_contains": ["approved", "accepted", "confirmed"],
        "hypothesis_contains": ["denied", "rejected", "declined"],
        "label": NLILabel.CONTRADICTION,
        "description": "Decision state conflict",
    },
    {
        "premise_contains": ["paid", "payment received"],
        "hypothesis_contains": ["unpaid", "payment pending", "owes"],
        "label": NLILabel.CONTRADICTION,
        "description": "Payment state conflict",
    },
]


class NLIChecker:
    """Natural Language Inference contradiction detection.

    Cascaded approach:
    1. Rule-based keyword matching (instant, free)
    2. DeBERTa-v3 local model (50ms, if available)
    3. Azure GPT-4o Mini fallback (500ms) when the tenant config is
       complete; otherwise the checker returns ``NEUTRAL`` rather than
       calling the public OpenAI surface.

    Args:
        model_name: HuggingFace model name for local NLI.
        use_local_model: Whether to attempt loading local DeBERTa model.
    """

    def __init__(
        self,
        model_name: str = "microsoft/deberta-v3-base-mnli-fever-anli",
        use_local_model: bool = False,
    ) -> None:
        self._model_name = model_name
        self._local_pipeline: Any | None = None

        if use_local_model:
            self._load_local_model()

    def _load_local_model(self) -> None:
        """Attempt to load the local DeBERTa NLI model."""
        try:
            from transformers import pipeline
            self._local_pipeline = pipeline(
                "text-classification",
                model=self._model_name,
                device=-1,  # CPU
            )
            logger.info("Loaded local NLI model: %s", self._model_name)
        except ImportError:
            logger.info("transformers not available — using API fallback for NLI")
        except Exception:
            logger.exception("Failed to load local NLI model")

    async def check_contradiction(
        self,
        premise: str,
        hypothesis: str,
    ) -> NLIResult:
        """Check if hypothesis contradicts the premise.

        Tries rule-based → local model → GPT-4o Mini in cascade.

        Args:
            premise: The known fact or statement.
            hypothesis: The statement to check against the premise.

        Returns:
            NLIResult with label and confidence.
        """
        # Layer 1: Rule-based (instant)
        rule_result = self._check_rules(premise, hypothesis)
        if rule_result:
            return rule_result

        # Layer 2: Local DeBERTa model (~50ms)
        if self._local_pipeline:
            local_result = self._check_local(premise, hypothesis)
            if local_result:
                return local_result

        # Layer 3: Azure GPT-4o Mini (~500ms) — only when the tenant
        # config is complete.  No public-OpenAI fallback exists.
        from brain_engine.models.azure_routing import (
            load_azure_openai_config,
        )

        if load_azure_openai_config().is_complete():
            return await self._check_gpt(premise, hypothesis)

        # Fallback: neutral (no confident assessment)
        return NLIResult(
            premise=premise,
            hypothesis=hypothesis,
            label=NLILabel.NEUTRAL,
            confidence=0.0,
            method="fallback",
        )

    async def check_batch(
        self,
        pairs: list[tuple[str, str]],
    ) -> list[NLIResult]:
        """Check multiple premise/hypothesis pairs.

        Args:
            pairs: List of (premise, hypothesis) tuples.

        Returns:
            List of NLIResult objects.
        """
        results: list[NLIResult] = []
        for premise, hypothesis in pairs:
            result = await self.check_contradiction(premise, hypothesis)
            results.append(result)
        return results

    def _check_rules(self, premise: str, hypothesis: str) -> NLIResult | None:
        """Quick rule-based contradiction check."""
        p_lower = premise.lower()
        h_lower = hypothesis.lower()

        for rule in RULE_PATTERNS:
            p_match = any(kw in p_lower for kw in rule["premise_contains"])
            h_match = any(kw in h_lower for kw in rule["hypothesis_contains"])

            if p_match and h_match:
                logger.info(
                    "Rule-based NLI: %s — %s",
                    rule["label"], rule["description"],
                )
                return NLIResult(
                    premise=premise,
                    hypothesis=hypothesis,
                    label=rule["label"],
                    confidence=0.85,
                    method="rules",
                )

        return None

    def _check_local(self, premise: str, hypothesis: str) -> NLIResult | None:
        """Check using local DeBERTa model."""
        if not self._local_pipeline:
            return None

        try:
            input_text = f"{premise} [SEP] {hypothesis}"
            result = self._local_pipeline(input_text)[0]

            label_str = result["label"].lower()
            if "contradiction" in label_str:
                label = NLILabel.CONTRADICTION
            elif "entailment" in label_str:
                label = NLILabel.ENTAILMENT
            else:
                label = NLILabel.NEUTRAL

            return NLIResult(
                premise=premise,
                hypothesis=hypothesis,
                label=label,
                confidence=result["score"],
                method="deberta",
            )
        except Exception:
            logger.exception("Local NLI model error")
            return None

    async def _check_gpt(self, premise: str, hypothesis: str) -> NLIResult:
        """Check using GPT-4o Mini as NLI classifier.

        Routes exclusively through the tenant's Azure OpenAI
        deployment.  Callers must guard with
        :meth:`AzureOpenAIConfig.is_complete` before invoking this.
        """
        try:
            from brain_engine.models.azure_routing import (
                build_async_azure_openai_client,
                load_azure_openai_config,
            )

            azure_cfg = load_azure_openai_config()
            client = build_async_azure_openai_client(azure_cfg)
            model_name = azure_cfg.chat_mini_deployment

            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an NLI classifier. Given a premise and hypothesis, "
                            "respond with exactly one word: ENTAILMENT, CONTRADICTION, or NEUTRAL.\n"
                            "ENTAILMENT = hypothesis follows from premise\n"
                            "CONTRADICTION = hypothesis conflicts with premise\n"
                            "NEUTRAL = no clear logical relationship"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Premise: {premise}\nHypothesis: {hypothesis}",
                    },
                ],
                max_tokens=10,
                temperature=0.0,
            )

            answer = response.choices[0].message.content.strip().upper()

            if "CONTRADICTION" in answer:
                label = NLILabel.CONTRADICTION
            elif "ENTAILMENT" in answer:
                label = NLILabel.ENTAILMENT
            else:
                label = NLILabel.NEUTRAL

            return NLIResult(
                premise=premise,
                hypothesis=hypothesis,
                label=label,
                confidence=0.94,  # Azure GPT-4o Mini NLI accuracy ~94%
                method="azure_gpt4o_mini",
            )
        except Exception:
            logger.exception("Azure GPT-4o Mini NLI check failed")
            return NLIResult(
                premise=premise,
                hypothesis=hypothesis,
                label=NLILabel.NEUTRAL,
                confidence=0.0,
                method="azure_gpt4o_mini_error",
            )
