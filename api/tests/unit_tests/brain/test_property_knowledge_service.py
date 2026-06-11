"""Bi-temporal Property Knowledge wiring — decision clock, as_of threading, Path-1 filter (CEN-27).

The T6 hook is verified against its source here (same idiom as
``test_gate_wiring_service.test_enumeration_is_verified_against_touchpoints``)
so the marked block and this suite cannot drift apart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.rag.entities.metadata_entities import MetadataFilteringCondition
from services.brain_decision_clock import (
    KERNEL_KNOWLEDGE_ENDPOINT_ENV,
    decision_time_rfc3339,
    get_decision_time,
    inject_as_of,
    is_kernel_knowledge_endpoint,
    kernel_knowledge_endpoint,
    reset_decision_time,
    set_decision_time,
)
from services.brain_property_knowledge_service import (
    OPEN_ENDED_VALID_TO,
    VALID_FROM_FIELD,
    VALID_TO_FIELD,
    validity_window_conditions,
)

_API_ROOT = Path(__file__).resolve().parents[3]
KERNEL = "http://brain-kernel.internal/v1/brain/knowledge"
EVENT_TS = datetime(2026, 6, 11, 9, 14, 3, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> None:
    monkeypatch.delenv(KERNEL_KNOWLEDGE_ENDPOINT_ENV, raising=False)


@pytest.fixture
def _clock():
    token = set_decision_time(EVENT_TS)
    yield
    reset_decision_time(token)


# ── decision clock ───────────────────────────────────────────────── #


def test_clock_unset_by_default():
    assert get_decision_time() is None
    assert decision_time_rfc3339() is None


def test_clock_set_get_reset_roundtrip():
    token = set_decision_time(EVENT_TS)
    try:
        assert get_decision_time() == EVENT_TS
        assert decision_time_rfc3339() == "2026-06-11T09:14:03Z"
    finally:
        reset_decision_time(token)
    assert get_decision_time() is None


def test_clock_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        set_decision_time(datetime(2026, 6, 11, 9, 14, 3))


def test_clock_normalizes_to_utc():
    eastern = timezone(timedelta(hours=-4))
    token = set_decision_time(EVENT_TS.astimezone(eastern))
    try:
        assert decision_time_rfc3339() == "2026-06-11T09:14:03Z"
    finally:
        reset_decision_time(token)


# ── kernel endpoint guard ────────────────────────────────────────── #


def test_kernel_endpoint_unconfigured_means_no_match():
    assert kernel_knowledge_endpoint() is None
    assert not is_kernel_knowledge_endpoint(KERNEL)


def test_kernel_endpoint_match_normalizes_trailing_slash(monkeypatch):
    monkeypatch.setenv(KERNEL_KNOWLEDGE_ENDPOINT_ENV, KERNEL + "/")
    assert kernel_knowledge_endpoint() == KERNEL
    assert is_kernel_knowledge_endpoint(KERNEL)
    assert is_kernel_knowledge_endpoint(KERNEL + "/")
    assert not is_kernel_knowledge_endpoint("https://thirdparty.example.com/api")
    assert not is_kernel_knowledge_endpoint(None)


# ── as_of injection matrix ───────────────────────────────────────── #


def _params() -> dict:
    return {"query": "quiet hours", "knowledge_id": "kb-1"}


def test_inject_noop_without_clock(monkeypatch):
    monkeypatch.setenv(KERNEL_KNOWLEDGE_ENDPOINT_ENV, KERNEL)
    params = _params()
    inject_as_of(params, KERNEL)
    assert params == _params()


@pytest.mark.usefixtures("_clock")
def test_inject_noop_for_foreign_endpoint(monkeypatch):
    monkeypatch.setenv(KERNEL_KNOWLEDGE_ENDPOINT_ENV, KERNEL)
    params = _params()
    inject_as_of(params, "https://thirdparty.example.com/api")
    assert params == _params()


@pytest.mark.usefixtures("_clock")
def test_inject_noop_when_endpoint_unconfigured():
    params = _params()
    inject_as_of(params, KERNEL)
    assert params == _params()


@pytest.mark.usefixtures("_clock")
def test_inject_adds_as_of_for_kernel_endpoint(monkeypatch):
    monkeypatch.setenv(KERNEL_KNOWLEDGE_ENDPOINT_ENV, KERNEL)
    params = _params()
    inject_as_of(params, KERNEL)
    assert params["as_of"] == "2026-06-11T09:14:03Z"
    assert params["query"] == "quiet hours"


# ── Path-1 manual-mode validity filter (table-stakes) ────────────── #


def test_validity_conditions_validate_against_upstream_entity():
    payload = validity_window_conditions(EVENT_TS)
    condition = MetadataFilteringCondition.model_validate(payload)
    assert condition.logical_operator == "and"
    assert condition.conditions is not None
    by_name = {c.name: c for c in condition.conditions}
    assert by_name[VALID_FROM_FIELD].comparison_operator == "before"
    assert by_name[VALID_TO_FIELD].comparison_operator == "after"
    assert by_name[VALID_FROM_FIELD].value == EVENT_TS.timestamp()


def test_validity_conditions_pass_variable_templates_verbatim():
    payload = validity_window_conditions("{{#start.event_ts#}}")
    assert all(c["value"] == "{{#start.event_ts#}}" for c in payload["conditions"])


def test_validity_conditions_default_to_now():
    before = datetime.now(tz=UTC).timestamp()
    payload = validity_window_conditions()
    after = datetime.now(tz=UTC).timestamp()
    value = payload["conditions"][0]["value"]
    assert before <= value <= after


def test_open_ended_sentinel_is_far_future():
    assert datetime(2200, 1, 1, tzinfo=UTC).timestamp() < OPEN_ENDED_VALID_TO


# ── T6 hook source verification ──────────────────────────────────── #


def test_t6_hook_present_in_external_knowledge_service():
    """The marked block must live inside fetch_external_knowledge_retrieval."""
    source = (_API_ROOT / "services" / "external_knowledge_service.py").read_text()
    fetch_body = source.split("def fetch_external_knowledge_retrieval", 1)[1]
    assert "CENDRA-HOOK(T6)" in fetch_body
    assert 'inject_as_of(request_params, settings.get("endpoint"))' in fetch_body
    # exactly one marker in the file, matching the FORK_LEDGER T6 count
    assert source.count("CENDRA-HOOK") == 1
