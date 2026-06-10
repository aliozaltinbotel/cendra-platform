"""Tests for tools/seed_fake_conversations.py.

ES interactions are mocked. Integration tests against a real cluster
(Painless scripts, update_by_query) are deferred — see spec §13.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add brainengine/ to sys.path so `import tools.seed_fake_conversations` works
# regardless of where pytest is invoked from.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_module_imports_without_error():
    """Smoke test: the seeder module imports cleanly."""
    import tools.seed_fake_conversations  # noqa: F401


from datetime import datetime, timezone


def _dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_stage_offsets_cover_every_booking_stage():
    """STAGE_GUEST_OFFSETS must contain every value of brain_engine.patterns.models.BookingStage.

    Adding a new stage upstream forces a sibling addition here. Drift guard.
    """
    from brain_engine.patterns.models import BookingStage
    from tools.seed_fake_conversations import STAGE_GUEST_OFFSETS

    for stage in BookingStage:
        assert stage.value in STAGE_GUEST_OFFSETS, (
            f"Stage {stage.value!r} is in BookingStage but not in STAGE_GUEST_OFFSETS"
        )


def test_pre_arrival_default_is_six_days_before_checkin():
    from tools.seed_fake_conversations import compute_message_times

    ci = _dt(2026, 4, 14, 15, 0)
    co = _dt(2026, 4, 17, 11, 0)
    guest_at, _ = compute_message_times("pre_arrival", ci, co)
    assert guest_at == _dt(2026, 4, 8, 15, 0)


def test_in_stay_default_is_30pct_into_stay():
    from tools.seed_fake_conversations import compute_message_times

    ci = _dt(2026, 4, 14, 15, 0)
    co = _dt(2026, 4, 17, 11, 0)
    # Stay duration = 2 days 20h = 68h. 30% = 20.4h. ci + 20:24h = 04-15 11:24.
    guest_at, _ = compute_message_times("in_stay", ci, co)
    assert guest_at == _dt(2026, 4, 15, 11, 24)


def test_post_checkout_is_two_days_after():
    from tools.seed_fake_conversations import compute_message_times

    ci = _dt(2026, 4, 14, 15, 0)
    co = _dt(2026, 4, 17, 11, 0)
    guest_at, _ = compute_message_times("post_checkout", ci, co)
    assert guest_at == _dt(2026, 4, 19, 11, 0)


def test_pm_reply_is_five_minutes_after_guest():
    from tools.seed_fake_conversations import compute_message_times

    ci = _dt(2026, 4, 14, 15, 0)
    co = _dt(2026, 4, 17, 11, 0)
    guest_at, pm_at = compute_message_times("in_stay", ci, co)
    assert (pm_at - guest_at).total_seconds() == 5 * 60


def test_guest_offset_hours_overrides_default():
    from tools.seed_fake_conversations import compute_message_times

    ci = _dt(2026, 4, 14, 15, 0)
    co = _dt(2026, 4, 17, 11, 0)
    # Even though stage default for pre_arrival is -144h, override wins.
    guest_at, _ = compute_message_times(
        "pre_arrival", ci, co, guest_offset_hours=-72
    )
    assert guest_at == _dt(2026, 4, 11, 15, 0)


def test_build_guest_item_uses_guest_sender_and_carries_markers():
    from tools.seed_fake_conversations import build_seed_item

    parent_id = "11111111-1111-1111-1111-111111111111"
    batch_id = "22222222-2222-2222-2222-222222222222"
    created_at = _dt(2026, 4, 8, 15, 0)

    item = build_seed_item(
        parent_conversation_id=parent_id,
        body="kapı kodu nedir",
        sender="guest",
        created_at=created_at,
        batch_id=batch_id,
        stage="pre_arrival",
    )

    assert item["sender"] == "guest"
    assert item["body"] == "kapı kodu nedir"
    assert item["messageId"] == parent_id
    assert item["createdAt"] == "2026-04-08T15:00:00Z"
    assert item["modifiedAt"] == "2026-04-08T15:00:00Z"
    assert item["isFakeSeed"] is True
    assert item["seedBatchId"] == batch_id
    assert item["seedScenarioStage"] == "pre_arrival"
    # `id` and `pmsId` are generated; just assert shape.
    assert isinstance(item["id"], str) and len(item["id"]) == 36
    assert item["pmsId"].startswith("fake-seed-")
    assert item["sendByAI"] is False
    assert item["messageType"] == "text"
    assert item["communicationType"] == "chat"


def test_build_pm_item_uses_property_sender():
    from tools.seed_fake_conversations import build_seed_item

    item = build_seed_item(
        parent_conversation_id="p",
        body="checkin tarihinden iki gun once paylasırım",
        sender="property",
        created_at=_dt(2026, 4, 8, 15, 5),
        batch_id="b",
        stage="pre_arrival",
    )
    assert item["sender"] == "property"
    assert item["createdAt"] == "2026-04-08T15:05:00Z"


def test_build_seed_item_rejects_unknown_sender():
    import pytest
    from tools.seed_fake_conversations import build_seed_item

    with pytest.raises(ValueError, match="sender"):
        build_seed_item(
            parent_conversation_id="p",
            body="x",
            sender="host",  # not in canonical set
            created_at=_dt(2026, 4, 8, 15, 0),
            batch_id="b",
            stage="in_stay",
        )


def test_format_iso_z_normalizes_non_utc_offset_to_utc():
    """Non-UTC tz-aware input must be converted to UTC before the Z suffix is added."""
    from datetime import timedelta as _td, timezone as _tz
    from tools.seed_fake_conversations import _format_iso_z, build_seed_item

    # 2026-04-08 15:00 in +03:00 = 12:00Z
    plus_three = datetime(2026, 4, 8, 15, 0, tzinfo=_tz(_td(hours=3)))
    assert _format_iso_z(plus_three) == "2026-04-08T12:00:00Z"

    # build_seed_item must propagate the normalized timestamp.
    item = build_seed_item(
        parent_conversation_id="p",
        body="x",
        sender="guest",
        created_at=plus_three,
        batch_id="b",
        stage="pre_arrival",
    )
    assert item["createdAt"] == "2026-04-08T12:00:00Z"
    assert item["modifiedAt"] == "2026-04-08T12:00:00Z"


import pytest


def _stub_inputs(monkeypatch, answers: list[str]) -> None:
    """Wire `builtins.input` to consume answers in order."""
    iterator = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kw: next(iterator))


# prompt_property_id ──────────────────────────────────────────────────────


def test_prompt_property_id_accepts_first_valid_input(monkeypatch, capsys):
    from tools.seed_fake_conversations import prompt_property_id

    _stub_inputs(monkeypatch, ["323133"])
    assert prompt_property_id() == "323133"


def test_prompt_property_id_rejects_empty_then_accepts(monkeypatch, capsys):
    from tools.seed_fake_conversations import prompt_property_id

    _stub_inputs(monkeypatch, ["", "  ", "323133"])
    assert prompt_property_id() == "323133"


def test_prompt_property_id_exits_after_three_failures(monkeypatch):
    from tools.seed_fake_conversations import prompt_property_id, PromptAbort

    _stub_inputs(monkeypatch, ["", "", ""])
    with pytest.raises(PromptAbort):
        prompt_property_id()


# prompt_total_count ──────────────────────────────────────────────────────


def test_prompt_total_count_accepts_value_in_range(monkeypatch):
    from tools.seed_fake_conversations import prompt_total_count

    _stub_inputs(monkeypatch, ["10"])
    assert prompt_total_count(eligible=11) == 10


def test_prompt_total_count_rejects_above_eligible(monkeypatch):
    from tools.seed_fake_conversations import prompt_total_count

    _stub_inputs(monkeypatch, ["12", "5"])
    assert prompt_total_count(eligible=11) == 5


def test_prompt_total_count_rejects_zero_or_negative(monkeypatch):
    from tools.seed_fake_conversations import prompt_total_count

    _stub_inputs(monkeypatch, ["0", "-3", "1"])
    assert prompt_total_count(eligible=11) == 1


# prompt_stage_distribution ───────────────────────────────────────────────


def test_prompt_stage_distribution_parses_simple_pair(monkeypatch):
    from tools.seed_fake_conversations import prompt_stage_distribution

    _stub_inputs(monkeypatch, ["pre_arrival=2, in_stay=8"])
    assert prompt_stage_distribution(total=10) == {"pre_arrival": 2, "in_stay": 8}


def test_prompt_stage_distribution_rejects_unknown_stage(monkeypatch):
    from tools.seed_fake_conversations import prompt_stage_distribution

    _stub_inputs(monkeypatch, ["pre-arrival=2, in_stay=8", "pre_arrival=2, in_stay=8"])
    assert prompt_stage_distribution(total=10) == {"pre_arrival": 2, "in_stay": 8}


def test_prompt_stage_distribution_rejects_sum_mismatch(monkeypatch):
    from tools.seed_fake_conversations import prompt_stage_distribution

    _stub_inputs(monkeypatch, ["pre_arrival=2, in_stay=7", "pre_arrival=2, in_stay=8"])
    assert prompt_stage_distribution(total=10) == {"pre_arrival": 2, "in_stay": 8}


def test_prompt_stage_distribution_rejects_garbled_input(monkeypatch):
    from tools.seed_fake_conversations import prompt_stage_distribution

    _stub_inputs(monkeypatch, ["pre_arrival 2 in_stay 8", "pre_arrival=2,in_stay=8"])
    assert prompt_stage_distribution(total=10) == {"pre_arrival": 2, "in_stay": 8}


def test_prompt_stage_distribution_rejects_empty_input(monkeypatch):
    """Blank input must be rejected; valid distribution accepted on retry."""
    from tools.seed_fake_conversations import prompt_stage_distribution

    _stub_inputs(monkeypatch, ["", "   ", "pre_arrival=2, in_stay=8"])
    assert prompt_stage_distribution(total=10) == {"pre_arrival": 2, "in_stay": 8}


# prompt_selection_mode ───────────────────────────────────────────────────


def test_prompt_selection_mode_maps_numeric_choice(monkeypatch):
    from tools.seed_fake_conversations import prompt_selection_mode

    _stub_inputs(monkeypatch, ["2"])
    assert prompt_selection_mode() == "most-messages"


def test_prompt_selection_mode_rejects_invalid(monkeypatch):
    from tools.seed_fake_conversations import prompt_selection_mode

    _stub_inputs(monkeypatch, ["0", "9", "1"])
    assert prompt_selection_mode() == "random"


# prompt_message_pair ─────────────────────────────────────────────────────


def test_prompt_message_pair_collects_both_lines(monkeypatch):
    from tools.seed_fake_conversations import prompt_message_pair

    _stub_inputs(monkeypatch, ["kapı kodu nedir", "checkin tarihinden iki gun once"])
    guest, pm = prompt_message_pair("pre_arrival", count=2)
    assert guest == "kapı kodu nedir"
    assert pm == "checkin tarihinden iki gun once"


def test_prompt_message_pair_rejects_empty(monkeypatch):
    from tools.seed_fake_conversations import prompt_message_pair

    _stub_inputs(monkeypatch, ["", "kapı kodu nedir", "", "PM cevap"])
    guest, pm = prompt_message_pair("pre_arrival", count=2)
    assert guest == "kapı kodu nedir"
    assert pm == "PM cevap"


# prompt_confirm ──────────────────────────────────────────────────────────


def test_prompt_confirm_accepts_y(monkeypatch):
    from tools.seed_fake_conversations import prompt_confirm

    _stub_inputs(monkeypatch, ["y"])
    assert prompt_confirm("Devam?") is True


def test_prompt_confirm_accepts_n(monkeypatch):
    from tools.seed_fake_conversations import prompt_confirm

    _stub_inputs(monkeypatch, ["n"])
    assert prompt_confirm("Devam?") is False


def test_prompt_confirm_rejects_other(monkeypatch):
    from tools.seed_fake_conversations import prompt_confirm

    _stub_inputs(monkeypatch, ["maybe", "yes please", "y"])
    assert prompt_confirm("Devam?") is True


from unittest.mock import MagicMock


def _hit(doc_id: str, *, msg_count: int, last: str, ci: str, co: str) -> dict:
    return {
        "_id": doc_id,
        "_source": {
            "id": doc_id,
            "data": {
                "messageCount": msg_count,
                "lastMessageAt": last,
                "propertyChannelId": "323133",
                "booking": {"checkInDate": ci, "checkOutDate": co},
            },
        },
    }


def test_find_conversations_for_property_passes_term_filter():
    from tools.seed_fake_conversations import find_conversations_for_property

    es = MagicMock()
    es.search.return_value = {"hits": {"hits": [
        _hit("a", msg_count=10, last="2026-04-15T13:00:00Z",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
    ]}}
    out = find_conversations_for_property(es, "unified_conversations", "323133")
    assert len(out) == 1
    body = es.search.call_args.kwargs["body"]
    assert body["query"]["term"]["data.propertyChannelId"] == "323133"


def test_extract_booking_dates_returns_utc_datetimes():
    from tools.seed_fake_conversations import extract_booking_dates

    hit = _hit("x", msg_count=1, last="2026-04-15T00:00:00Z",
               ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z")
    ci, co = extract_booking_dates(hit)
    assert ci == _dt(2026, 4, 14, 15, 0)
    assert co == _dt(2026, 4, 17, 11, 0)


def test_extract_booking_dates_returns_none_when_missing():
    from tools.seed_fake_conversations import extract_booking_dates

    hit = {"_id": "x", "_source": {"data": {"messageCount": 1}}}
    assert extract_booking_dates(hit) is None


def test_extract_booking_dates_returns_none_when_unparseable():
    from tools.seed_fake_conversations import extract_booking_dates

    hit = _hit("x", msg_count=1, last="-",
               ci="not-a-date", co="2026-04-17T11:00:00Z")
    assert extract_booking_dates(hit) is None


def test_filter_eligible_drops_invalid_dates():
    from tools.seed_fake_conversations import filter_eligible

    candidates = [
        _hit("good", msg_count=1, last="-",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
        _hit("bad", msg_count=1, last="-", ci="bad", co="bad"),
    ]
    out = filter_eligible(candidates)
    assert [c["_id"] for c in out] == ["good"]


def test_pick_candidates_most_messages_sorts_desc():
    from tools.seed_fake_conversations import pick_candidates

    candidates = [
        _hit("low",  msg_count=2,  last="2026-04-01T00:00:00Z",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
        _hit("high", msg_count=20, last="2026-03-01T00:00:00Z",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
    ]
    out = pick_candidates(candidates, mode="most-messages", n=2)
    assert [c["_id"] for c in out] == ["high", "low"]


def test_pick_candidates_most_recent_sorts_desc():
    from tools.seed_fake_conversations import pick_candidates

    candidates = [
        _hit("old", msg_count=2, last="2026-03-01T00:00:00Z",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
        _hit("new", msg_count=2, last="2026-05-01T00:00:00Z",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
    ]
    out = pick_candidates(candidates, mode="most-recent", n=2)
    assert [c["_id"] for c in out] == ["new", "old"]


def test_pick_candidates_random_takes_n_with_no_replacement():
    from tools.seed_fake_conversations import pick_candidates

    candidates = [
        _hit(f"id{i}", msg_count=i, last="2026-04-01T00:00:00Z",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z")
        for i in range(10)
    ]
    out = pick_candidates(candidates, mode="random", n=3, rng_seed=42)
    assert len(out) == 3
    assert len({c["_id"] for c in out}) == 3  # no replacement


def test_pick_candidates_raises_when_n_exceeds_pool():
    from tools.seed_fake_conversations import pick_candidates

    with pytest.raises(ValueError, match="3 requested but only 2 eligible"):
        pick_candidates([_hit("a", msg_count=1, last="-",
                              ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
                         _hit("b", msg_count=1, last="-",
                              ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z")],
                        mode="random", n=3)


def test_painless_append_script_is_constant():
    from tools.seed_fake_conversations import APPEND_SCRIPT

    assert "ctx._source.data.messages.addAll(params.items)" in APPEND_SCRIPT
    assert "messageCount" in APPEND_SCRIPT
    assert "lastMessageAt" in APPEND_SCRIPT


def test_seed_orchestrator_calls_update_for_each_planned_conversation(monkeypatch):
    """Wire prompts through the orchestrator and assert ES.update is called per conversation."""
    from tools.seed_fake_conversations import run_seed

    es = MagicMock()
    candidates = [
        _hit("c1", msg_count=8, last="2026-04-01T00:00:00Z",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
        _hit("c2", msg_count=14, last="2026-04-02T00:00:00Z",
             ci="2026-05-22T15:00:00Z", co="2026-05-25T11:00:00Z"),
    ]
    es.search.return_value = {"hits": {"hits": candidates}}

    # Stub all six prompts in order.
    answers = iter([
        "323133",                       # property
        "2",                             # total
        "pre_arrival=2",                 # distribution
        "1",                             # selection: random
        "kapı kodu nedir",               # guest
        "checkin tarihinden iki gun once",  # pm
        "y",                             # confirm
    ])
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(answers))

    rc = run_seed(es=es, index="unified_conversations", dry_run=False, rng_seed=0)
    assert rc == 0
    assert es.update.call_count == 2

    # Both calls write to unified_conversations
    for call in es.update.call_args_list:
        assert call.kwargs["index"] == "unified_conversations"
        script = call.kwargs["script"]
        assert "addAll" in script["source"]
        items = script["params"]["items"]
        assert len(items) == 2  # guest + pm
        assert items[0]["sender"] == "guest"
        assert items[1]["sender"] == "property"
        # Markers consistent within one update call
        assert items[0]["seedBatchId"] == items[1]["seedBatchId"]


def test_seed_dry_run_does_not_call_update(monkeypatch):
    from tools.seed_fake_conversations import run_seed

    es = MagicMock()
    es.search.return_value = {"hits": {"hits": [
        _hit("c1", msg_count=8, last="-",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
    ]}}
    answers = iter([
        "323133", "1", "pre_arrival=1", "1",
        "guest msg", "pm msg", "y",
    ])
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(answers))

    rc = run_seed(es=es, index="unified_conversations", dry_run=True, rng_seed=0)
    assert rc == 0
    assert es.update.call_count == 0


def test_seed_aborts_on_no_confirm(monkeypatch):
    from tools.seed_fake_conversations import run_seed

    es = MagicMock()
    es.search.return_value = {"hits": {"hits": [
        _hit("c1", msg_count=8, last="-",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
    ]}}
    answers = iter([
        "323133", "1", "pre_arrival=1", "1",
        "g", "p", "n",
    ])
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(answers))

    rc = run_seed(es=es, index="unified_conversations", dry_run=False, rng_seed=0)
    assert rc == 0
    assert es.update.call_count == 0


def test_remove_batch_script_uses_param_batch_id():
    from tools.seed_fake_conversations import REMOVE_BATCH_SCRIPT, REMOVE_ALL_FAKE_SCRIPT

    assert "removeIf(m -> m.seedBatchId == params.batch_id)" in REMOVE_BATCH_SCRIPT
    assert "removeIf(m -> m.isFakeSeed == true)" in REMOVE_ALL_FAKE_SCRIPT


def test_run_cleanup_by_batch_id_targets_only_marked_docs():
    """run_cleanup scans _source for the batch_id marker and updates only the matching docs."""
    from tools.seed_fake_conversations import run_cleanup, REMOVE_BATCH_SCRIPT

    es = MagicMock()
    # Two pages: first returns three docs (one with our batch, two unrelated),
    # second returns empty (signals end of scroll).
    matched_messages = [
        {"sender": "guest", "body": "x", "seedBatchId": "B1", "isFakeSeed": True},
        {"sender": "property", "body": "y", "seedBatchId": "B1", "isFakeSeed": True},
    ]
    page1 = {
        "hits": {
            "hits": [
                {"_id": "match", "_routing": "cust",
                 "_source": {"data": {"messages": matched_messages}},
                 "sort": [1]},
                {"_id": "no-marker", "_routing": "cust",
                 "_source": {"data": {"messages": [{"sender": "guest", "body": "z"}]}},
                 "sort": [2]},
                {"_id": "different-batch", "_routing": "cust",
                 "_source": {"data": {"messages": [{"sender": "guest", "body": "w", "seedBatchId": "B2"}]}},
                 "sort": [3]},
            ]
        }
    }
    page2 = {"hits": {"hits": []}}
    es.search.side_effect = [page1, page2]

    rc = run_cleanup(
        es=es, index="unified_conversations",
        batch_id="B1", remove_all=False, dry_run=False,
    )
    assert rc == 0
    # Exactly one targeted update — only the doc carrying batch_id=B1.
    assert es.update.call_count == 1
    call = es.update.call_args
    assert call.kwargs["index"] == "unified_conversations"
    assert call.kwargs["id"] == "match"
    assert call.kwargs["routing"] == "cust"
    assert call.kwargs["script"]["source"] == REMOVE_BATCH_SCRIPT
    assert call.kwargs["script"]["params"] == {"batch_id": "B1"}
    # update_by_query is no longer used.
    assert es.update_by_query.call_count == 0


def test_run_cleanup_all_targets_every_marked_doc():
    """remove_all matches anything with isFakeSeed=true, regardless of batch."""
    from tools.seed_fake_conversations import run_cleanup, REMOVE_ALL_FAKE_SCRIPT

    es = MagicMock()
    page1 = {
        "hits": {
            "hits": [
                {"_id": "a", "_routing": "ra",
                 "_source": {"data": {"messages": [{"isFakeSeed": True, "seedBatchId": "B1"}]}},
                 "sort": [1]},
                {"_id": "b", "_routing": "rb",
                 "_source": {"data": {"messages": [{"isFakeSeed": True, "seedBatchId": "B2"}]}},
                 "sort": [2]},
                {"_id": "c", "_routing": "rc",
                 "_source": {"data": {"messages": [{"sender": "guest"}]}},  # no marker
                 "sort": [3]},
            ]
        }
    }
    page2 = {"hits": {"hits": []}}
    es.search.side_effect = [page1, page2]

    rc = run_cleanup(
        es=es, index="unified_conversations",
        batch_id=None, remove_all=True, dry_run=False,
    )
    assert rc == 0
    # Both marked docs targeted, the unmarked one skipped.
    assert es.update.call_count == 2
    targeted_ids = {c.kwargs["id"] for c in es.update.call_args_list}
    assert targeted_ids == {"a", "b"}
    for call in es.update.call_args_list:
        assert call.kwargs["script"]["source"] == REMOVE_ALL_FAKE_SCRIPT
    assert es.update_by_query.call_count == 0


def test_run_cleanup_dry_run_skips_update():
    """Dry-run scans _source but never calls update."""
    from tools.seed_fake_conversations import run_cleanup

    es = MagicMock()
    page1 = {
        "hits": {
            "hits": [
                {"_id": "match", "_routing": "cust",
                 "_source": {"data": {"messages": [{"isFakeSeed": True, "seedBatchId": "B1"}]}},
                 "sort": [1]},
            ]
        }
    }
    page2 = {"hits": {"hits": []}}
    es.search.side_effect = [page1, page2]

    rc = run_cleanup(
        es=es, index="unified_conversations",
        batch_id="B1", remove_all=False, dry_run=True,
    )
    assert rc == 0
    # Dry-run still scans, but does not write.
    assert es.update.call_count == 0
    assert es.update_by_query.call_count == 0


def test_run_list_batches_aggregates_by_seed_batch_id():
    from tools.seed_fake_conversations import run_list_batches
    from unittest.mock import MagicMock

    es = MagicMock()
    es.search.return_value = {
        "aggregations": {
            "messages_nested": {
                "by_batch": {
                    "buckets": [
                        {
                            "key": "batch-A",
                            "doc_count": 8,
                            "stages": {"buckets": [
                                {"key": "pre_arrival", "doc_count": 4},
                                {"key": "in_stay", "doc_count": 4},
                            ]},
                            "earliest": {"value_as_string": "2026-05-04T11:08:00Z"},
                        },
                        {
                            "key": "batch-B",
                            "doc_count": 4,
                            "stages": {"buckets": [
                                {"key": "in_stay", "doc_count": 4},
                            ]},
                            "earliest": {"value_as_string": "2026-05-05T14:23:00Z"},
                        },
                    ]
                }
            }
        }
    }
    rc = run_list_batches(es=es, index="unified_conversations", dry_run=False)
    assert rc == 0
    assert es.search.call_count == 1


def test_run_list_batches_handles_empty_aggregation():
    from tools.seed_fake_conversations import run_list_batches
    from unittest.mock import MagicMock

    es = MagicMock()
    es.search.return_value = {
        "aggregations": {
            "messages_nested": {"by_batch": {"buckets": []}}
        }
    }
    rc = run_list_batches(es=es, index="unified_conversations", dry_run=False)
    assert rc == 0


def test_seed_summary_prints_item_and_conversation_count(monkeypatch, capsys):
    """run_seed must print '<N> mesaj × 2 = <2N> item, <N> conversation'a eklendi.' on success."""
    from tools.seed_fake_conversations import run_seed

    es = MagicMock()
    es.search.return_value = {"hits": {"hits": [
        _hit("c1", msg_count=8, last="-",
             ci="2026-04-14T15:00:00Z", co="2026-04-17T11:00:00Z"),
        _hit("c2", msg_count=14, last="-",
             ci="2026-05-22T15:00:00Z", co="2026-05-25T11:00:00Z"),
    ]}}
    answers = iter([
        "323133", "2", "pre_arrival=2", "1",
        "kapı kodu nedir", "checkin tarihinden iki gun once", "y",
    ])
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(answers))

    rc = run_seed(es=es, index="unified_conversations", dry_run=False, rng_seed=0)
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 mesaj × 2 (Guest + PM) = 4 item" in out
    assert "2 conversation'a eklendi" in out


def test_run_list_batches_dry_run_prints_acknowledgement(monkeypatch, capsys):
    from tools.seed_fake_conversations import run_list_batches

    es = MagicMock()
    es.search.return_value = {
        "aggregations": {
            "messages_nested": {"by_batch": {"buckets": []}}
        }
    }
    rc = run_list_batches(es=es, index="unified_conversations", dry_run=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out


def test_run_show_batch_lists_matching_items_with_full_doc_id(capsys):
    """show-batch prints full doc ids and per-item details for the requested batch."""
    from tools.seed_fake_conversations import run_show_batch

    es = MagicMock()
    matched = [
        {"sender": "guest",    "body": "kapı kodu nedir",
         "createdAt": "2025-12-25T15:00:00Z",
         "seedBatchId": "B1", "isFakeSeed": True, "seedScenarioStage": "pre_arrival"},
        {"sender": "property", "body": "checkin tarihinden iki gun once",
         "createdAt": "2025-12-25T15:05:00Z",
         "seedBatchId": "B1", "isFakeSeed": True, "seedScenarioStage": "pre_arrival"},
    ]
    other_batch = [
        {"sender": "guest", "body": "z",
         "createdAt": "2026-01-01T00:00:00Z",
         "seedBatchId": "B2", "isFakeSeed": True, "seedScenarioStage": "in_stay"},
    ]
    page1 = {
        "hits": {"hits": [
            {"_id": "doc-with-batch-1",
             "_source": {"data": {"messages": matched,
                                  "propertyChannelId": "323133",
                                  "title": "Conversation A"}},
             "sort": [1]},
            {"_id": "doc-with-other-batch",
             "_source": {"data": {"messages": other_batch,
                                  "propertyChannelId": "414215",
                                  "title": "Conversation B"}},
             "sort": [2]},
        ]}
    }
    page2 = {"hits": {"hits": []}}
    es.search.side_effect = [page1, page2]

    rc = run_show_batch(es=es, index="unified_conversations", batch_id="B1", dry_run=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "doc-with-batch-1" in out
    assert "doc-with-other-batch" not in out
    assert "323133" in out
    assert "pre_arrival" in out
    assert "kapı kodu nedir" in out
    assert "checkin tarihinden iki gun once" in out
    assert "2 items in 1 conversation(s)" in out


def test_run_show_batch_handles_no_matches(capsys):
    from tools.seed_fake_conversations import run_show_batch

    es = MagicMock()
    page1 = {
        "hits": {"hits": [
            {"_id": "doc-x",
             "_source": {"data": {"messages": [{"sender": "guest", "body": "no marker"}],
                                  "propertyChannelId": "111", "title": "x"}},
             "sort": [1]},
        ]}
    }
    page2 = {"hits": {"hits": []}}
    es.search.side_effect = [page1, page2]

    rc = run_show_batch(es=es, index="unified_conversations", batch_id="ghost", dry_run=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No items found for batch ghost" in out
