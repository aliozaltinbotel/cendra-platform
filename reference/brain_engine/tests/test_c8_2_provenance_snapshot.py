"""Unit tests for ``api_server.server._c8_2_provenance_snapshot``.

The helper bundles the three reservation-context sources (UI,
GraphQL, post-merge) plus a tail slice of assistant history into
a JSON string so a single diagnostic log line captures the full
provenance of the value forwarded to the conversation pipeline.
These tests pin the shape of that JSON so a future refactor cannot
silently drop a field the C8.2 (Sandbox UI 2026-05-19) probe
relies on.
"""

from __future__ import annotations

import json

from api_server.server import _c8_2_provenance_snapshot
from brain_engine.conversation.models import (
    ConversationMessage,
    ReservationContext,
    SenderType,
)


def _ctx(**overrides: object) -> ReservationContext:
    """Build a ``ReservationContext`` with the test defaults."""
    base: dict[str, object] = {
        "status": "confirmed",
        "check_in": "2026-05-14",
        "check_out": "2026-05-18",
        "guest_name": "Test Guest",
        "num_guests": 2,
    }
    base.update(overrides)
    return ReservationContext(**base)


def test_snapshot_includes_all_three_contexts() -> None:
    ui = _ctx(check_in="2026-05-14")
    graphql = _ctx(check_in="2026-05-15")
    merged = _ctx(check_in="2026-05-14")

    payload = json.loads(
        _c8_2_provenance_snapshot(
            ui=ui,
            graphql=graphql,
            merged=merged,
            history=[],
        ),
    )

    assert payload["ui"]["check_in"] == "2026-05-14"
    assert payload["graphql"]["check_in"] == "2026-05-15"
    assert payload["merged"]["check_in"] == "2026-05-14"


def test_snapshot_serialises_none_sources_as_null() -> None:
    payload = json.loads(
        _c8_2_provenance_snapshot(
            ui=None,
            graphql=None,
            merged=None,
            history=[],
        ),
    )

    assert payload["ui"] is None
    assert payload["graphql"] is None
    assert payload["merged"] is None
    assert payload["history_total"] == 0
    assert payload["recent_bot_texts"] == []


def test_snapshot_recent_bot_texts_filters_and_caps() -> None:
    history = [
        ConversationMessage(text="hi", sender_type=SenderType.GUEST),
        ConversationMessage(text="hello", sender_type=SenderType.BOT),
        ConversationMessage(text="ok", sender_type=SenderType.GUEST),
        ConversationMessage(text="", sender_type=SenderType.BOT),
        ConversationMessage(text="bot-1", sender_type=SenderType.BOT),
        ConversationMessage(text="bot-2", sender_type=SenderType.BOT),
        ConversationMessage(text="guest-msg", sender_type=SenderType.GUEST),
        ConversationMessage(text="bot-3", sender_type=SenderType.BOT),
        ConversationMessage(text="bot-4", sender_type=SenderType.BOT),
    ]

    payload = json.loads(
        _c8_2_provenance_snapshot(
            ui=None,
            graphql=None,
            merged=None,
            history=history,
        ),
    )

    assert payload["history_total"] == len(history)
    assert payload["recent_bot_texts"] == ["bot-2", "bot-3", "bot-4"]


def test_snapshot_is_single_line_json() -> None:
    payload_str = _c8_2_provenance_snapshot(
        ui=_ctx(),
        graphql=_ctx(),
        merged=_ctx(),
        history=[
            ConversationMessage(text="x", sender_type=SenderType.BOT),
        ],
    )

    assert "\n" not in payload_str
    assert json.loads(payload_str)["history_total"] == 1
