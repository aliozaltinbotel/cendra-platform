"""Модуль durability — устойчивое выполнение, конкурентность и обработка задач.

Вдохновлён архитектурой LangGraph, адаптирован для Brain Engine.
Заменяет мультиагентную систему Cendra единой моделью конкурентности.

Компоненты:
    PipelineCheckpointer — сохранение/восстановление состояния после каждого шага.
    RetryPolicy          — экспоненциальный backoff с jitter для LLM/DB вызовов.
    CircuitBreaker       — защита от каскадных отказов (CLOSED→OPEN→HALF_OPEN).
    InterruptResume      — пауза конвейера для одобрения человеком.
    DurablePipeline      — оркестрация выполнения с чекпоинтами.
    ParallelStep         — параллельное выполнение шагов через asyncio.gather.
    TaskQueue            — фоновая очередь задач на Redis.
    WorkerPool           — async worker pool.
"""

from brain_engine.durability.checkpointer import (
    PipelineCheckpointer,
    PipelineState,
    StepResult,
)
from brain_engine.durability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
    LLM_CIRCUIT,
    QDRANT_CIRCUIT,
    REDIS_CIRCUIT,
    with_circuit_breaker,
)
from brain_engine.durability.interrupt import InterruptResume, PipelineInterrupt
from brain_engine.durability.parallel import (
    ParallelResult,
    ParallelStep,
    parallel,
    parallel_map,
)
from brain_engine.durability.pipeline import DurablePipeline, PipelineContext
from brain_engine.durability.retry import RetryPolicy, with_retry
from brain_engine.durability.task_queue import Task, TaskQueue
from brain_engine.durability.worker_pool import WorkerPool

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitOpenError",
    "CircuitState",
    "DurablePipeline",
    "InterruptResume",
    "LLM_CIRCUIT",
    "ParallelResult",
    "ParallelStep",
    "PipelineCheckpointer",
    "PipelineContext",
    "PipelineInterrupt",
    "PipelineState",
    "QDRANT_CIRCUIT",
    "REDIS_CIRCUIT",
    "RetryPolicy",
    "StepResult",
    "Task",
    "TaskQueue",
    "with_circuit_breaker",
    "with_retry",
    "WorkerPool",
    "parallel",
    "parallel_map",
]
