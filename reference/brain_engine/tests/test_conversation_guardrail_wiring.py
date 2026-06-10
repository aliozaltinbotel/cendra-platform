"""Sprint 6 W3 wiring tests — foundation guardrail on conversation service.

Pins:

* :func:`_foundation_guardrail_enabled` reads
  ``BRAIN_FOUNDATION_GUARDRAIL_ENABLED`` and returns ``False`` by
  default — operators must opt in.
* :meth:`ConversationService._apply_foundation_guardrail` is a
  no-op when the flag is off, when the resolver is missing,
  when the message is empty, or when the resolver returns
  ``False``.  Resolver failures are swallowed.
* When flag-on + resolver returns ``True``, the method mirrors
  the existing ``approval`` mode by setting
  :pyattr:`PipelineState.requires_pm_approval` to ``True``,
  :pyattr:`response_flags.is_need_attention` to ``True``, and
  :pyattr:`response_flags.send_status` to ``False``.
"""

from __future__ import annotations

import pytest

from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
    ResponseFlags,
)
from brain_engine.conversation.service import (
    ConversationService,
    _foundation_guardrail_enabled,
)

# ── fixtures ──────────────────────────────────────────────── #


def _state(
    *,
    message: str = "early check-in please",
    property_id: str = "prop-1",
) -> PipelineState:
    """Build a minimal :class:`PipelineState` for the guardrail step."""
    request = ConversationRequest(
        customer_id="customer-1",
        property_id=property_id,
        guest_name="guest-1",
        history=(),
        guest_message=message,
    )
    state = PipelineState(request=request)
    state.cleaned_message = message
    state.response_flags = ResponseFlags()
    return state


def _service(
    *,
    resolver: object | None = None,
) -> ConversationService:
    """Build a service with everything but the guardrail unwired."""
    return ConversationService(
        foundation_guardrail_resolver=resolver,
    )


# ── env flag ──────────────────────────────────────────────── #


def test_flag_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the env var set, the helper returns ``False``."""
    monkeypatch.delenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        raising=False,
    )
    assert _foundation_guardrail_enabled() is False


@pytest.mark.parametrize(
    "value",
    ["1", "true", "TRUE", "yes", "on", " 1 "],
)
def test_flag_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Standard truthy strings enable the guardrail."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        value,
    )
    assert _foundation_guardrail_enabled() is True


@pytest.mark.parametrize(
    "value",
    ["", "0", "false", "no", "off"],
)
def test_flag_falsy_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Standard falsy strings keep the guardrail disabled."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        value,
    )
    assert _foundation_guardrail_enabled() is False


# ── _apply_foundation_guardrail ──────────────────────────── #


@pytest.mark.asyncio
async def test_guardrail_skipped_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag off ⇒ no state mutation, even when resolver would block."""
    monkeypatch.delenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        raising=False,
    )
    calls: list[tuple[str, str]] = []

    def resolver(message: str, property_id: str) -> bool:
        calls.append((message, property_id))
        return True

    service = _service(resolver=resolver)
    state = _state()
    await service._apply_foundation_guardrail(state)
    assert state.requires_pm_approval is False
    assert state.response_flags.send_status is True
    # Resolver must not even be called when the flag is off.
    assert calls == []


@pytest.mark.asyncio
async def test_guardrail_skipped_when_resolver_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No resolver injected ⇒ no state mutation."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        "1",
    )
    service = _service(resolver=None)
    state = _state()
    await service._apply_foundation_guardrail(state)
    assert state.requires_pm_approval is False


@pytest.mark.asyncio
async def test_guardrail_skipped_when_message_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty / whitespace-only message bypasses the resolver."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        "1",
    )
    calls: list[tuple[str, str]] = []

    def resolver(message: str, property_id: str) -> bool:
        calls.append((message, property_id))
        return True

    service = _service(resolver=resolver)
    state = _state(message="   ")
    await service._apply_foundation_guardrail(state)
    assert state.requires_pm_approval is False
    assert calls == []


@pytest.mark.asyncio
async def test_guardrail_resolver_false_leaves_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver returns ``False`` ⇒ no state mutation."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        "1",
    )

    def resolver(message: str, property_id: str) -> bool:
        del message, property_id
        return False

    service = _service(resolver=resolver)
    state = _state()
    await service._apply_foundation_guardrail(state)
    assert state.requires_pm_approval is False
    assert state.response_flags.send_status is True


@pytest.mark.asyncio
async def test_guardrail_resolver_true_forces_approval_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver returns ``True`` ⇒ state matches approval mode."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        "1",
    )

    def resolver(message: str, property_id: str) -> bool:
        del message, property_id
        return True

    service = _service(resolver=resolver)
    state = _state()
    await service._apply_foundation_guardrail(state)
    assert state.requires_pm_approval is True
    assert state.response_flags.is_need_attention is True
    assert state.response_flags.send_status is False


@pytest.mark.asyncio
async def test_guardrail_supports_async_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An async resolver is awaited transparently."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        "1",
    )

    async def resolver(message: str, property_id: str) -> bool:
        del message, property_id
        return True

    service = _service(resolver=resolver)
    state = _state()
    await service._apply_foundation_guardrail(state)
    assert state.requires_pm_approval is True


@pytest.mark.asyncio
async def test_guardrail_swallows_resolver_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising resolver logs + falls through — state untouched."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        "1",
    )

    def resolver(message: str, property_id: str) -> bool:
        del message, property_id
        raise RuntimeError("simulated resolver failure")

    service = _service(resolver=resolver)
    state = _state()
    await service._apply_foundation_guardrail(state)
    # Must not raise; state remains untouched.
    assert state.requires_pm_approval is False
    assert state.response_flags.send_status is True


@pytest.mark.asyncio
async def test_guardrail_resolver_receives_message_and_property(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver is called with ``(message_text, property_id)``."""
    monkeypatch.setenv(
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        "1",
    )
    captured: list[tuple[str, str]] = []

    def resolver(message: str, property_id: str) -> bool:
        captured.append((message, property_id))
        return False

    service = _service(resolver=resolver)
    state = _state(
        message="I smell gas in the bedroom",
        property_id="villa-azul",
    )
    await service._apply_foundation_guardrail(state)
    assert captured == [
        ("I smell gas in the bedroom", "villa-azul"),
    ]
