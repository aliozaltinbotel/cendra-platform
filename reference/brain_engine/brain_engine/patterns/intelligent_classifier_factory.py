"""Factory for the production :class:`IntelligentClassifier`.

Wires the three layers shipped by Phase 1 — 4 into a single
runtime instance:

* :class:`LanguageDetectorService` (Layer 1, lingua, offline).
* :class:`ScenarioMatcher` (Layer 2, fastembed) populated from
  the foundation document
  ``Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_
  Foundation.md`` parsed by
  :func:`brain_engine.patterns.foundation_registry.
  load_foundation_examples`.
* :class:`LiteLLMClassifierClient` (Layer 3, gpt-4o-mini JSON-
  mode pick).

The factory is *idempotent*: a single ``build_intelligent_classifier``
call constructs everything, the resulting :class:`IntelligentClassifier`
is shareable across coroutines (every method is async + stateless
across calls).  The :class:`ScenarioMatcher` lazy-loads its
embedding model on the first ``top_k`` call, so the factory call
itself stays cheap.

Returns ``None`` when the foundation document is missing or yields
zero scenarios — callers must treat the wired classifier as
optional and degrade to the existing flag + hint pathways.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import structlog

from brain_engine.patterns.foundation_registry import (
    load_foundation_examples,
)
from brain_engine.patterns.intelligent_classifier import (
    IntelligentClassifier,
)
from brain_engine.patterns.language_detector import (
    get_shared_language_detector,
)
from brain_engine.patterns.litellm_classifier_client import (
    DEFAULT_LLM_MODEL,
    LiteLLMClassifierClient,
)
from brain_engine.patterns.scenario_matcher import (
    DEFAULT_TOP_K,
    ScenarioMatcher,
)

__all__ = [
    "DEFAULT_FOUNDATION_PATH",
    "build_intelligent_classifier",
]


_REPO_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[2]
)
DEFAULT_FOUNDATION_PATH: Final[Path] = (
    _REPO_ROOT
    / "Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md"
)


logger = structlog.get_logger(__name__)


def build_intelligent_classifier(
    *,
    foundation_path: Path | str = DEFAULT_FOUNDATION_PATH,
    llm_model: str = DEFAULT_LLM_MODEL,
    top_k: int = DEFAULT_TOP_K,
) -> IntelligentClassifier | None:
    """Construct a production :class:`IntelligentClassifier`.

    Returns ``None`` when the foundation document is missing or
    parses to zero scenarios — the caller (typically
    ``api_server/server.py``) must treat the result as optional
    and only thread it into downstream consumers when present.

    Args:
        foundation_path: Path to the foundation markdown.  Falls
            back to :data:`DEFAULT_FOUNDATION_PATH` when not
            provided.
        llm_model: Model name passed to litellm.  ``gpt-4o-mini``
            balances quality and cost for JSON-mode classification.
        top_k: Number of candidate scenarios to narrow to before
            the LLM pick.
    """
    examples = load_foundation_examples(foundation_path)
    if not examples:
        logger.warning(
            "intelligent_classifier.factory.empty_registry",
            foundation_path=str(foundation_path),
        )
        return None
    matcher = ScenarioMatcher(examples)
    detector = get_shared_language_detector()
    llm_client = LiteLLMClassifierClient(model=llm_model)
    logger.info(
        "intelligent_classifier.factory.built",
        scenarios=len(examples),
        llm_model=llm_model,
        top_k=top_k,
    )
    return IntelligentClassifier(
        detector=detector,
        matcher=matcher,
        llm_client=llm_client,
        top_k=top_k,
    )
