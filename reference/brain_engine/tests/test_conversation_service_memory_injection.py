"""Tests for the Task 2 ``memory_system`` DI parameter.

Task 2 of CLAUDE_CODE_WIRING_FIX_PLAN.md (see docs/wiring_audit.md
for the baseline) widens the
:class:`brain_engine.conversation.service.ConversationService`
constructor with an optional ``memory_system`` slot, and surfaces it
through the ``get_conversation_service`` FastAPI dependency.

These tests pin three guarantees:

* The constructor accepts a ``memory_system`` kwarg and stores it on
  ``self._memory_system`` unchanged.
* The constructor still works with no ``memory_system`` passed
  (Task 3 lands the lifespan wire-up later — until then the slot is
  ``None`` and the pre-Task-4 behaviour is preserved).
* The ``get_conversation_service`` FastAPI dependency reads
  ``request.app.state.memory_system`` and forwards it into the
  constructor — so once Task 3 populates that state slot, the
  conversation pipeline picks the system up automatically.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from brain_engine.api import conversation_endpoints
from brain_engine.api.conversation_endpoints import get_conversation_service
from brain_engine.conversation.service import ConversationService

# ---------------------------------------------------------------------------
# Fixture — reset the module-level singleton between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_conversation_service_singleton() -> None:
    """Clear the ``_conversation_service`` cache on every test entry.

    ``get_conversation_service`` lazily caches the service in a module
    global; without this reset, an early test would freeze a service
    built without the new ``memory_system`` slot and starve the
    later tests of their proof.
    """
    conversation_endpoints._conversation_service = None
    yield
    conversation_endpoints._conversation_service = None


# ---------------------------------------------------------------------------
# Constructor accepts the new kwarg
# ---------------------------------------------------------------------------


def test_constructor_accepts_memory_system_kwarg() -> None:
    """``memory_system`` propagates through to ``self._memory_system``."""
    fake_memory: Any = MagicMock(name="MemorySystem")
    service = ConversationService(memory_system=fake_memory)
    assert service._memory_system is fake_memory


def test_constructor_default_memory_system_is_none() -> None:
    """No kwarg = None, preserves pre-Task-2 behaviour."""
    service = ConversationService()
    assert service._memory_system is None


def test_constructor_memory_system_does_not_affect_other_attrs() -> None:
    """Passing memory_system does not alter unrelated defaults."""
    fake_memory: Any = MagicMock(name="MemorySystem")
    service = ConversationService(memory_system=fake_memory)
    # Sanity — independent attributes still default-built.
    assert service._case_store is None
    assert service._pm_fact_store is None
    assert service._orchestrator is None
    assert service._reservation_prefetcher is None


# ---------------------------------------------------------------------------
# Endpoint dependency reads app.state.memory_system
# ---------------------------------------------------------------------------


def test_get_service_forwards_memory_from_app_state() -> None:
    """``app.state.memory_system`` reaches the service constructor."""
    fake_memory: Any = MagicMock(name="MemorySystem")
    fake_request = MagicMock()
    fake_request.app.state.case_store = None
    fake_request.app.state.pm_fact_store = None
    fake_request.app.state.orchestrator = None
    fake_request.app.state.reservation_prefetcher = None
    fake_request.app.state.memory_system = fake_memory

    service = get_conversation_service(fake_request)

    assert service._memory_system is fake_memory


def test_get_conversation_service_handles_missing_memory_system_attr() -> None:
    """When ``app.state`` has no ``memory_system`` slot, fall back to None.

    Important for Task 3 — until the lifespan wire-up lands, the
    attribute will not exist on ``app.state``.  ``getattr(..., None)``
    inside ``get_conversation_service`` keeps the dependency working.
    """

    class _BareState:
        # Intentionally empty — no ``memory_system`` attribute, mimics
        # a deployment that has not yet shipped Task 3.
        pass

    fake_request = MagicMock()
    fake_request.app.state = _BareState()

    service = get_conversation_service(fake_request)

    assert service._memory_system is None
