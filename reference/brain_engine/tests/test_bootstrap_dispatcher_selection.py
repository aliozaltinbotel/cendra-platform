"""Tests for bootstrap dispatcher selection in ``multi_tenant``.

``BOOTSTRAP_QUEUE_ENABLED`` switches a pod between the in-process
asyncio dispatcher (Stage 1) and the Service Bus producer (Stage 2).
A flag that is on but missing a connection string must fall back to
asyncio rather than crash — dispatcher selection sits on the serving
path and a misconfigured env var must never take it down.
"""

from __future__ import annotations

import pytest

from api_server.bootstrap.multi_tenant import (
    _build_dispatcher,
    _chain_close,
    _queue_enabled,
)
from brain_engine.tenants import (
    AsyncioBootstrapDispatcher,
    ServiceBusBootstrapDispatcher,
)

_FLAG_ENV = "BOOTSTRAP_QUEUE_ENABLED"
_CONN_ENV = "AZURE_SERVICEBUS_CONNECTION_STRING"
_FAKE_CONN = "Endpoint=sb://x.servicebus.windows.net/;SharedAccessKey=k"


def test_queue_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    assert _queue_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_queue_enabled_for_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv(_FLAG_ENV, value)
    assert _queue_enabled() is True


def test_build_dispatcher_default_is_asyncio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    dispatcher, close = _build_dispatcher()
    assert isinstance(dispatcher, AsyncioBootstrapDispatcher)
    assert close is None


def test_build_dispatcher_service_bus_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLAG_ENV, "true")
    monkeypatch.setenv(_CONN_ENV, _FAKE_CONN)
    dispatcher, close = _build_dispatcher()
    assert isinstance(dispatcher, ServiceBusBootstrapDispatcher)
    assert close is not None  # the sender's aclose cleanup


def test_build_dispatcher_falls_back_without_conn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLAG_ENV, "true")
    monkeypatch.delenv(_CONN_ENV, raising=False)
    dispatcher, close = _build_dispatcher()
    assert isinstance(dispatcher, AsyncioBootstrapDispatcher)
    assert close is None


async def test_chain_close_runs_all_in_order() -> None:
    order: list[str] = []

    async def first() -> None:
        order.append("first")

    async def second() -> None:
        order.append("second")

    combined = _chain_close(first, second)
    assert combined is not None
    await combined()
    assert order == ["first", "second"]


async def test_chain_close_skips_none() -> None:
    order: list[str] = []

    async def only() -> None:
        order.append("only")

    combined = _chain_close(None, only, None)
    assert combined is not None
    await combined()
    assert order == ["only"]


def test_chain_close_all_none_returns_none() -> None:
    assert _chain_close(None, None) is None
