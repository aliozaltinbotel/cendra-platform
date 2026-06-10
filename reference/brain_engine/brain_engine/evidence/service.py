"""Evidence composer — fan out across sources, merge into a bundle.

:class:`EvidenceService` is the entry point the HTTP layer calls.  It
runs all injected sources concurrently with
``asyncio.gather(..., return_exceptions=True)``, converts failures
into :class:`EvidenceSourceError` entries inside the returned bundle,
and applies the :attr:`EvidenceQuery.limit` cap per category.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from brain_engine.evidence.errors import (
    EvidenceCompositionError,
    EvidenceSourceError,
)
from brain_engine.evidence.models import (
    BlockerPick,
    CasePick,
    EvidenceBundle,
    EvidenceQuery,
    PromptPick,
    RulePick,
)
from brain_engine.evidence.sources import (
    BlockerEvidenceSource,
    CaseEvidenceSource,
    PromptEvidenceSource,
    RuleEvidenceSource,
)

logger = structlog.get_logger(__name__)


class EvidenceService:
    """Orchestrates evidence fan-out into an :class:`EvidenceBundle`."""

    def __init__(
        self,
        *,
        rule_source: RuleEvidenceSource | None = None,
        case_source: CaseEvidenceSource | None = None,
        prompt_source: PromptEvidenceSource | None = None,
        blocker_source: BlockerEvidenceSource | None = None,
    ) -> None:
        self._rules = rule_source
        self._cases = case_source
        self._prompts = prompt_source
        self._blockers = blocker_source
        self._log = logger.bind(component="evidence_service")

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def compose(self, query: EvidenceQuery) -> EvidenceBundle:
        """Return a fully-merged :class:`EvidenceBundle` for ``query``."""
        if query.limit <= 0:
            raise EvidenceCompositionError(
                "EvidenceQuery.limit must be positive",
            )
        results = await asyncio.gather(
            self._call(self._rules, "rules", query),
            self._call(self._cases, "cases", query),
            self._call(self._prompts, "prompts", query),
            self._call(self._blockers, "blockers", query),
            return_exceptions=True,
        )
        rules_out, cases_out, prompts_out, blockers_out = (
            self._unpack(results[0], RulePick),
            self._unpack(results[1], CasePick),
            self._unpack(results[2], PromptPick),
            self._unpack(results[3], BlockerPick),
        )
        errors = tuple(self._collect_errors(results))
        return EvidenceBundle(
            query=query,
            rules=self._cap(rules_out, query.limit),
            cases=self._cap(cases_out, query.limit),
            prompts=self._cap(prompts_out, query.limit),
            blockers=self._cap(blockers_out, query.limit),
            errors=errors,
            meta={"source_count": self._active_source_count()},
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _call(
        self,
        source: Any,
        label: str,
        query: EvidenceQuery,
    ) -> tuple[Any, ...]:
        """Dispatch to one source; wrap upstream failures."""
        if source is None:
            return ()
        try:
            if label == "rules":
                return await source.fetch_rules(query)
            if label == "cases":
                return await source.fetch_cases(query)
            if label == "prompts":
                return await source.fetch_prompts(query)
            return await source.fetch_blockers(query)
        except EvidenceSourceError:
            raise
        except Exception as exc:
            self._log.warning(
                "evidence.source_failed",
                source=label,
                error=str(exc),
            )
            raise EvidenceSourceError(label, str(exc)) from exc

    @staticmethod
    def _unpack(
        result: object,
        expected: type,
    ) -> tuple[Any, ...]:
        """Drop failures and enforce expected pick type."""
        if isinstance(result, BaseException):
            return ()
        if not isinstance(result, tuple):
            return tuple(result)  # type: ignore[arg-type]
        return tuple(item for item in result if isinstance(item, expected))

    @staticmethod
    def _cap(items: tuple[Any, ...], limit: int) -> tuple[Any, ...]:
        return items[:limit]

    @staticmethod
    def _collect_errors(results: list[Any]) -> list[str]:
        errors: list[str] = []
        for result in results:
            if isinstance(result, EvidenceSourceError):
                errors.append(str(result))
            elif isinstance(result, BaseException):
                errors.append(f"unknown: {result}")
        return errors

    def _active_source_count(self) -> int:
        return sum(
            1 for s in (
                self._rules, self._cases,
                self._prompts, self._blockers,
            )
            if s is not None
        )
