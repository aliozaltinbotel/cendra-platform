"""Tests for the Task 3 ``BRAIN_MEMORY_INJECT_ENABLED`` lifespan wiring.

Task 3 of CLAUDE_CODE_WIRING_FIX_PLAN.md (see docs/wiring_audit.md)
adds a single env-flagged alias inside ``api_server.bootstrap.memory``:
when ``BRAIN_MEMORY_INJECT_ENABLED`` is truthy, the freshly-initialised
:class:`brain_engine.memory.factory.MemorySystem` (already exposed on
``application.state.memory`` for module-level readers) is also exposed
on ``application.state.memory_system`` — the slot the
``ConversationService`` FastAPI dependency from Task 2 reads.

The alias is intentionally *the same object*, not a second instance.
That keeps the lifespan's existing ``shutdown()`` path on
``app.state.memory`` authoritative — no double-close, no second I/O
warm-up, no second Redis/Qdrant connection pool.

These tests pin five guarantees:

* Flag plumbing — default off, recognised truthy / falsy values.
* Alias appears on ``app.state.memory_system`` when the flag is on.
* Alias is absent when the flag is off (``getattr`` returns ``None``).
* Alias and the legacy ``app.state.memory`` slot point at the same
  object, so a single ``shutdown()`` covers both.
* The wire-up still calls ``MemorySystem.initialize`` exactly once,
  regardless of the flag.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from api_server.bootstrap import memory as bootstrap_memory
from api_server.bootstrap.memory import memory_inject_enabled, wire
from config.settings import Settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_inject_flag() -> Iterator[None]:
    """Strip ``BRAIN_MEMORY_INJECT_ENABLED`` between tests."""
    previous = os.environ.pop("BRAIN_MEMORY_INJECT_ENABLED", None)
    try:
        yield
    finally:
        os.environ.pop("BRAIN_MEMORY_INJECT_ENABLED", None)
        if previous is not None:
            os.environ["BRAIN_MEMORY_INJECT_ENABLED"] = previous


def _patched_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> MagicMock:
    """Replace ``create_memory_system`` with a mock returning a stub.

    The stub stands in for a real :class:`MemorySystem` so the test
    never opens Redis / Qdrant.  ``initialize`` is an :class:`AsyncMock`
    so the test can assert it was awaited exactly once.
    """
    fake_memory: Any = MagicMock(name="MemorySystem")
    fake_memory.initialize = AsyncMock()
    monkeypatch.setattr(
        bootstrap_memory,
        "create_memory_system",
        lambda **_kwargs: fake_memory,
    )
    return fake_memory


# ---------------------------------------------------------------------------
# Flag plumbing
# ---------------------------------------------------------------------------


def test_flag_off_by_default() -> None:
    assert memory_inject_enabled() is False


@pytest.mark.parametrize(
    "raw", ["1", "true", "TRUE", "yes", "on", " 1 "],
)
def test_flag_truthy_values(raw: str) -> None:
    os.environ["BRAIN_MEMORY_INJECT_ENABLED"] = raw
    assert memory_inject_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "garbage"])
def test_flag_falsy_values(raw: str) -> None:
    os.environ["BRAIN_MEMORY_INJECT_ENABLED"] = raw
    assert memory_inject_enabled() is False


# ---------------------------------------------------------------------------
# Wire-up behaviour
# ---------------------------------------------------------------------------


async def test_wire_sets_memory_system_alias_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_memory = _patched_factory(monkeypatch)
    os.environ["BRAIN_MEMORY_INJECT_ENABLED"] = "1"
    app = FastAPI()
    settings = Settings()

    result = await wire(app, settings=settings)

    assert result is fake_memory
    assert app.state.memory is fake_memory
    assert app.state.memory_system is fake_memory


async def test_wire_skips_alias_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the flag, the new slot stays unset on ``app.state``."""
    fake_memory = _patched_factory(monkeypatch)
    app = FastAPI()
    settings = Settings()

    await wire(app, settings=settings)

    assert app.state.memory is fake_memory
    assert getattr(app.state, "memory_system", None) is None


async def test_alias_points_to_same_instance_as_legacy_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The alias must share identity with ``app.state.memory``.

    Two different objects would mean two ``MemorySystem`` instances,
    two Redis connection pools, and a double ``shutdown()`` path.
    Identity check guards against accidental drift.
    """
    fake_memory = _patched_factory(monkeypatch)
    os.environ["BRAIN_MEMORY_INJECT_ENABLED"] = "1"
    app = FastAPI()
    settings = Settings()

    await wire(app, settings=settings)

    assert app.state.memory is fake_memory
    assert app.state.memory is app.state.memory_system
    assert id(app.state.memory) == id(app.state.memory_system)


async def test_wire_initializes_memory_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag flip must not double-initialise the cognitive memory."""
    fake_memory = _patched_factory(monkeypatch)
    os.environ["BRAIN_MEMORY_INJECT_ENABLED"] = "1"
    app = FastAPI()
    settings = Settings()

    await wire(app, settings=settings)

    assert fake_memory.initialize.await_count == 1
