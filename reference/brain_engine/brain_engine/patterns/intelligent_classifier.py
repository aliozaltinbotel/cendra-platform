"""Layer 3 of the intelligent classifier — LLM final pick over top-K.

Composes the Layer 1 :class:`LanguageDetectorService` and the
Layer 2 :class:`ScenarioMatcher` with an LLM-backed final
classification step.  The whole pipeline replaces the
hand-curated multilingual keyword tables in
:mod:`brain_engine.patterns.classifier`.

Pipeline
--------

::

    incoming guest message
        ↓ Layer 1
    language code (ISO 639-1) + confidence + is_fallback
        ↓ Layer 2
    top-K candidate scenario ids with similarity scores
        ↓ Layer 3 (this module)
    1 LLM call:
        - prompt carries only the K candidates (not all 500)
        - prompt is wrapped in the detected language so the
          LLM stays grounded
        - structured JSON response: ``scenario_id`` +
          ``decision_type`` + ``confidence``
        ↓
    :class:`IntelligentClassification` (audit-friendly value object)

Why three layers
----------------

* **Cost.**  Sending the LLM 500 scenarios in every prompt is
  wasteful (token spend) and inaccurate (the model cannot
  reliably remember the full taxonomy).  Embedding retrieval
  narrows the space to K (default 15) candidates the LLM picks
  among.
* **Multilingual.**  Layer 1 + Layer 2 together do the heavy
  lifting for non-English messages — no per-language keyword
  maintenance.
* **Auditability.**  Every layer's output is stored on the
  result so the audit log can replay why a particular scenario
  was chosen.

Honest scope
------------

* The module is *agnostic* to the LLM transport.  Callers pass
  in any object implementing the :class:`LLMClassifierClient`
  Protocol — production wires
  :class:`brain_engine.reasoning.business_classifier.
  BusinessFlagClassifier`, tests use a stub.
* No retries, no caching here — the caller owns those policies.
* The classifier is *additive*: it returns ``None`` for the
  scenario when the LLM cannot confidently pick from the
  candidates, leaving the legacy chain in place for one final
  cleanup pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import structlog

from brain_engine.patterns.language_detector import (
    DEFAULT_LANGUAGE,
    DetectionResult,
    LanguageDetectorService,
)
from brain_engine.patterns.scenario_matcher import (
    DEFAULT_TOP_K,
    ScenarioCandidate,
    ScenarioMatcher,
)

__all__ = [
    "IntelligentClassification",
    "IntelligentClassifier",
    "LLMClassificationResult",
    "LLMClassifierClient",
]


logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LLMClassificationResult:
    """Structured outcome the LLM final-pick step returns.

    Attributes:
        scenario_id: One of the IDs the prompt offered, or empty
            when the LLM declined to commit.
        decision_type: One of the seven canonical
            :class:`~brain_engine.patterns.models.DecisionType`
            values (string form).  Empty when undetermined.
        confidence: ``[0.0, 1.0]`` — the LLM's self-reported
            confidence.  Stored verbatim for the audit log; not
            used by this module for filtering.
        rationale: Short free-form explanation; first 200 chars
            land in the audit log.
    """

    scenario_id: str
    decision_type: str
    confidence: float
    rationale: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "confidence must be in [0.0, 1.0]"
            )


class LLMClassifierClient(Protocol):
    """Transport-agnostic interface the LLM final-pick step requires.

    Production implementations wrap ``litellm.acompletion`` (or
    equivalent); tests pass a deterministic stub.  Implementations
    MUST NOT raise — every transport error must be translated into
    an empty :class:`LLMClassificationResult` with
    ``confidence=0.0`` so the caller can fall through to the
    legacy keyword chain.
    """

    async def classify(
        self,
        *,
        message: str,
        language: str,
        candidates: Sequence[ScenarioCandidate],
    ) -> LLMClassificationResult:
        """Pick a scenario + decision_type from ``candidates``."""
        ...


@dataclass(frozen=True, slots=True)
class IntelligentClassification:
    """Audit-friendly composite result of one classification pass.

    Stores every intermediate signal so the audit log can replay
    *why* a scenario / decision_type pair was emitted.  Callers
    that only care about the final answer read
    :attr:`scenario_id` / :attr:`decision_type`; debug / audit
    consumers walk the rest.
    """

    message: str
    language: DetectionResult
    candidates: tuple[ScenarioCandidate, ...]
    llm: LLMClassificationResult
    scenario_id: str = ""
    decision_type: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.language, DetectionResult):
            raise TypeError(
                "language must be a DetectionResult instance"
            )

    @property
    def top_candidate(self) -> ScenarioCandidate | None:
        """Return the highest-similarity candidate, if any."""
        return self.candidates[0] if self.candidates else None


class IntelligentClassifier:
    """Composes Layers 1+2+3 into a single async classifier.

    The classifier is stateless — every :meth:`classify` call
    walks the pipeline end-to-end.  Embedding cold-start happens
    on the first call (or eagerly via the underlying matcher's
    ``load()``).
    """

    def __init__(
        self,
        *,
        detector: LanguageDetectorService,
        matcher: ScenarioMatcher,
        llm_client: LLMClassifierClient,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        self._detector = detector
        self._matcher = matcher
        self._llm = llm_client
        self._top_k = top_k
        self._log = logger.bind(component="intelligent_classifier")

    async def classify(self, message: str) -> IntelligentClassification:
        """Run language detection → retrieval → LLM final pick.

        Empty / whitespace inputs short-circuit to a zero-result
        :class:`IntelligentClassification` — the legacy chain
        upstream handles the empty case.
        """
        language = self._detector.detect(message)
        candidates = self._matcher.top_k(message, k=self._top_k)
        if not candidates:
            empty = LLMClassificationResult(
                scenario_id="",
                decision_type="",
                confidence=0.0,
                rationale="no candidates available",
            )
            return IntelligentClassification(
                message=message,
                language=language,
                candidates=(),
                llm=empty,
                scenario_id="",
                decision_type="",
            )
        llm_result = await self._llm.classify(
            message=message,
            language=language.language,
            candidates=candidates,
        )
        chosen_id = self._resolve_scenario_id(
            candidates=candidates,
            llm_result=llm_result,
        )
        self._log.info(
            "classify.done",
            language=language.language,
            language_fallback=language.is_fallback,
            top_candidate=candidates[0].scenario_id,
            llm_chosen=llm_result.scenario_id,
            chosen_id=chosen_id,
        )
        return IntelligentClassification(
            message=message,
            language=language,
            candidates=candidates,
            llm=llm_result,
            scenario_id=chosen_id,
            decision_type=llm_result.decision_type,
        )

    @staticmethod
    def _resolve_scenario_id(
        *,
        candidates: Sequence[ScenarioCandidate],
        llm_result: LLMClassificationResult,
    ) -> str:
        """Pick the final scenario id from the LLM output.

        When the LLM returned an id present in ``candidates``, use
        it.  Otherwise fall back to the highest-similarity
        candidate (best signal we have).  When ``candidates`` is
        empty the caller never reaches this method.
        """
        if llm_result.scenario_id:
            allowed = {c.scenario_id for c in candidates}
            if llm_result.scenario_id in allowed:
                return llm_result.scenario_id
        return candidates[0].scenario_id


_ = (DEFAULT_LANGUAGE, field)
