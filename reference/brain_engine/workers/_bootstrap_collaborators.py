"""Optional pipeline collaborators rebuilt for the bootstrap worker.

The sandbox example-reply generator and the Foundation analysis
orchestrator are *optional* inputs to ``build_bootstrap_pipeline``:
the pipeline runs without them, just without generated replies or
scenario tagging.  The worker still builds both so a queue-driven
bootstrap is byte-for-byte equivalent to the in-process one (same
Foundation tagging the Foundation Layer PRs introduced).

Both builders mirror their FastAPI-lifespan counterparts and degrade
exactly as the server does — a misconfigured LLM backend falls back
to the deterministic template generator, and a missing Foundation
markdown yields ``None`` rather than crashing the worker.  They live
in their own module so :mod:`workers.bootstrap_deps` stays focused on
store wiring and well under the file-size budget.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import structlog

from brain_engine.sandbox import TemplateExampleReplyGenerator

if TYPE_CHECKING:
    from brain_engine.analysis.orchestrator import (
        FoundationAnalysisOrchestrator,
    )
    from brain_engine.profiles import PropertyProfileStore

__all__ = ["build_foundation_orchestrator", "build_sandbox_generator"]


logger = structlog.get_logger(__name__)


_LLM_BACKENDS = frozenset({"anthropic", "openai", "azure_openai"})


def build_sandbox_generator(profile_store: PropertyProfileStore) -> Any:
    """Build the sandbox reply generator (mirrors server.py:2303-2354).

    Returns an :class:`LLMExampleReplyGenerator` when
    ``SANDBOX_GENERATOR_BACKEND`` names an LLM provider, falling back
    to the deterministic template generator on any init failure —
    the same non-fatal degradation the server applies.
    """

    backend = os.getenv("SANDBOX_GENERATOR_BACKEND", "template").lower()
    if backend not in _LLM_BACKENDS:
        return TemplateExampleReplyGenerator()
    try:
        from brain_engine.models.factory import init_chat_model
        from brain_engine.sandbox import LLMExampleReplyGenerator

        default_model = {
            "anthropic": "claude-sonnet-4-6",
            "openai": "gpt-4o-mini",
            "azure_openai": os.getenv(
                "AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"
            ),
        }[backend]
        model_id = os.getenv("SANDBOX_GENERATOR_MODEL", default_model)
        extra: dict[str, Any] = {}
        if backend == "azure_openai":
            extra = {
                "azure_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", ""),
                "api_version": os.getenv("AZURE_OPENAI_API_VERSION", ""),
                "api_key": os.getenv("AZURE_OPENAI_API_KEY", "") or None,
            }
        chat_model = init_chat_model(f"{backend}:{model_id}", **extra)
        logger.info(
            "bootstrap_worker.sandbox_generator_wired",
            backend=backend,
            model=model_id,
        )
        return LLMExampleReplyGenerator(
            chat_model, profile_store=profile_store
        )
    except Exception as exc:  # optional: degrade to template
        logger.warning(
            "bootstrap_worker.sandbox_generator_fallback_template",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return TemplateExampleReplyGenerator()


async def build_foundation_orchestrator() -> (
    FoundationAnalysisOrchestrator | None
):
    """Build the Foundation orchestrator (mirrors server.py:2375-2406).

    Returns ``None`` when the Foundation markdown is missing or parses
    to zero scenarios — every consumer treats the slot as optional.
    """

    from brain_engine.analysis.orchestrator import (
        FoundationAnalysisOrchestrator,
    )
    from brain_engine.patterns.foundation_catalog_store import (
        InMemoryFoundationCatalogStore,
    )
    from brain_engine.patterns.foundation_registry import (
        compute_doc_hash,
        load_foundation_examples,
        load_foundation_scenarios,
    )
    from brain_engine.patterns.intelligent_classifier_factory import (
        DEFAULT_FOUNDATION_PATH,
    )
    from brain_engine.patterns.scenario_matcher import ScenarioMatcher

    try:
        examples = load_foundation_examples(DEFAULT_FOUNDATION_PATH)
        if not examples:
            logger.warning("bootstrap_worker.foundation_skipped_empty")
            return None
        catalog = InMemoryFoundationCatalogStore()
        scenarios = load_foundation_scenarios(DEFAULT_FOUNDATION_PATH)
        if scenarios:
            await catalog.upsert_many(
                scenarios,
                doc_hash=compute_doc_hash(DEFAULT_FOUNDATION_PATH) or "",
            )
        logger.info(
            "bootstrap_worker.foundation_built",
            scenarios=len(examples),
            catalog_rows=len(scenarios),
        )
        return FoundationAnalysisOrchestrator(
            scenario_matcher=ScenarioMatcher(examples),
            foundation_catalog=catalog,
        )
    except Exception:
        logger.exception("bootstrap_worker.foundation_build_failed")
        return None
