"""Модели для многоуровневого guardrail-конвейера.

Определяет уровни (Tier), результат отдельной проверки (CheckResult),
результат уровня (TierResult) и критерий серьёзности (Severity).

Tier 1 (<10ms)   — regex/формат: FormatCheck + LexicalCheck
Tier 2 (20-100ms) — семантические: RepeatCheck + RepeatQuestionCheck + Contradiction
Tier 3 (500ms+)  — LLM-тяжёлые: HallucinationCheck + NLI/LLM Judge
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any, Final


class GuardrailTier(IntEnum):
    """Уровень guardrail-проверки (по возрастанию стоимости).

    Числовое значение совпадает с номером — удобно для сравнений:
        if tier >= GuardrailTier.TIER_2: ...
    """

    TIER_1 = 1  # <10ms — regex, формат, длина
    TIER_2 = 2  # 20-100ms — семантические проверки
    TIER_3 = 3  # 500ms+ — LLM judge, NLI, hallucination


class Severity(StrEnum):
    """Серьёзность найденной проблемы."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# Severity, при которых tier считается проваленным и последующие тиры пропускаются
EARLY_EXIT_SEVERITIES: Final[frozenset[Severity]] = frozenset({
    Severity.HIGH,
    Severity.CRITICAL,
})


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Результат одной guardrail-проверки.

    Иммутабельный value object — не изменяется после создания.

    Attributes:
        check_name: Уникальное имя проверки (e.g. "format_check", "hallucination").
        passed: Прошла ли проверка.
        severity: Серьёзность проблемы (LOW/MEDIUM/HIGH/CRITICAL).
        message: Описание найденной проблемы.
        tier: Уровень, на котором выполнена проверка.
        metadata: Дополнительные данные (slot_name, correction_prompt и т.д.).
    """

    check_name: str
    passed: bool
    severity: Severity = Severity.LOW
    message: str = ""
    tier: GuardrailTier = GuardrailTier.TIER_1
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        """Блокирующая ли проблема (вызывает early exit)."""
        return not self.passed and self.severity in EARLY_EXIT_SEVERITIES


@dataclass(slots=True)
class TierResult:
    """Результат выполнения одного уровня guardrail-конвейера.

    Мутабельный — собирает CheckResult по мере выполнения проверок.

    Attributes:
        tier: Номер выполненного уровня.
        checks: Результаты отдельных проверок на этом уровне.
        duration_ms: Время выполнения уровня в миллисекундах.
        early_exit: Был ли early exit на этом уровне.
    """

    tier: GuardrailTier
    checks: list[CheckResult] = field(default_factory=list)
    duration_ms: float = 0.0
    early_exit: bool = False

    @property
    def passed(self) -> bool:
        """Прошёл ли уровень без блокирующих проблем."""
        return not any(c.is_blocking for c in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        """Все провалившиеся проверки на этом уровне."""
        return [c for c in self.checks if not c.passed]

    @property
    def warnings(self) -> list[CheckResult]:
        """Проверки с неблокирующими проблемами."""
        return [
            c for c in self.checks
            if not c.passed and c.severity not in EARLY_EXIT_SEVERITIES
        ]

    def add(self, check: CheckResult) -> None:
        """Добавить результат проверки.

        Устанавливает early_exit если проверка блокирующая.

        Args:
            check: Результат отдельной проверки.
        """
        self.checks.append(check)
        if check.is_blocking:
            self.early_exit = True


class TierTimer:
    """Контекст-менеджер для замера времени выполнения уровня.

    Usage:
        tier_result = TierResult(tier=GuardrailTier.TIER_1)
        with TierTimer(tier_result):
            # выполнить проверки
            ...
        # tier_result.duration_ms заполнено
    """

    __slots__ = ("_result", "_start")

    def __init__(self, result: TierResult) -> None:
        self._result = result
        self._start: float = 0.0

    def __enter__(self) -> TierTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> bool:
        elapsed = (time.perf_counter() - self._start) * 1000
        self._result.duration_ms = round(elapsed, 2)
        return False
