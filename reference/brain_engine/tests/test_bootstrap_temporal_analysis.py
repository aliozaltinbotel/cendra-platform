"""Tests for the temporal analysis lifespan wire (Phase 3, PR3b.1).

The server cannot boot locally (needs cluster Redis / Qdrant), so the
wiring is proven at the unit the wire owns: store injection into the
router's deps, the ``app.state`` marker, and chat-model degradation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import api_server.bootstrap.temporal_analysis as boot
import brain_engine.api.temporal_analysis_endpoints as endpoints


@pytest.fixture(autouse=True)
def _reset_deps() -> Any:
    endpoints._deps.clear()
    yield
    endpoints._deps.clear()


def _app() -> Any:
    return SimpleNamespace(state=SimpleNamespace())


def _settings() -> Any:
    return SimpleNamespace(llm_model="gpt-4o")


def test_wire_injects_stores_and_model_and_marks_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    monkeypatch.setattr(boot, "_build_chat_model", lambda settings: sentinel)
    memory = SimpleNamespace(knowledge_graph="KG", guest_history="GH")
    app = _app()

    boot.wire(app, settings=_settings(), memory=memory)

    assert endpoints._deps["knowledge_graph"] == "KG"
    assert endpoints._deps["guest_history"] == "GH"
    assert endpoints._deps["chat_model"] is sentinel
    assert app.state.temporal_analysis_wired is True


def test_wire_without_memory_omits_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boot, "_build_chat_model", lambda settings: None)
    app = _app()

    boot.wire(app, settings=_settings(), memory=None)

    assert "knowledge_graph" not in endpoints._deps
    assert "guest_history" not in endpoints._deps
    assert "chat_model" not in endpoints._deps
    assert app.state.temporal_analysis_wired is True


def test_wire_skips_missing_store_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boot, "_build_chat_model", lambda settings: None)
    # Memory exposes a knowledge graph but no guest history.
    memory = SimpleNamespace(knowledge_graph="KG", guest_history=None)

    boot.wire(_app(), settings=_settings(), memory=memory)

    assert endpoints._deps["knowledge_graph"] == "KG"
    assert "guest_history" not in endpoints._deps


def test_build_chat_model_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("no provider config")

    monkeypatch.setattr(
        "brain_engine.models.factory.init_chat_model",
        _boom,
    )

    assert boot._build_chat_model(_settings()) is None
