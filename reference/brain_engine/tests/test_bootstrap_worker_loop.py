"""Tests for the bootstrap worker's drain loop and settlement routing.

:func:`workers.bootstrap_worker._drain` is the SDK-free heart of the
receive loop.  These tests run it against a fake receiver + fake
handler to prove it: settles each message by the handler's verdict,
stops when the queue goes idle, and respects the per-pass message cap
— all without touching Azure.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from workers.bootstrap_message_handler import Settlement
from workers.bootstrap_worker import _drain, _drain_loop, _settle

pytestmark = pytest.mark.asyncio


class _Msg:
    """Minimal received-message stand-in; ``str(msg)`` is the body."""

    def __init__(self, body: str) -> None:
        self._body = body

    def __str__(self) -> str:
        return self._body


class _FakeReceiver:
    """Yields preset batches, records every settlement call."""

    def __init__(self, batches: list[list[_Msg]]) -> None:
        self._batches = list(batches)
        self.completed: list[str] = []
        self.abandoned: list[str] = []
        self.dead_lettered: list[tuple[str, str]] = []

    async def receive_messages(
        self,
        *,
        max_message_count: int,
        max_wait_time: int,
    ) -> list[_Msg]:
        if self._batches:
            return self._batches.pop(0)
        return []

    async def complete_message(self, message: _Msg) -> None:
        self.completed.append(str(message))

    async def abandon_message(self, message: _Msg) -> None:
        self.abandoned.append(str(message))

    async def dead_letter_message(
        self,
        message: _Msg,
        *,
        reason: str,
        error_description: str,
    ) -> None:
        self.dead_lettered.append((str(message), reason))


class _FakeHandler:
    """Maps a message body to a fixed settlement."""

    def __init__(self, verdicts: dict[str, Settlement]) -> None:
        self._verdicts = verdicts
        self.seen: list[str] = []

    async def handle(self, body: str) -> Settlement:
        self.seen.append(body)
        return self._verdicts.get(body, Settlement.COMPLETE)


def _drain_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "max_messages": 50,
        "receive_batch": 10,
        "max_wait_seconds": 1,
        "max_idle_receives": 1,
    }
    args.update(overrides)
    return args


async def test_drain_completes_batch_then_stops_on_idle() -> None:
    receiver = _FakeReceiver([[_Msg("a"), _Msg("b")]])
    handler = _FakeHandler({})  # default COMPLETE
    tally = await _drain(
        receiver,
        cast(Any, handler),
        **_drain_args(),
    )
    assert receiver.completed == ["a", "b"]
    assert tally["complete"] == 2
    assert handler.seen == ["a", "b"]


async def test_drain_routes_each_settlement() -> None:
    receiver = _FakeReceiver([[_Msg("ok"), _Msg("poison"), _Msg("infra")]])
    handler = _FakeHandler(
        {
            "ok": Settlement.COMPLETE,
            "poison": Settlement.DEAD_LETTER,
            "infra": Settlement.ABANDON,
        },
    )
    tally = await _drain(receiver, cast(Any, handler), **_drain_args())
    assert receiver.completed == ["ok"]
    assert receiver.abandoned == ["infra"]
    assert receiver.dead_lettered == [("poison", "bootstrap_unprocessable")]
    assert tally == {"complete": 1, "abandon": 1, "dead_letter": 1}


async def test_drain_respects_max_messages_cap() -> None:
    receiver = _FakeReceiver([[_Msg("a"), _Msg("b"), _Msg("c")]])
    handler = _FakeHandler({})
    tally = await _drain(
        receiver,
        cast(Any, handler),
        **_drain_args(max_messages=2),
    )
    assert tally["complete"] == 2
    assert receiver.completed == ["a", "b"]


async def test_drain_stops_after_idle_threshold() -> None:
    # No batches at all → first receive is empty → idle threshold of 1
    # ends the pass immediately with a zero tally.
    receiver = _FakeReceiver([])
    handler = _FakeHandler({})
    tally = await _drain(receiver, cast(Any, handler), **_drain_args())
    assert tally == {"complete": 0, "abandon": 0, "dead_letter": 0}
    assert handler.seen == []


async def test_drain_keeps_draining_across_empty_then_full() -> None:
    # idle threshold 2: one empty receive does not stop the pass, the
    # following non-empty batch is still processed.
    receiver = _FakeReceiver([[], [_Msg("late")]])
    handler = _FakeHandler({})
    tally = await _drain(
        receiver,
        cast(Any, handler),
        **_drain_args(max_idle_receives=2),
    )
    assert receiver.completed == ["late"]
    assert tally["complete"] == 1


async def test_settle_dead_letter_passes_reason() -> None:
    receiver = _FakeReceiver([])
    msg = _Msg("x")
    await _settle(receiver, msg, Settlement.DEAD_LETTER)
    assert receiver.dead_lettered == [("x", "bootstrap_unprocessable")]


# ── _drain_loop: continuous (Deployment) vs one-shot (ScaledJob) ──


class _StoppingReceiver(_FakeReceiver):
    """Sets ``stop`` once the queue drains — models a SIGTERM arriving
    after the backlog is processed, so the continuous loop terminates.
    """

    def __init__(self, batches: list[list[_Msg]], stop: asyncio.Event) -> None:
        super().__init__(batches)
        self._stop = stop

    async def receive_messages(
        self,
        *,
        max_message_count: int,
        max_wait_time: int,
    ) -> list[_Msg]:
        messages = await super().receive_messages(
            max_message_count=max_message_count,
            max_wait_time=max_wait_time,
        )
        if not messages:
            self._stop.set()
        return messages


async def test_drain_loop_single_pass_when_not_continuous() -> None:
    # continuous=False drains the queue once and returns — even with a
    # never-set stop event it must not hang.
    receiver = _FakeReceiver([[_Msg("a"), _Msg("b")]])
    handler = _FakeHandler({})
    tally = await _drain_loop(
        receiver,
        cast(Any, handler),
        continuous=False,
        stop=asyncio.Event(),
        **_drain_args(),
    )
    assert tally["complete"] == 2
    assert receiver.completed == ["a", "b"]


async def test_drain_loop_continuous_runs_until_stop() -> None:
    # continuous=True keeps looping; the receiver sets stop once the
    # backlog drains, so the loop accumulates the work then exits.
    stop = asyncio.Event()
    receiver = _StoppingReceiver([[_Msg("a")], [_Msg("b")]], stop)
    handler = _FakeHandler({})
    tally = await _drain_loop(
        receiver,
        cast(Any, handler),
        continuous=True,
        stop=stop,
        **_drain_args(),
    )
    assert stop.is_set()
    assert tally["complete"] == 2
    assert receiver.completed == ["a", "b"]


async def test_drain_loop_respects_preset_stop() -> None:
    # An already-set stop short-circuits before any receive.
    receiver = _FakeReceiver([[_Msg("a")]])
    handler = _FakeHandler({})
    stop = asyncio.Event()
    stop.set()
    tally = await _drain_loop(
        receiver,
        cast(Any, handler),
        continuous=True,
        stop=stop,
        **_drain_args(),
    )
    assert tally == {"complete": 0, "abandon": 0, "dead_letter": 0}
    assert receiver.completed == []
