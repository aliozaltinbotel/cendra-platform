"""Guardrail Pipeline — three-tier validation cascade with early exit.

Tiers ordered cheapest-first:

  Tier 1 (<10ms)    — FormatCheck + LexicalCheck (regex, deterministic)
  Tier 2 (20-100ms) — RepeatCheck + RepeatQuestionCheck + ContradictionChecker
  Tier 3 (500ms+)   — HallucinationCheck + NLI/LLM Judge

A blocking finding (HIGH/CRITICAL) on any tier short-circuits the
remaining tiers — saves 500ms+ on every such request.

Deep contradiction analysis lives in :class:`NeuroSymbolicCascade`:
  Layer 1: Keyword Rules → Layer 2: ConceptNet → Layer 3: NLI → Layer 4: GPT-4o

All LLM-backed checks route through Azure OpenAI via
``brain_engine/models/azure_routing.py`` — no public ``api.openai.com``
calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from brain_engine.guardrails.contradiction_checker import ContradictionChecker
from brain_engine.guardrails.format_check import FormatCheck
from brain_engine.guardrails.hallucination_check import HallucinationCheck
from brain_engine.guardrails.lexical_check import LexicalCheck
from brain_engine.guardrails.models import (
    CheckResult,
    GuardrailTier,
    Severity,
    TierResult,
    TierTimer,
)
from brain_engine.guardrails.repeat_check import RepeatCheck
from brain_engine.guardrails.repeat_question_check import RepeatQuestionCheck
from brain_engine.guardrails.symbolic_rules import SymbolicRulesEngine
from brain_engine.streaming.emit_helpers import emit_guardrail_check

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Результат выполнения полного guardrail-конвейера."""

    passed: bool
    failures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    cleaned_response: str = ""
    tier_results: list[TierResult] = field(default_factory=list)

    @property
    def correction_prompt(self) -> str:
        """Собрать промпт для исправления из всех failures."""
        if not self.failures:
            return ""
        lines = ["Fix the following issues:"]
        for f in self.failures:
            lines.append(f"- {f.get('check', 'unknown')}: {f.get('message', '')}")
        return "\n".join(lines)

    @property
    def total_duration_ms(self) -> float:
        """Суммарное время всех выполненных уровней."""
        return sum(tr.duration_ms for tr in self.tier_results)

    @property
    def tiers_executed(self) -> int:
        """Количество выполненных уровней (1-3)."""
        return len(self.tier_results)


class GuardrailPipeline:
    """Трёхуровневый конвейер валидации с early exit.

    Интегрирует все guardrail-компоненты по стоимости (дешёвые первыми):
      Tier 1: FormatCheck + LexicalCheck (regex)
      Tier 2: RepeatCheck + RepeatQuestionCheck + Contradiction
      Tier 3: HallucinationCheck (LLM)

    При блокирующей ошибке на любом уровне — ранний выход.

    Args:
        max_retries: Number of regeneration attempts before rejecting.
        audience: Target audience for lexical checks (guest/cleaner/owner).
    """

    def __init__(
        self,
        max_retries: int = 2,
        audience: str = "guest",
    ) -> None:
        self.max_retries = max_retries

        # Tier 1 — regex, детерминистические (<10ms)
        self.format_check = FormatCheck()
        self.lexical = LexicalCheck(audience=audience)

        # Tier 2 — семантические (20-100ms)
        self.repeat_check = RepeatCheck()
        self.repeat_question = RepeatQuestionCheck()
        self.contradiction = ContradictionChecker()
        self.symbolic = SymbolicRulesEngine()

        # Tier 3 — LLM-тяжёлые (500ms+)
        self.hallucination = HallucinationCheck(strict=False)

    # ── Публичный API ────────────────────────────────────────── #

    def validate_action(self, action: str, context: dict[str, Any]) -> PipelineResult:
        """Валидация предложенного действия перед выполнением.

        Запускает только синхронные проверки (без LLM).

        Args:
            action: Имя действия (e.g. "call_guest", "submit_claim").
            context: Текущие значения слотов и состояние.

        Returns:
            PipelineResult с результатом валидации.
        """
        result = PipelineResult(passed=True)

        # Символические правила
        allowed, reason = self.symbolic.is_allowed(action, context)
        if not allowed:
            result.passed = False
            result.failures.append({
                "check": "symbolic_rules",
                "message": reason,
                "severity": "HIGH",
            })
        emit_guardrail_check(
            check_name="symbolic_rules",
            decision="pass" if allowed else "fail",
            reason=reason if not allowed else None,
        )

        # Проверка противоречий
        contradictions = self.contradiction.check_slots(context)
        for c in contradictions:
            entry = {
                "check": "contradiction",
                "message": c.message,
                "severity": c.severity,
                "type": c.conflict_type,
            }
            if c.severity == "HIGH":
                result.passed = False
                result.failures.append(entry)
            else:
                result.warnings.append(entry)
        emit_guardrail_check(
            check_name="contradiction",
            decision=(
                "fail" if any(c.severity == "HIGH" for c in contradictions)
                else "warn" if contradictions
                else "pass"
            ),
            reason=(
                "; ".join(c.message for c in contradictions)
                if contradictions else None
            ),
            details={"count": len(contradictions)} if contradictions else None,
        )

        if not result.passed:
            logger.warning(
                "Guardrail blocked action '%s': %d failure(s), %d warning(s)",
                action, len(result.failures), len(result.warnings),
            )
        elif result.warnings:
            logger.info(
                "Action '%s' passed with %d warning(s)", action, len(result.warnings),
            )

        return result

    def validate_response(
        self,
        response: str,
        context: dict[str, Any],
        filled_slots: dict[str, Any] | None = None,
        knowledge_base: str = "",
    ) -> PipelineResult:
        """Валидация LLM-ответа через трёхуровневый конвейер с early exit.

        Tier 1 (<10ms):    FormatCheck + LexicalCheck
        Tier 2 (20-100ms): RepeatCheck + RepeatQuestionCheck + Contradiction
        Tier 3 (500ms+):   HallucinationCheck

        При блокирующей ошибке (HIGH/CRITICAL) на любом уровне —
        последующие уровни пропускаются.

        Args:
            response: Сгенерированный текст ответа.
            context: Текущий контекст разговора.
            filled_slots: Уже заполненные слоты (для repeat-question).
            knowledge_base: Референсный текст для проверки галлюцинаций.

        Returns:
            PipelineResult с результатом валидации и метриками по уровням.
        """
        pipeline_result = PipelineResult(
            passed=True,
            cleaned_response=response,
        )
        slots = filled_slots or {}

        # Пустой ответ — мгновенный отказ
        if not response or not response.strip():
            pipeline_result.passed = False
            pipeline_result.failures.append({
                "check": "empty_response",
                "message": "Response is empty",
                "severity": "HIGH",
            })
            emit_guardrail_check(
                check_name="empty_response",
                decision="fail",
                reason="Response is empty",
            )
            return pipeline_result

        # ── Tier 1: regex, формат (<10ms) ────────────────────── #
        tier1 = self._run_tier1(response, pipeline_result)
        pipeline_result.tier_results.append(tier1)

        if tier1.early_exit:
            pipeline_result.passed = False
            logger.warning(
                "Tier 1 early exit (%dms): %d failure(s), skipping Tier 2/3",
                int(tier1.duration_ms),
                len(tier1.failures),
            )
            return self._finalize(pipeline_result)

        # ── Tier 2: семантические (20-100ms) ─────────────────── #
        tier2 = self._run_tier2(response, slots, context, pipeline_result)
        pipeline_result.tier_results.append(tier2)

        if tier2.early_exit:
            pipeline_result.passed = False
            logger.warning(
                "Tier 2 early exit (%dms): %d failure(s), skipping Tier 3",
                int(tier2.duration_ms),
                len(tier2.failures),
            )
            return self._finalize(pipeline_result)

        # ── Tier 3: LLM-тяжёлые (500ms+) ────────────────────── #
        tier3 = self._run_tier3(response, slots, knowledge_base)
        pipeline_result.tier_results.append(tier3)

        return self._finalize(pipeline_result)

    def reset(self) -> None:
        """Сброс stateful-проверок (вызывать между сессиями)."""
        self.repeat_check.reset()

    # ── Tier 1: regex, формат ────────────────────────────────── #

    def _run_tier1(
        self,
        response: str,
        pipeline_result: PipelineResult,
    ) -> TierResult:
        """Tier 1 (<10ms): FormatCheck + LexicalCheck.

        Дешёвые детерминистические проверки на regex/формат.

        Args:
            response: Текст ответа.
            pipeline_result: Общий результат для обновления cleaned_response.

        Returns:
            TierResult с результатами проверок Tier 1.
        """
        tier = TierResult(tier=GuardrailTier.TIER_1)

        with TierTimer(tier):
            # FormatCheck — структура, длина, паттерны
            format_issues = self.format_check.check(response)
            for issue in format_issues:
                tier.add(CheckResult(
                    check_name="format_check",
                    passed=False,
                    severity=Severity.MEDIUM,
                    message=issue,
                    tier=GuardrailTier.TIER_1,
                ))

            # Проверка длины ответа
            word_count = len(response.split())
            if word_count > 500:
                tier.add(CheckResult(
                    check_name="response_length",
                    passed=False,
                    severity=Severity.LOW,
                    message=f"Response is very long ({word_count} words). Consider being more concise.",
                    tier=GuardrailTier.TIER_1,
                ))

            # LexicalCheck — тон, жаргон, естественность
            lexical_result = self.lexical.check(response)
            if lexical_result.has_issues:
                for issue in lexical_result.issues:
                    severity = (
                        Severity.HIGH
                        if issue.severity == "HIGH"
                        else Severity.MEDIUM
                    )
                    tier.add(CheckResult(
                        check_name=f"lexical:{issue.issue_type}",
                        passed=False,
                        severity=severity,
                        message=f"{issue.original} → {issue.suggestion}",
                        tier=GuardrailTier.TIER_1,
                    ))

                # Автокорректированный текст
                if lexical_result.cleaned_text != response:
                    pipeline_result.cleaned_response = lexical_result.cleaned_text

        emit_guardrail_check(
            check_name="format",
            decision="warn" if format_issues else "pass",
            reason="; ".join(format_issues) if format_issues else None,
            details={"count": len(format_issues)} if format_issues else None,
        )
        emit_guardrail_check(
            check_name="response_length",
            decision="warn" if word_count > 500 else "pass",
            details={"word_count": word_count},
        )
        emit_guardrail_check(
            check_name="lexical",
            decision="warn" if lexical_result.has_issues else "pass",
            details=(
                {"count": len(lexical_result.issues)}
                if lexical_result.has_issues else None
            ),
        )

        self._collect_into_pipeline(tier, pipeline_result)
        return tier

    # ── Tier 2: семантические ────────────────────────────────── #

    def _run_tier2(
        self,
        response: str,
        slots: dict[str, Any],
        context: dict[str, Any],
        pipeline_result: PipelineResult,
    ) -> TierResult:
        """Tier 2 (20-100ms): RepeatCheck + RepeatQuestionCheck + Contradiction.

        Семантические проверки, не требующие LLM.

        Args:
            response: Текст ответа.
            slots: Заполненные слоты.
            context: Контекст разговора.
            pipeline_result: Общий результат для сбора warnings.

        Returns:
            TierResult с результатами проверок Tier 2.
        """
        tier = TierResult(tier=GuardrailTier.TIER_2)
        rq_issues: list[Any] = []
        repeat_ok = True
        contradictions: list[Any] = []

        with TierTimer(tier):
            # RepeatQuestionCheck — не переспрашивать уже известные данные
            if slots:
                rq_issues = self.repeat_question.check(response, slots)
                for issue in rq_issues:
                    tier.add(CheckResult(
                        check_name="repeat_question",
                        passed=False,
                        severity=Severity.MEDIUM,
                        message=issue.suggestion,
                        tier=GuardrailTier.TIER_2,
                        metadata={
                            "slot": issue.slot_name,
                            "known_value": str(issue.slot_value),
                        },
                    ))

                if rq_issues:
                    correction = self.repeat_question.build_correction_prompt(rq_issues)
                    tier.add(CheckResult(
                        check_name="repeat_question_correction",
                        passed=True,
                        severity=Severity.LOW,
                        message=correction,
                        tier=GuardrailTier.TIER_2,
                    ))

            # RepeatCheck — не повторять предыдущие ответы
            repeat_ok = self.repeat_check.check_and_record(response)
            if not repeat_ok:
                tier.add(CheckResult(
                    check_name="repeat_response",
                    passed=False,
                    severity=Severity.LOW,
                    message=(
                        "Response is very similar to a recent response. "
                        "Consider varying phrasing."
                    ),
                    tier=GuardrailTier.TIER_2,
                ))

            # ContradictionChecker — противоречия в слотах
            contradictions = self.contradiction.check_slots(context)
            for c in contradictions:
                severity = Severity.HIGH if c.severity == "HIGH" else Severity.MEDIUM
                tier.add(CheckResult(
                    check_name="contradiction",
                    passed=False,
                    severity=severity,
                    message=c.message,
                    tier=GuardrailTier.TIER_2,
                    metadata={"type": c.conflict_type},
                ))

        emit_guardrail_check(
            check_name="repeat_question",
            decision="warn" if rq_issues else "pass",
            reason=(
                "; ".join(getattr(i, "suggestion", "") for i in rq_issues)
                if rq_issues else None
            ),
            details=(
                {"count": len(rq_issues)}
                if rq_issues
                else {"skipped": True} if not slots else None
            ),
        )
        emit_guardrail_check(
            check_name="repeat_response",
            decision="pass" if repeat_ok else "warn",
            reason=None if repeat_ok else "similar to a recent response",
        )
        emit_guardrail_check(
            check_name="contradiction",
            decision=(
                "fail" if any(c.severity == "HIGH" for c in contradictions)
                else "warn" if contradictions
                else "pass"
            ),
            reason=(
                "; ".join(c.message for c in contradictions)
                if contradictions else None
            ),
            details={"count": len(contradictions)} if contradictions else None,
        )

        self._collect_into_pipeline(tier, pipeline_result)
        return tier

    # ── Tier 3: LLM-тяжёлые ─────────────────────────────────── #

    def _run_tier3(
        self,
        response: str,
        slots: dict[str, Any],
        knowledge_base: str,
    ) -> TierResult:
        """Tier 3 (500ms+): HallucinationCheck.

        Дорогие проверки с использованием LLM/NLI.

        Args:
            response: Текст ответа.
            slots: Заполненные слоты (known_facts).
            knowledge_base: Референсный текст.

        Returns:
            TierResult с результатами проверок Tier 3.
        """
        tier = TierResult(tier=GuardrailTier.TIER_3)

        with TierTimer(tier):
            hallucination_warnings = self.hallucination.check(
                response,
                known_facts=slots,
                knowledge_base=knowledge_base,
            )
            for warning in hallucination_warnings:
                tier.add(CheckResult(
                    check_name="hallucination",
                    passed=False,
                    severity=Severity.MEDIUM,
                    message=warning,
                    tier=GuardrailTier.TIER_3,
                ))

        emit_guardrail_check(
            check_name="hallucination",
            decision="warn" if hallucination_warnings else "pass",
            reason=(
                "; ".join(hallucination_warnings)
                if hallucination_warnings else None
            ),
            details=(
                {"count": len(hallucination_warnings)}
                if hallucination_warnings else None
            ),
        )

        return tier

    # ── Внутренние хелперы ───────────────────────────────────── #

    @staticmethod
    def _collect_into_pipeline(
        tier: TierResult,
        pipeline_result: PipelineResult,
    ) -> None:
        """Перенести результаты уровня в общий PipelineResult.

        Failures и warnings собираются в dict-формате для обратной
        совместимости с существующим API.

        Args:
            tier: Результат выполненного уровня.
            pipeline_result: Общий результат конвейера.
        """
        for check in tier.checks:
            if check.passed:
                continue

            entry: dict[str, Any] = {
                "check": check.check_name,
                "message": check.message,
                "severity": check.severity.value,
            }
            entry.update(check.metadata)

            if check.is_blocking:
                pipeline_result.failures.append(entry)
            else:
                pipeline_result.warnings.append(entry)

    @staticmethod
    def _finalize(pipeline_result: PipelineResult) -> PipelineResult:
        """Финализировать результат конвейера.

        Определяет итоговый passed по наличию блокирующих failures.
        Логирует итоговую статистику.

        Args:
            pipeline_result: Собранный результат конвейера.

        Returns:
            Финализированный PipelineResult.
        """
        high_failures = [
            f for f in pipeline_result.failures
            if f.get("severity") in {"HIGH", "CRITICAL"}
        ]
        pipeline_result.passed = len(high_failures) == 0

        tiers_run = pipeline_result.tiers_executed
        total_ms = pipeline_result.total_duration_ms

        if not pipeline_result.passed:
            logger.warning(
                "Response validation FAILED: %d failure(s), %d warning(s), "
                "%d/%d tiers executed in %.1fms",
                len(pipeline_result.failures),
                len(pipeline_result.warnings),
                tiers_run, 3,
                total_ms,
            )
        else:
            logger.info(
                "Response validation PASSED: %d warning(s), "
                "%d/%d tiers executed in %.1fms",
                len(pipeline_result.warnings),
                tiers_run, 3,
                total_ms,
            )

        return pipeline_result
