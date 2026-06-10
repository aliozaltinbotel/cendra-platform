"""REST ``/conversations`` must wire the property profile store.

``get_conversation_service`` builds the :class:`ConversationService`
singleton for the REST endpoint.  It previously omitted
``profile_store`` / ``owner_profile_store``, so the REST pipeline ran
with ``self._profile_store is None`` → ``_load_property_knowledge``
skipped the profile lookup → the agent deferred every property
question even though the harvested profile carried the answer (the
AG-UI SSE handler wired both stores and worked).  These tests pin that
both stores now flow from ``app.state`` into the service.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import brain_engine.api.conversation_endpoints as ce
from brain_engine.api.conversation_endpoints import get_conversation_service


def _request(**state: Any) -> Any:
    """A fake FastAPI request exposing ``app.state`` attributes."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


def _reset_singleton() -> None:
    ce._conversation_service = None


def test_rest_service_receives_profile_store_from_app_state() -> None:
    """The property profile store on ``app.state`` reaches the service."""
    _reset_singleton()
    profile_store = object()
    owner_store = object()
    service = get_conversation_service(
        _request(
            property_profile_store=profile_store,
            owner_profile_store=owner_store,
        ),
    )
    assert service._profile_store is profile_store
    assert service._owner_profile_store is owner_store
    _reset_singleton()


def test_rest_service_tolerates_absent_stores() -> None:
    """Missing ``app.state`` attributes degrade to ``None`` (no crash) —
    preserves minimal-config environments."""
    _reset_singleton()
    service = get_conversation_service(_request())
    assert service._profile_store is None
    assert service._owner_profile_store is None
    _reset_singleton()
