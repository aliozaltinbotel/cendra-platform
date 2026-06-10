"""Circuit Breaker — защита от каскадных отказов внешних сервисов.

Реализация паттерна Circuit Breaker для Azure OpenAI, Redis и Qdrant.
Предотвращает бессмысленные retry при 100% failure rate: после N
последовательных ошибок цепь «размыкается» и вызовы отклоняются
мгновенно (fail-fast) до восстановления сервиса.

Состояния:
    CLOSED   → нормальная работа, ошибки считаются
    OPEN     → сервис недоступен, вызовы отклоняются мгновенно
    HALF_OPEN → пробный вызов для проверки восстановления

Использование:
    # Как декоратор
    @with_circuit_breaker(LLM_CIRCUIT)
    async def call_llm(prompt: str) -> str:
        ...

    # Как контекст-менеджер
    async with circuit.protect():
        result = await external_call()

    # Программно
    if circuit.allow_request():
        try:
            result = await external_call()
            circuit.record_success()
        except Exception as exc:
            circuit.record_failure()
            raise
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


# ── Модели ──────────────────────────────────────────────────── #


class CircuitState(StrEnum):
    """Состояния Circuit Breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitBreakerConfig:
    """Конфигурация Circuit Breaker.

    Attributes:
        name: Человекочитаемое имя для логирования.
        failure_threshold: Количество последовательных ошибок для размыкания.
        recovery_timeout: Секунды ожидания перед пробным вызовом (HALF_OPEN).
        half_open_max_calls: Макс. пробных вызовов в HALF_OPEN.
        success_threshold: Успешных вызовов в HALF_OPEN для замыкания.
        excluded_exceptions: Исключения, не считающиеся сбоем сервиса.
    """

    name: str = "default"
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 1
    success_threshold: int = 1
    excluded_exceptions: tuple[type[Exception], ...] = ()


class CircuitOpenError(Exception):
    """Вызов отклонён — цепь разомкнута (сервис недоступен).

    Attributes:
        circuit_name: Имя Circuit Breaker.
        remaining_seconds: Секунд до пробного вызова.
    """

    def __init__(self, circuit_name: str, remaining_seconds: float) -> None:
        self.circuit_name = circuit_name
        self.remaining_seconds = remaining_seconds
        super().__init__(
            f"Circuit '{circuit_name}' is OPEN, "
            f"retry in {remaining_seconds:.1f}s"
        )


# ── Circuit Breaker ─────────────────────────────────────────── #


class CircuitBreaker:
    """Реализация паттерна Circuit Breaker.

    Потокобезопасен для asyncio (single-threaded event loop).
    Состояние хранится в памяти — при рестарте сбрасывается в CLOSED.

    Args:
        config: Конфигурация Circuit Breaker.
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config: Final[CircuitBreakerConfig] = config or CircuitBreakerConfig()
        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._last_failure_time: float = 0.0
        self._half_open_calls: int = 0

    # ── Публичный API ────────────────────────────────────────── #

    @property
    def state(self) -> CircuitState:
        """Текущее состояние с учётом таймаута восстановления."""
        if self._state == CircuitState.OPEN:
            if self._recovery_elapsed():
                self._transition_to(CircuitState.HALF_OPEN)
        return self._state

    @property
    def config(self) -> CircuitBreakerConfig:
        """Конфигурация (только чтение)."""
        return self._config

    @property
    def failure_count(self) -> int:
        """Текущее количество последовательных ошибок."""
        return self._failure_count

    def allow_request(self) -> bool:
        """Разрешён ли вызов в текущем состоянии.

        Returns:
            True если вызов разрешён.
        """
        current = self.state

        if current == CircuitState.CLOSED:
            return True

        if current == CircuitState.HALF_OPEN:
            return self._half_open_calls < self._config.half_open_max_calls

        return False

    def record_success(self) -> None:
        """Зафиксировать успешный вызов."""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            logger.debug(
                "Circuit '%s' HALF_OPEN success %d/%d",
                self._config.name,
                self._success_count,
                self._config.success_threshold,
            )
            if self._success_count >= self._config.success_threshold:
                self._transition_to(CircuitState.CLOSED)
        else:
            # CLOSED — сбрасываем счётчик ошибок
            self._failure_count = 0

    def record_failure(self, exc: Exception | None = None) -> None:
        """Зафиксировать неуспешный вызов.

        Args:
            exc: Исключение, если нужно проверить excluded_exceptions.
        """
        # Исключённые ошибки не считаются сбоем сервиса
        if exc is not None and isinstance(exc, self._config.excluded_exceptions):
            return

        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            # Пробный вызов не прошёл — обратно в OPEN
            logger.warning(
                "Circuit '%s' probe failed, returning to OPEN",
                self._config.name,
            )
            self._transition_to(CircuitState.OPEN)
        elif self._failure_count >= self._config.failure_threshold:
            self._transition_to(CircuitState.OPEN)

    def reset(self) -> None:
        """Принудительный сброс в CLOSED (для тестов и мониторинга)."""
        self._transition_to(CircuitState.CLOSED)

    def protect(self) -> _CircuitContext:
        """Async context manager для защиты блока кода.

        Usage:
            async with circuit.protect():
                result = await external_call()

        Raises:
            CircuitOpenError: Если цепь разомкнута.
        """
        return _CircuitContext(self)

    # ── Приватные методы ─────────────────────────────────────── #

    def _recovery_elapsed(self) -> bool:
        """Прошло ли достаточно времени для пробного вызова."""
        if self._last_failure_time == 0.0:
            return True
        elapsed = time.monotonic() - self._last_failure_time
        return elapsed >= self._config.recovery_timeout

    def _remaining_recovery_seconds(self) -> float:
        """Секунды до перехода в HALF_OPEN."""
        if self._last_failure_time == 0.0:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_time
        return max(0.0, self._config.recovery_timeout - elapsed)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Переход в новое состояние с логированием и сбросом счётчиков."""
        old_state = self._state
        self._state = new_state

        if new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0

        if new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            self._success_count = 0

        if new_state == CircuitState.OPEN:
            self._half_open_calls = 0
            self._success_count = 0

        if old_state != new_state:
            log_fn = logger.warning if new_state == CircuitState.OPEN else logger.info
            log_fn(
                "Circuit '%s' transitioned: %s → %s (failures=%d)",
                self._config.name,
                old_state.value,
                new_state.value,
                self._failure_count,
            )

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self._config.name!r}, "
            f"state={self._state.value!r}, "
            f"failures={self._failure_count})"
        )


# ── Context Manager ─────────────────────────────────────────── #


class _CircuitContext:
    """Async context manager для CircuitBreaker.protect().

    Автоматически вызывает record_success() / record_failure()
    при выходе из блока.
    """

    __slots__ = ("_breaker",)

    def __init__(self, breaker: CircuitBreaker) -> None:
        self._breaker = breaker

    async def __aenter__(self) -> CircuitBreaker:
        if not self._breaker.allow_request():
            remaining = self._breaker._remaining_recovery_seconds()
            raise CircuitOpenError(self._breaker.config.name, remaining)
        if self._breaker.state == CircuitState.HALF_OPEN:
            self._breaker._half_open_calls += 1
        return self._breaker

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        if exc_type is None:
            self._breaker.record_success()
        elif isinstance(exc_val, Exception):
            self._breaker.record_failure(exc_val)
        # Не подавляем исключения
        return False


# ── Pre-built circuits ──────────────────────────────────────── #

LLM_CIRCUIT: Final[CircuitBreaker] = CircuitBreaker(
    CircuitBreakerConfig(
        name="llm",
        failure_threshold=5,
        recovery_timeout=30.0,
        half_open_max_calls=1,
        success_threshold=1,
        excluded_exceptions=(ValueError, TypeError),
    ),
)

REDIS_CIRCUIT: Final[CircuitBreaker] = CircuitBreaker(
    CircuitBreakerConfig(
        name="redis",
        failure_threshold=5,
        recovery_timeout=15.0,
        half_open_max_calls=2,
        success_threshold=2,
        excluded_exceptions=(ValueError,),
    ),
)

QDRANT_CIRCUIT: Final[CircuitBreaker] = CircuitBreaker(
    CircuitBreakerConfig(
        name="qdrant",
        failure_threshold=5,
        recovery_timeout=20.0,
        half_open_max_calls=1,
        success_threshold=1,
        excluded_exceptions=(ValueError,),
    ),
)


# ── Декоратор ───────────────────────────────────────────────── #


def with_circuit_breaker(
    circuit: CircuitBreaker | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Декоратор для защиты async-функций через Circuit Breaker.

    Сохраняет сигнатуру оборачиваемой функции (ParamSpec).

    Args:
        circuit: Экземпляр CircuitBreaker. По умолчанию LLM_CIRCUIT.

    Returns:
        Декоратор-обёртка.

    Usage:
        @with_circuit_breaker(REDIS_CIRCUIT)
        async def get_from_redis(key: str) -> str:
            ...
    """
    effective_circuit = circuit or LLM_CIRCUIT

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            async with effective_circuit.protect():
                return await func(*args, **kwargs)  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]

    return decorator
