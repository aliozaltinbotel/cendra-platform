"""Langfuse trace surface for the LLM invoke chokepoint.

Reference: ``brain_engine_advisory.md`` §5 (LLM observability).

Why this module exists
----------------------
``brain_engine.observability.exporters.prometheus_exporter`` already
captures aggregate counters (latency, tokens, cost) and that is what
the four Grafana dashboards visualise.  But Mümin's "I cannot see
what the engine is doing" feedback also asks for *per-call* replay:
the exact prompt, the exact completion, the model verdict, and the
cost — bound to a conversation thread he can navigate in a UI.

Langfuse is the LLM-trace product that fills that gap.  This client
is intentionally thin: it wraps the optional Langfuse SDK, exposes
one ``trace_llm`` async-context manager, and degrades to a no-op
when either the SDK is missing or the env keys are unset.  No
exception ever escapes the tracer — the invoke chokepoint must
never break because Langfuse is unhappy.

Usage
-----
    from brain_engine.observability.langfuse_client import (
        get_default_tracer,
    )

    tracer = get_default_tracer()
    async with tracer.trace_llm(
        provider="azure_openai",
        model="gpt-4o-mini",
        prompt=messages,
    ) as span:
        response = await self._do_invoke(messages, formatted)
        span.record_response(
            completion=response.content,
            tokens_input=response.usage.input_tokens,
            tokens_output=response.usage.output_tokens,
            cost_usd=cost,
        )

The ``span`` is a real Langfuse ``Generation`` object when the SDK
is wired, or a :class:`_NoopSpan` otherwise.  Both expose the same
``record_response`` and ``record_error`` surface so callers stay
provider-agnostic.

Pure-Python, no I/O at import time.  The langfuse SDK is loaded
lazily inside ``ensure_initialised`` so a missing dependency cannot
break the rest of the application.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Final

import structlog

__all__ = [
    "LangfuseSpan",
    "LangfuseTracer",
    "build_default_tracer",
    "get_default_tracer",
]


logger = structlog.get_logger(__name__)


# Environment variables read at first ``ensure_initialised`` call.
# ``LANGFUSE_HOST`` defaults to the docker-compose-published instance
# (``http://localhost:3001``); production deployments override via
# the AKS deployment manifest.
_ENV_PUBLIC_KEY: Final[str] = "LANGFUSE_PUBLIC_KEY"
_ENV_SECRET_KEY: Final[str] = "LANGFUSE_SECRET_KEY"
_ENV_HOST: Final[str] = "LANGFUSE_HOST"
_DEFAULT_HOST: Final[str] = "http://localhost:3001"


# ---------------------------------------------------------------------------
# Span surface
# ---------------------------------------------------------------------------


class LangfuseSpan:
    """Thin wrapper around a Langfuse ``Generation`` object.

    Carries the SDK handle internally; callers only see the
    ``record_response`` / ``record_error`` API.  Both methods are
    safe to call from inside the ``trace_llm`` context — they swallow
    any SDK-side exception and log a warning instead of propagating
    so an instrumentation bug never breaks the LLM call.
    """

    __slots__ = ("_generation", "_log")

    def __init__(self, generation: Any) -> None:
        self._generation = generation
        self._log = logger.bind(component="langfuse_span")

    def record_response(
        self,
        *,
        completion: str,
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Attach the model output and usage metrics to the span."""
        if self._generation is None:
            return
        try:
            self._generation.update(
                output=completion,
                usage={
                    "input": int(tokens_input),
                    "output": int(tokens_output),
                    "total": int(tokens_input) + int(tokens_output),
                    "unit": "TOKENS",
                    "input_cost": float(cost_usd) / 2.0,
                    "output_cost": float(cost_usd) / 2.0,
                    "total_cost": float(cost_usd),
                },
            )
        except Exception as exc:  # noqa: BLE001 — never break invoke
            self._log.warning(
                "langfuse_span_update_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def record_error(self, exc: BaseException) -> None:
        """Mark the span as errored with the failing exception."""
        if self._generation is None:
            return
        try:
            self._generation.update(
                level="ERROR",
                status_message=f"{type(exc).__name__}: {exc}",
            )
        except Exception as inner:  # noqa: BLE001 — never break invoke
            self._log.warning(
                "langfuse_span_error_failed",
                error=str(inner),
                error_type=type(inner).__name__,
            )


class _NoopSpan(LangfuseSpan):
    """Used when the Langfuse SDK is unavailable or not configured."""

    def __init__(self) -> None:
        super().__init__(generation=None)


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LangfuseTracer:
    """Owner of the Langfuse client handle for one process.

    Construction is two-step: ``__init__`` only stores configuration,
    ``ensure_initialised`` actually imports the SDK and creates the
    client on first use.  This lets the tracer be imported in
    environments that lack ``langfuse`` (CI lint, AST parse) without
    raising.

    Attributes:
        public_key: Langfuse project public key.  Empty disables the
            tracer.
        secret_key: Langfuse project secret key.  Empty disables the
            tracer.
        host: Langfuse base URL.  Defaults to the docker-compose
            published instance.
    """

    public_key: str = ""
    secret_key: str = ""
    host: str = _DEFAULT_HOST
    _client: Any = field(default=None, init=False, repr=False)
    _initialised: bool = field(default=False, init=False, repr=False)

    @property
    def enabled(self) -> bool:
        """Whether the tracer will produce real spans."""
        return bool(self.public_key) and bool(self.secret_key)

    def ensure_initialised(self) -> None:
        """Import the SDK and build a client on first use.

        Idempotent — repeated calls are no-ops.  Falls back to a
        disabled state on any import / construction error so the
        invoke chokepoint never sees a broken tracer.
        """
        if self._initialised:
            return
        self._initialised = True
        if not self.enabled:
            return
        try:
            from langfuse import Langfuse  # type: ignore[import-not-found]
        except ImportError:
            logger.info(
                "langfuse_sdk_not_installed",
                hint=(
                    "pip install langfuse>=2.0 to enable trace UI;"
                    " falling back to no-op spans."
                ),
            )
            return
        try:
            self._client = Langfuse(
                public_key=self.public_key,
                secret_key=self.secret_key,
                host=self.host,
            )
            logger.info(
                "langfuse_tracer_initialised",
                host=self.host,
            )
        except Exception as exc:  # noqa: BLE001 — never break callers
            self._client = None
            logger.warning(
                "langfuse_tracer_init_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    @asynccontextmanager
    async def trace_llm(
        self,
        *,
        provider: str,
        model: str,
        prompt: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[LangfuseSpan]:
        """Async-context manager yielding a :class:`LangfuseSpan`.

        Args:
            provider: LLM provider (e.g. ``"azure_openai"``).
            model: Concrete model name (e.g. ``"gpt-4o-mini"``).
            prompt: The chat messages sent to the model.  Stored on
                the span as ``input``.
            metadata: Optional free-form dict attached to the span.

        Yields:
            A :class:`LangfuseSpan` — always non-``None``; falls back
            to :class:`_NoopSpan` when the tracer is disabled.
        """
        self.ensure_initialised()
        if self._client is None:
            yield _NoopSpan()
            return
        generation = None
        try:
            generation = self._client.generation(
                name=f"{provider}:{model}",
                model=model,
                model_parameters={"provider": provider},
                input=prompt,
                metadata=dict(metadata or {}),
            )
        except Exception as exc:  # noqa: BLE001 — never break invoke
            logger.warning(
                "langfuse_generation_create_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            yield _NoopSpan()
            return
        span = LangfuseSpan(generation=generation)
        try:
            yield span
        except BaseException as exc:
            span.record_error(exc)
            raise
        finally:
            try:
                generation.end()
            except Exception as exc:  # noqa: BLE001 — never break invoke
                logger.warning(
                    "langfuse_generation_end_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    def shutdown(self) -> None:
        """Flush any pending Langfuse events.

        Should be called from the FastAPI lifespan ``shutdown`` hook
        so trace events queued in the SDK background thread reach
        the server before the pod terminates.
        """
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "langfuse_flush_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_DEFAULT: LangfuseTracer | None = None


def build_default_tracer() -> LangfuseTracer:
    """Construct a tracer from environment variables.

    Reads ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` /
    ``LANGFUSE_HOST`` once and stores the result.  Subsequent calls
    return the same instance so every callsite shares one client.
    """
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = LangfuseTracer(
            public_key=os.getenv(_ENV_PUBLIC_KEY, ""),
            secret_key=os.getenv(_ENV_SECRET_KEY, ""),
            host=os.getenv(_ENV_HOST, _DEFAULT_HOST),
        )
    return _DEFAULT


def get_default_tracer() -> LangfuseTracer:
    """Convenience accessor — same as :func:`build_default_tracer`."""
    return build_default_tracer()
