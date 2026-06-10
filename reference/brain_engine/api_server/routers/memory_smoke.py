"""HTTP surface for the real-data memory + patterns smoke harness.

The endpoint stitches the lifespan-wired GraphQL loader, episode
builder, case extractor, case store, episodic memory, pattern miner
and rule store into a :class:`MemorySmokeRunner` and runs it for one
property.  The response is a JSON-serialised
:class:`MemorySmokeReport` carrying a per-stage verdict so the
caller (operator, k8s Job, dashboard probe) can see at a glance
which subsystem broke.

The router uses lazy dependency injection — concrete services are
provided through :func:`configure_deps` during application
``lifespan``.  When a dependency is missing the route returns
``503`` instead of raising ``AttributeError`` so a half-configured
pod still answers cleanly.

The endpoint deliberately requires the operator to pass the
property identifier in the path — there is no batch / wildcard form.
The smoke harness writes into shared memory and pattern stores; a
batch run would multiply storage pressure without adding signal.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Path, Query
from fastapi.responses import JSONResponse

from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.memory.smoke import (
    DEFAULT_SMOKE_DAYS,
    DEFAULT_SMOKE_LIMIT,
    MemorySmokeRunner,
)
from brain_engine.onboarding.episode_builder import EpisodeBuilder
from brain_engine.onboarding.graphql_archive_loader import (
    GraphQLConversationArchiveLoader,
)
from brain_engine.onboarding.historical_case_extractor import (
    HistoricalCaseExtractor,
)
from brain_engine.patterns.case_builder import CaseBuilder
from brain_engine.patterns.classifier import DecisionClassifier
from brain_engine.patterns.feature_builder import FeatureBuilder
from brain_engine.patterns.pattern_miner import PatternMiner
from brain_engine.patterns.router import PatternRuleRouter
from brain_engine.patterns.store import (
    DecisionCaseStore,
    PatternRuleStore,
)


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/admin/memory", tags=["Intelligence"])


_deps: dict[str, Any] = {
    "archive_loader": None,
    "case_store": None,
    "rule_store": None,
    "rule_router": None,
    "episodic_memory": None,
}


def configure_deps(deps: dict[str, Any]) -> None:
    """Publish the lifespan-built dependencies into the router scope.

    The lifespan calls this once after the GraphQL loader, case
    store, rule store + router and the memory tier are all live.  A
    second call replaces the prior values atomically — useful in
    tests where a fixture installs a fresh stack between
    invocations.

    Keys are validated at the route level rather than here so the
    router can keep working even when only a subset of dependencies
    is configured (the route will return 503 with a precise reason).
    """
    _deps.update(deps)


@router.post(
    "/smoke/{property_id}",
    summary="Run the memory + patterns smoke harness on real data",
)
async def run_memory_smoke(
    property_id: str = Path(..., min_length=1),
    days: int = Query(
        DEFAULT_SMOKE_DAYS,
        ge=1,
        le=730,
        description=(
            "Look-back window for the GraphQL archive loader. "
            "Defaults to the cold-start fast value."
        ),
    ),
    limit: int = Query(
        DEFAULT_SMOKE_LIMIT,
        ge=1,
        le=500,
        description=(
            "Hard cap on conversations consumed by the smoke run."
        ),
    ),
) -> JSONResponse:
    """Run the smoke harness for ``property_id`` and return the report.

    Returns:
        ``200`` with the smoke report JSON when every stage that ran
        either passed or was legitimately skipped.

        ``503`` with an ``error`` field when a required dependency
        was not configured at lifespan.

        ``500`` with the exception summary when the harness itself
        raised an unexpected error — by contract individual stages
        contain their own failures, so this is reserved for setup
        bugs.
    """
    archive_loader = _deps.get("archive_loader")
    case_store = _deps.get("case_store")
    rule_store = _deps.get("rule_store")
    rule_router = _deps.get("rule_router")
    episodic_memory = _deps.get("episodic_memory")

    missing = _missing_deps(
        archive_loader=archive_loader,
        case_store=case_store,
        rule_store=rule_store,
        episodic_memory=episodic_memory,
    )
    if missing:
        return JSONResponse(
            status_code=503,
            content={
                "error": "memory smoke not ready",
                "missing": missing,
            },
        )

    if not isinstance(archive_loader, GraphQLConversationArchiveLoader):
        return JSONResponse(
            status_code=503,
            content={
                "error": (
                    "smoke requires GraphQLConversationArchiveLoader "
                    "but a different archive loader is wired"
                ),
            },
        )
    if not isinstance(case_store, DecisionCaseStore):
        return JSONResponse(
            status_code=503,
            content={"error": "case_store does not satisfy protocol"},
        )
    if not isinstance(rule_store, PatternRuleStore):
        return JSONResponse(
            status_code=503,
            content={"error": "rule_store does not satisfy protocol"},
        )
    if not isinstance(episodic_memory, EpisodicMemory):
        return JSONResponse(
            status_code=503,
            content={
                "error": "episodic_memory is not an EpisodicMemory",
            },
        )

    runner = MemorySmokeRunner(
        archive_loader=archive_loader,
        episode_builder=EpisodeBuilder(),
        case_extractor=HistoricalCaseExtractor(
            case_builder=CaseBuilder(FeatureBuilder()),
            classifier=DecisionClassifier(),
        ),
        case_store=case_store,
        episodic_memory=episodic_memory,
        pattern_miner=PatternMiner(),
        rule_store=rule_store,
        rule_router=(
            rule_router
            if isinstance(rule_router, PatternRuleRouter)
            else None
        ),
    )

    try:
        report = await runner.run(
            property_id=property_id,
            days=days,
            limit=limit,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001 - top-level smoke bus
        logger.exception(
            "memory_smoke.unhandled_failure property_id=%s",
            property_id,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": (
                    f"{exc.__class__.__name__}: {exc}"
                    or exc.__class__.__name__
                ),
            },
        )

    return JSONResponse(content=report.as_dict())


def _missing_deps(
    *,
    archive_loader: Any,
    case_store: Any,
    rule_store: Any,
    episodic_memory: Any,
) -> list[str]:
    """Return the names of dependencies that are still ``None``."""
    pairs = (
        ("archive_loader", archive_loader),
        ("case_store", case_store),
        ("rule_store", rule_store),
        ("episodic_memory", episodic_memory),
    )
    return [name for name, value in pairs if value is None]
