"""Regression test for ``BaseChatModel.invoke()`` profile access.

``invoke()`` records cost + metrics through ``self.model_profile``
after the provider call returns.  Two stale references to a
non-existent ``self.profile`` attribute made every ``invoke()`` raise
``AttributeError`` *after* the model had already answered — the reply
was obtained and then silently dropped.  In the bootstrap pipeline
this surfaced as repeated ``bootstrap.sandbox_empty_reply`` warnings.

This guards the correct attribute name so the metrics path never
discards a valid response again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from brain_engine.models.base import BaseChatModel
from brain_engine.models.messages import AIMessage


class _StubModel(BaseChatModel):
    """Minimal concrete model whose provider call always succeeds."""

    async def _do_invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AIMessage:
        return AIMessage(content="ok")

    async def _do_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        yield "ok"


def test_model_profile_property_resolves() -> None:
    model = _StubModel(model="gpt-4o-mini", provider="azure_openai")
    # The attribute the metrics path reads must exist (the bug used a
    # stale ``self.profile`` that no model ever defined).
    assert model.model_profile is not None
    assert not hasattr(model, "profile")


async def test_invoke_returns_response_through_metrics_path() -> None:
    model = _StubModel(model="gpt-4o-mini", provider="azure_openai")
    # Pre-fix this raised AttributeError on ``self.profile`` *after*
    # ``_do_invoke`` had already produced the answer.
    resp = await model.invoke([{"role": "user", "content": "hi"}])
    assert resp.content == "ok"
