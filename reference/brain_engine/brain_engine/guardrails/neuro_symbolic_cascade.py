"""Neuro-Symbolic 4-Layer Cascade — Full contradiction/commonsense validation.

Implements the cascade described in Brain_Engine_Research_2026:
  Layer 1: Keyword Rules (instant, free) — symbolic_rules + contradiction_checker
  Layer 2: ConceptNet API (100-200ms) — commonsense semantic relationships
  Layer 3: NLI DeBERTa-v3 / Azure GPT-4o Mini (50-500ms) — logical contradiction
  Layer 4: Azure GPT-4o (500ms+) — final arbiter for ambiguous cases

Layer 3 and Layer 4 LLM calls route exclusively through the tenant's
Azure OpenAI deployment via ``brain_engine/models/azure_routing.py``;
the public ``api.openai.com`` surface is never used.

The cascade runs cheapest-first and short-circuits on confident detection.
Overall accuracy: ~94%+ at <700ms latency for most cases.

Usage:
    cascade = NeuroSymbolicCascade()
    result = await cascade.validate(
        premise="The guest is celebrating a birthday",
        hypothesis="Delivery address is a funeral home",
        context={"guest_name": "John", "occasion": "birthday"}
    )
    if result.is_contradiction:
        # Block or flag the action
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from brain_engine.guardrails.conceptnet import ConceptNetClient, CommonsenseResult
from brain_engine.guardrails.nli_checker import NLIChecker, NLILabel, NLIResult
from brain_engine.guardrails.contradiction_checker import ContradictionChecker

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CascadeResult:
    """Result of the neuro-symbolic cascade validation.

    Attributes:
        is_contradiction: Whether a contradiction was detected.
        confidence: Overall confidence (0-1).
        detected_at_layer: Which layer caught the issue (1-4, 0 = none).
        layer_results: Results from each layer that was executed.
        explanation: Human-readable explanation of the finding.
        total_latency_ms: Estimated total latency in milliseconds.
    """

    is_contradiction: bool = False
    confidence: float = 0.0
    detected_at_layer: int = 0
    layer_results: list[dict[str, Any]] = field(default_factory=list)
    explanation: str = ""
    total_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses."""
        return {
            "is_contradiction": self.is_contradiction,
            "confidence": self.confidence,
            "detected_at_layer": self.detected_at_layer,
            "layer_results": self.layer_results,
            "explanation": self.explanation,
            "total_latency_ms": self.total_latency_ms,
        }


class NeuroSymbolicCascade:
    """4-layer neuro-symbolic contradiction detection cascade.

    Runs layers in order of cost (cheapest first) and short-circuits
    when a confident detection is made.

    Args:
        conceptnet_client: Optional pre-configured ConceptNet client.
        nli_checker: Optional pre-configured NLI checker.
        contradiction_checker: Optional pre-configured contradiction checker.
        confidence_threshold: Min confidence to short-circuit at any layer.
        use_conceptnet_api: Whether to make real ConceptNet API calls.
        use_local_nli: Whether to use local DeBERTa model.
    """

    def __init__(
        self,
        conceptnet_client: ConceptNetClient | None = None,
        nli_checker: NLIChecker | None = None,
        contradiction_checker: ContradictionChecker | None = None,
        confidence_threshold: float = 0.8,
        use_conceptnet_api: bool = True,
        use_local_nli: bool = False,
    ) -> None:
        self._conceptnet = conceptnet_client or ConceptNetClient(
            use_api=use_conceptnet_api,
        )
        self._nli = nli_checker or NLIChecker(
            use_local_model=use_local_nli,
        )
        self._contradiction = contradiction_checker or ContradictionChecker()
        self._confidence_threshold = confidence_threshold

    async def validate(
        self,
        premise: str,
        hypothesis: str,
        context: dict[str, Any] | None = None,
        slots: dict[str, Any] | None = None,
    ) -> CascadeResult:
        """Run the full 4-layer cascade validation.

        Args:
            premise: The known fact or statement.
            hypothesis: The claim to check against the premise.
            context: Additional context for commonsense checks.
            slots: Current slot values for rule-based checks.

        Returns:
            CascadeResult with detection details.
        """
        result = CascadeResult()

        # ── Layer 1: Keyword Rules + Symbolic Checks (instant) ───────────
        layer1 = self._run_layer1(premise, hypothesis, slots or {})
        result.layer_results.append(layer1)
        result.total_latency_ms += layer1.get("latency_ms", 0)

        if layer1.get("detected") and layer1.get("confidence", 0) >= self._confidence_threshold:
            result.is_contradiction = True
            result.confidence = layer1["confidence"]
            result.detected_at_layer = 1
            result.explanation = layer1.get("explanation", "Rule-based detection")
            logger.info("Cascade: contradiction at Layer 1 (rules) — %s", result.explanation)
            return result

        # ── Layer 2: ConceptNet Commonsense (100-200ms) ──────────────────
        layer2 = await self._run_layer2(premise, hypothesis)
        result.layer_results.append(layer2)
        result.total_latency_ms += layer2.get("latency_ms", 0)

        if layer2.get("detected") and layer2.get("confidence", 0) >= self._confidence_threshold:
            result.is_contradiction = True
            result.confidence = layer2["confidence"]
            result.detected_at_layer = 2
            result.explanation = layer2.get("explanation", "ConceptNet commonsense conflict")
            logger.info("Cascade: contradiction at Layer 2 (ConceptNet) — %s", result.explanation)
            return result

        # ── Layer 3: NLI DeBERTa / GPT-4o Mini (50-500ms) ───────────────
        layer3 = await self._run_layer3(premise, hypothesis)
        result.layer_results.append(layer3)
        result.total_latency_ms += layer3.get("latency_ms", 0)

        if layer3.get("detected") and layer3.get("confidence", 0) >= self._confidence_threshold:
            result.is_contradiction = True
            result.confidence = layer3["confidence"]
            result.detected_at_layer = 3
            result.explanation = layer3.get("explanation", "NLI contradiction detected")
            logger.info("Cascade: contradiction at Layer 3 (NLI) — %s", result.explanation)
            return result

        # ── Layer 4: Azure GPT-4o Final Arbiter (500ms+) ────────────────
        # Only invoke if earlier layers had weak signals AND the tenant
        # Azure OpenAI config is complete — no public-OpenAI fallback.
        from brain_engine.models.azure_routing import (
            load_azure_openai_config,
        )

        has_weak_signal = any(
            lr.get("confidence", 0) > 0.3
            for lr in result.layer_results
            if lr.get("detected")
        )
        azure_complete = load_azure_openai_config().is_complete()
        if has_weak_signal and azure_complete:
            layer4 = await self._run_layer4(premise, hypothesis, context or {})
            result.layer_results.append(layer4)
            result.total_latency_ms += layer4.get("latency_ms", 0)

            if layer4.get("detected"):
                result.is_contradiction = True
                result.confidence = layer4["confidence"]
                result.detected_at_layer = 4
                result.explanation = layer4.get(
                    "explanation", "Azure GPT-4o final assessment",
                )
                logger.info(
                    "Cascade: contradiction at Layer 4 (Azure GPT-4o) — %s",
                    result.explanation,
                )
                return result

        # No contradiction detected
        result.explanation = "No contradiction detected across all layers"
        logger.debug("Cascade: no contradiction found")
        return result

    def _run_layer1(
        self,
        premise: str,
        hypothesis: str,
        slots: dict[str, Any],
    ) -> dict[str, Any]:
        """Layer 1: Rule-based keyword matching and slot contradictions."""
        layer: dict[str, Any] = {"layer": 1, "name": "keyword_rules", "latency_ms": 1}

        # Check slot-level contradictions
        if slots:
            contradictions = self._contradiction.check_slots(slots)
            if contradictions:
                high_severity = [c for c in contradictions if c.severity == "HIGH"]
                if high_severity:
                    layer["detected"] = True
                    layer["confidence"] = 0.9
                    layer["explanation"] = high_severity[0].message
                    return layer

        # Simple keyword opposition check
        opposition_pairs = [
            ({"yes", "confirmed", "approved", "available"}, {"no", "denied", "rejected", "unavailable"}),
            ({"clean", "undamaged", "perfect"}, {"damaged", "broken", "dirty", "stained"}),
            ({"checked in", "arrived"}, {"checked out", "departed", "left"}),
        ]

        p_words = set(premise.lower().split())
        h_words = set(hypothesis.lower().split())

        for set_a, set_b in opposition_pairs:
            if (p_words & set_a and h_words & set_b) or (p_words & set_b and h_words & set_a):
                layer["detected"] = True
                layer["confidence"] = 0.85
                layer["explanation"] = f"Keyword opposition: {p_words & (set_a | set_b)} vs {h_words & (set_a | set_b)}"
                return layer

        layer["detected"] = False
        layer["confidence"] = 0.0
        return layer

    async def _run_layer2(
        self,
        premise: str,
        hypothesis: str,
    ) -> dict[str, Any]:
        """Layer 2: ConceptNet commonsense check."""
        layer: dict[str, Any] = {"layer": 2, "name": "conceptnet", "latency_ms": 150}

        try:
            result: CommonsenseResult = await self._conceptnet.check_commonsense(
                concept_a=premise,
                concept_b=hypothesis,
            )

            layer["detected"] = result.is_conflict
            layer["confidence"] = result.confidence
            layer["explanation"] = result.explanation
        except Exception:
            logger.exception("Layer 2 (ConceptNet) error")
            layer["detected"] = False
            layer["confidence"] = 0.0
            layer["explanation"] = "ConceptNet check failed"

        return layer

    async def _run_layer3(
        self,
        premise: str,
        hypothesis: str,
    ) -> dict[str, Any]:
        """Layer 3: NLI DeBERTa / GPT-4o Mini."""
        layer: dict[str, Any] = {"layer": 3, "name": "nli", "latency_ms": 300}

        try:
            result: NLIResult = await self._nli.check_contradiction(premise, hypothesis)

            layer["detected"] = result.label == NLILabel.CONTRADICTION
            layer["confidence"] = result.confidence
            layer["method"] = result.method
            layer["explanation"] = f"NLI ({result.method}): {result.label.value} ({result.confidence:.2f})"
        except Exception:
            logger.exception("Layer 3 (NLI) error")
            layer["detected"] = False
            layer["confidence"] = 0.0
            layer["explanation"] = "NLI check failed"

        return layer

    async def _run_layer4(
        self,
        premise: str,
        hypothesis: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Layer 4: Azure GPT-4o final arbiter."""
        layer: dict[str, Any] = {
            "layer": 4, "name": "azure_gpt4o_arbiter", "latency_ms": 600,
        }

        try:
            from brain_engine.models.azure_routing import (
                build_async_azure_openai_client,
                load_azure_openai_config,
            )

            azure_cfg = load_azure_openai_config()
            client = build_async_azure_openai_client(azure_cfg)
            model_name = azure_cfg.chat_deployment

            ctx_str = "\n".join(f"- {k}: {v}" for k, v in context.items()) if context else "None"

            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a contradiction detection expert for a property management AI system. "
                            "Analyze whether the hypothesis contradicts the premise, considering commonsense "
                            "knowledge and the context of short-term rental management.\n\n"
                            "Respond in JSON format:\n"
                            '{"is_contradiction": true/false, "confidence": 0.0-1.0, "explanation": "..."}'
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Premise: {premise}\n"
                            f"Hypothesis: {hypothesis}\n"
                            f"Context:\n{ctx_str}"
                        ),
                    },
                ],
                max_tokens=200,
                temperature=0.0,
                response_format={"type": "json_object"},
            )

            import json
            answer = json.loads(response.choices[0].message.content)

            layer["detected"] = answer.get("is_contradiction", False)
            layer["confidence"] = answer.get("confidence", 0.0)
            layer["explanation"] = answer.get("explanation", "")
        except Exception:
            logger.exception("Layer 4 (Azure GPT-4o) error")
            layer["detected"] = False
            layer["confidence"] = 0.0
            layer["explanation"] = "Azure GPT-4o arbiter failed"

        return layer
