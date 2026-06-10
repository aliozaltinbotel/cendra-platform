"""Tests for the ``/api/admin/foundation/{status,analyze}`` router.

Pins:

* ``GET /status`` returns 200 with the right shape under three
  states: fully wired (orchestrator + catalogue present), partial
  (no orchestrator), and missing markdown file on disk.
* ``POST /analyze`` synthesises an :class:`AnalysisEvent`, runs the
  injected orchestrator, and projects the result to a stable JSON
  shape (candidates, dominant scenario, decisions, origin, flags).
* ``POST /analyze`` returns 503 when no orchestrator is wired and
  500 when the orchestrator raises — neither path crashes the
  worker.
* Environment flag projection mirrors the runtime helper —
  truthy / falsy parsing rules match
  :func:`_foundation_orchestrator_enabled` in the service module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_server.routers.foundation_audit import (
    configure_deps,
    router,
)
from brain_engine.analysis.models import (
    AnalysisEvent,
    AnalysisResult,
    FoundationMatch,
    FoundationMatchCandidate,
)
from brain_engine.patterns.foundation_registry import FoundationScenario
from brain_engine.patterns.models import PatternOrigin

# ── stubs ─────────────────────────────────────────────────── #


def _scenario(
    *,
    scenario_id: str = "s4_209_gas_smell",
    stage_number: int = 4,
    should_auto_reply: str = "No",
    should_learn_pattern: str = "No",
    memory_types: tuple[str, ...] = ("Property knowledge",),
) -> FoundationScenario:
    """Build a minimal :class:`FoundationScenario` for projection tests."""
    return FoundationScenario(
        scenario_id=scenario_id,
        title="Gas smell reported",
        stage_number=stage_number,
        stage_label="In-stay",
        trigger="guest reports gas",
        risk_level="Critical",
        should_auto_reply=should_auto_reply,
        should_learn_pattern=should_learn_pattern,
        memory_types=memory_types,
    )


class _RecordingOrchestrator:
    """Recording stub that mimics the orchestrator's analyse contract."""

    def __init__(
        self,
        *,
        result: AnalysisResult | None = None,
    ) -> None:
        self.calls: list[AnalysisEvent] = []
        self._result = result

    async def analyze(self, event: AnalysisEvent) -> AnalysisResult:
        self.calls.append(event)
        if self._result is not None:
            return self._result
        return AnalysisResult(
            event_id=event.event_id,
            foundation_match=FoundationMatch(),
            origin=PatternOrigin(
                foundation_scenario_ids=(),
                source_event_ids=(event.event_id,),
                contributing_signal_ids=(),
            ),
        )


class _RaisingOrchestrator:
    """Always raises — used to verify the 500 path."""

    async def analyze(self, event: AnalysisEvent) -> AnalysisResult:
        del event
        raise RuntimeError("simulated orchestrator failure")


def _client(deps: dict[str, object]) -> TestClient:
    """Build a FastAPI client with the audit router and injected deps."""
    configure_deps(deps)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── isolation_fixture ──────────────────────────────────────── #


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear flag env vars + reset router deps between tests."""
    for name in (
        "BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED",
        "BRAIN_FOUNDATION_GUARDRAIL_ENABLED",
        "BRAIN_FOUNDATION_LEARN_GATE_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    configure_deps(
        {
            "orchestrator": None,
            "scenarios_count": 0,
            "foundation_path": "",
            "case_store": None,
            "rule_store": None,
        },
    )


# ── GET /status ───────────────────────────────────────────── #


def test_status_reports_ready_when_orchestrator_and_catalog_wired(
    tmp_path: Path,
) -> None:
    """A fully wired pod returns ready=true with all blocks populated."""
    md = tmp_path / "foundation.md"
    md.write_bytes(b"# stub MD payload\n")
    deps = {
        "orchestrator": _make_real_orchestrator(),
        "scenarios_count": 469,
        "foundation_path": str(md),
    }
    client = _client(deps)
    res = client.get("/api/admin/foundation/status")
    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is True
    assert body["catalog"] == {"loaded": True, "scenarios_count": 469}
    assert body["orchestrator"] == {"wired": True}
    assert body["markdown"]["present"] is True
    assert body["markdown"]["size_bytes"] == md.stat().st_size
    assert set(body["flags"].keys()) == {
        "orchestrator_enabled",
        "guardrail_enabled",
        "learn_gate_enabled",
    }


def test_status_marks_not_ready_when_orchestrator_missing(
    tmp_path: Path,
) -> None:
    """Orchestrator None ⇒ ready=false even when MD is present."""
    md = tmp_path / "foundation.md"
    md.write_bytes(b"# stub MD payload\n")
    client = _client(
        {
            "orchestrator": None,
            "scenarios_count": 469,
            "foundation_path": str(md),
        },
    )
    res = client.get("/api/admin/foundation/status")
    assert res.status_code == 200
    assert res.json()["ready"] is False
    assert res.json()["orchestrator"] == {"wired": False}


def test_status_marks_md_missing_when_file_absent(
    tmp_path: Path,
) -> None:
    """A non-existent MD path returns present=false with the path echoed."""
    md = tmp_path / "nope.md"  # does not exist on disk
    client = _client(
        {
            "orchestrator": _make_real_orchestrator(),
            "scenarios_count": 469,
            "foundation_path": str(md),
        },
    )
    res = client.get("/api/admin/foundation/status")
    body = res.json()
    assert body["markdown"]["present"] is False
    assert body["markdown"]["path"] == str(md)
    assert "size_bytes" not in body["markdown"]


def test_status_flags_reflect_env_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each flag toggles independently based on its env var."""
    md = tmp_path / "foundation.md"
    md.write_bytes(b"x")
    client = _client(
        {
            "orchestrator": _make_real_orchestrator(),
            "scenarios_count": 469,
            "foundation_path": str(md),
        },
    )
    monkeypatch.setenv("BRAIN_FOUNDATION_ORCHESTRATOR_ENABLED", "1")
    monkeypatch.setenv("BRAIN_FOUNDATION_GUARDRAIL_ENABLED", "0")
    monkeypatch.setenv("BRAIN_FOUNDATION_LEARN_GATE_ENABLED", "yes")
    flags = client.get("/api/admin/foundation/status").json()["flags"]
    assert flags["orchestrator_enabled"] is True
    assert flags["guardrail_enabled"] is False
    assert flags["learn_gate_enabled"] is True


# ── POST /analyze ─────────────────────────────────────────── #


def test_analyze_returns_503_without_orchestrator() -> None:
    """No orchestrator wired ⇒ 503 with a structured error body."""
    client = _client(
        {
            "orchestrator": None,
            "scenarios_count": 0,
            "foundation_path": "",
        },
    )
    res = client.post(
        "/api/admin/foundation/analyze",
        json={"text": "anything"},
    )
    assert res.status_code == 503
    assert res.json()["error"] == "foundation_orchestrator_not_wired"


def test_analyze_returns_500_when_orchestrator_raises() -> None:
    """Orchestrator raise ⇒ 500, structured error, no crash."""
    client = _client(
        {
            "orchestrator": _RaisingOrchestrator(),
            "scenarios_count": 469,
            "foundation_path": "",
        },
    )
    res = client.post(
        "/api/admin/foundation/analyze",
        json={"text": "anything"},
    )
    assert res.status_code == 500
    assert res.json()["error"] == "foundation_orchestrator_failed"


def test_analyze_projects_match_decisions_origin() -> None:
    """A successful run returns the full projected JSON shape."""
    scenario = _scenario()
    candidate = FoundationMatchCandidate(
        scenario_id=scenario.scenario_id,
        similarity=0.87,
        catalog_entry=scenario,
    )
    result = AnalysisResult(
        event_id="placeholder",
        foundation_match=FoundationMatch(
            candidates=(candidate,),
            dominant_scenario_id=scenario.scenario_id,
            dominant_catalog_entry=scenario,
        ),
        origin=PatternOrigin(
            foundation_scenario_ids=(scenario.scenario_id,),
            source_event_ids=("placeholder",),
            contributing_signal_ids=(),
        ),
        guardrail_block=True,
        pattern_candidate_emitted=False,
        memory_routes=("property_knowledge", "guest_risk_memory"),
    )
    orchestrator = _RecordingOrchestrator(result=result)
    client = _client(
        {
            "orchestrator": orchestrator,
            "scenarios_count": 469,
            "foundation_path": "",
        },
    )
    res = client.post(
        "/api/admin/foundation/analyze",
        json={
            "text": "Mutfakta gaz kokusu var",
            "property_id": "323133",
            "reservation_id": "res-42",
            "guest_id": "guest-1",
        },
    )
    assert res.status_code == 200
    body = res.json()

    # orchestrator was called with the synthesised event
    assert len(orchestrator.calls) == 1
    sent_event = orchestrator.calls[0]
    assert sent_event.text == "Mutfakta gaz kokusu var"
    assert sent_event.property_id == "323133"
    assert sent_event.reservation_id == "res-42"
    assert sent_event.guest_id == "guest-1"

    # match projection
    assert body["match"]["candidates_count"] == 1
    assert body["match"]["candidates"][0]["scenario_id"] == (
        "s4_209_gas_smell"
    )
    assert body["match"]["candidates"][0]["similarity"] == pytest.approx(
        0.87,
    )
    assert body["match"]["dominant_scenario_id"] == "s4_209_gas_smell"
    assert body["match"]["dominant_catalog_entry"]["risk_level"] == (
        "Critical"
    )
    assert body["match"]["dominant_catalog_entry"][
        "should_auto_reply"
    ] == "No"

    # Full projection mirrors FOUNDATION_469_SCENARIOS.xlsx — the
    # 18 Excel columns plus the two helper fields stage_group +
    # stage that recreate Excel's Stage Group / Stage strings
    # verbatim.
    expected_fields = {
        "scenario_id",
        "title",
        "stage_number",
        "stage_label",
        "stage_group",
        "stage",
        "trigger",
        "signals_to_inspect",
        "risk_level",
        "ai_default_behavior",
        "required_data_checks",
        "should_auto_reply",
        "should_escalate_to_pm",
        "should_create_task",
        "should_learn_pattern",
        "pattern_to_learn",
        "example_learned_pattern",
        "memory_types",
        "what_not_to_learn",
        "future_behavior_impact",
    }
    actual_fields = set(
        body["match"]["dominant_catalog_entry"].keys(),
    )
    assert expected_fields == actual_fields, (
        f"missing: {expected_fields - actual_fields}, "
        f"extra: {actual_fields - expected_fields}"
    )

    # decisions projection
    assert body["decisions"]["guardrail_block"] is True
    assert body["decisions"]["pattern_candidate_emitted"] is False
    assert body["decisions"]["memory_routes"] == [
        "property_knowledge",
        "guest_risk_memory",
    ]

    # origin projection
    assert body["origin"]["foundation_scenario_ids"] == [
        "s4_209_gas_smell",
    ]
    # Stub returned the pre-baked result verbatim (placeholder) — the
    # router does not rewrite ``source_event_ids`` when projecting.
    assert body["origin"]["source_event_ids"] == ["placeholder"]
    assert body["origin"]["contributing_signal_ids"] == []

    # flags block always present
    assert set(body["flags"].keys()) == {
        "orchestrator_enabled",
        "guardrail_enabled",
        "learn_gate_enabled",
    }


def test_analyze_requires_text_field() -> None:
    """An empty body fails Pydantic validation with 422."""
    client = _client(
        {
            "orchestrator": _RecordingOrchestrator(),
            "scenarios_count": 469,
            "foundation_path": "",
        },
    )
    res = client.post("/api/admin/foundation/analyze", json={})
    assert res.status_code == 422


def test_analyze_event_id_is_unique_per_call() -> None:
    """Two consecutive calls produce different event_ids."""
    orchestrator = _RecordingOrchestrator()
    client = _client(
        {
            "orchestrator": orchestrator,
            "scenarios_count": 469,
            "foundation_path": "",
        },
    )
    res1 = client.post(
        "/api/admin/foundation/analyze",
        json={"text": "hi"},
    )
    res2 = client.post(
        "/api/admin/foundation/analyze",
        json={"text": "hi"},
    )
    assert res1.json()["event_id"] != res2.json()["event_id"]


# ── helpers ───────────────────────────────────────────────── #


def _make_real_orchestrator() -> object:
    """Lightweight orchestrator-shaped duck for the status tests."""

    class _StubOrchestrator:
        async def analyze(
            self,
            event: AnalysisEvent,
        ) -> AnalysisResult:
            return AnalysisResult(
                event_id=event.event_id,
                foundation_match=FoundationMatch(),
                origin=PatternOrigin(
                    foundation_scenario_ids=(),
                    source_event_ids=(event.event_id,),
                    contributing_signal_ids=(),
                ),
            )

    # Tag the stub so the router's isinstance check accepts it.  We
    # use isinstance(orchestrator, FoundationAnalysisOrchestrator)
    # only in the analyse path; the status path checks ``is not
    # None`` so a plain duck is fine here.
    return _StubOrchestrator()


# Round-trip the unused datetime import to keep CI silent.
_ = datetime.now(UTC)


# ── GET /coverage ─────────────────────────────────────────── #


class _StubCase:
    """Minimal duck-typed DecisionCase for coverage stats."""

    def __init__(
        self,
        *,
        scenario: str = "general",
        foundation_scenario_id: str | None = None,
    ) -> None:
        self.scenario = scenario
        self.foundation_scenario_id = foundation_scenario_id


class _StubCaseStore:
    """Recording case store with predictable count + search outputs."""

    def __init__(
        self,
        *,
        cases: list[_StubCase],
        total: int | None = None,
    ) -> None:
        self._cases = cases
        self._total = total if total is not None else len(cases)
        self.count_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []

    async def count(self, **kwargs: object) -> int:
        self.count_calls.append(kwargs)
        return self._total

    async def search(self, **kwargs: object) -> list[_StubCase]:
        self.search_calls.append(kwargs)
        limit = int(kwargs.get("limit", len(self._cases)) or len(self._cases))
        return self._cases[:limit]


class _StubRule:
    """Minimal duck-typed PatternRule for the rules block."""

    def __init__(
        self,
        *,
        scope_id: str,
        foundation_scenario_id: str | None,
    ) -> None:
        self.scope_id = scope_id
        self.foundation_scenario_id = foundation_scenario_id


class _StubRuleStore:
    """Rule store stub returning a fixed list of active rules."""

    def __init__(self, rules: list[_StubRule]) -> None:
        self._rules = rules

    async def get_active_rules(self, **_kwargs: object) -> list[_StubRule]:
        return self._rules


def test_coverage_returns_503_without_case_store() -> None:
    """No case_store wired ⇒ 503 with structured error body."""
    client = _client(
        {
            "orchestrator": None,
            "scenarios_count": 0,
            "foundation_path": "",
            "case_store": None,
            "rule_store": None,
        },
    )
    res = client.get(
        "/api/admin/foundation/coverage",
        params={"property_id": "prop-1"},
    )
    assert res.status_code == 503
    assert res.json()["error"] == "case_store_not_wired"


def test_coverage_reports_case_level_ratio() -> None:
    """Sample with 2/3 cases tagged ⇒ 66.67% coverage."""
    case_store = _StubCaseStore(
        cases=[
            _StubCase(foundation_scenario_id="s1_16_early_checkin"),
            _StubCase(foundation_scenario_id="s1_16_early_checkin"),
            _StubCase(foundation_scenario_id=None),
        ],
        total=300,
    )
    client = _client(
        {
            "orchestrator": None,
            "scenarios_count": 469,
            "foundation_path": "",
            "case_store": case_store,
            "rule_store": None,
        },
    )
    res = client.get(
        "/api/admin/foundation/coverage",
        params={"property_id": "prop-1"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["property_id"] == "prop-1"
    assert body["cases"]["total"] == 300
    assert body["cases"]["sample_size"] == 3
    assert body["cases"]["with_foundation_scenario_id"] == 2
    assert body["cases"]["coverage_pct"] == 66.67
    assert body["top_scenarios"] == [
        {"scenario_id": "s1_16_early_checkin", "case_count": 2},
    ]
    assert body["rules"]["available"] is False


def test_coverage_top_n_respects_query_param() -> None:
    """The top_n query param caps the returned scenarios list."""
    case_store = _StubCaseStore(
        cases=[
            _StubCase(foundation_scenario_id=f"s_{i}")
            for i in range(5)
        ],
    )
    client = _client(
        {
            "orchestrator": None,
            "scenarios_count": 469,
            "foundation_path": "",
            "case_store": case_store,
            "rule_store": None,
        },
    )
    res = client.get(
        "/api/admin/foundation/coverage",
        params={"property_id": "prop-1", "top_n": 2},
    )
    assert res.status_code == 200
    assert len(res.json()["top_scenarios"]) == 2


def test_coverage_rules_block_when_store_wired() -> None:
    """rule_store wired ⇒ rules block populated with the right ratio."""
    case_store = _StubCaseStore(cases=[], total=0)
    rule_store = _StubRuleStore(
        rules=[
            _StubRule(
                scope_id="prop-1",
                foundation_scenario_id="s1_16_early_checkin",
            ),
            _StubRule(
                scope_id="prop-1",
                foundation_scenario_id=None,
            ),
            _StubRule(
                scope_id="prop-2",
                foundation_scenario_id="s2_22",
            ),
        ],
    )
    client = _client(
        {
            "orchestrator": None,
            "scenarios_count": 469,
            "foundation_path": "",
            "case_store": case_store,
            "rule_store": rule_store,
        },
    )
    res = client.get(
        "/api/admin/foundation/coverage",
        params={"property_id": "prop-1"},
    )
    body = res.json()
    rules = body["rules"]
    assert rules["available"] is True
    assert rules["total"] == 2  # other property excluded
    assert rules["with_foundation_scenario_id"] == 1
    assert rules["coverage_pct"] == 50.0


def test_coverage_handles_empty_sample() -> None:
    """No cases ⇒ coverage_pct=0.0, top_scenarios=[]"""
    case_store = _StubCaseStore(cases=[], total=0)
    client = _client(
        {
            "orchestrator": None,
            "scenarios_count": 469,
            "foundation_path": "",
            "case_store": case_store,
            "rule_store": None,
        },
    )
    res = client.get(
        "/api/admin/foundation/coverage",
        params={"property_id": "prop-1"},
    )
    body = res.json()
    assert body["cases"]["coverage_pct"] == 0.0
    assert body["top_scenarios"] == []


def test_coverage_search_failure_returns_zero_sample() -> None:
    """Store search exception ⇒ sample_size=0, no crash."""

    class _BoomStore:
        async def count(self, **_kwargs: object) -> int:
            return 100

        async def search(self, **_kwargs: object) -> list[_StubCase]:
            raise RuntimeError("simulated search failure")

    client = _client(
        {
            "orchestrator": None,
            "scenarios_count": 469,
            "foundation_path": "",
            "case_store": _BoomStore(),
            "rule_store": None,
        },
    )
    res = client.get(
        "/api/admin/foundation/coverage",
        params={"property_id": "prop-1"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["cases"]["total"] == 100
    assert body["cases"]["sample_size"] == 0
    assert body["cases"]["with_foundation_scenario_id"] == 0


def test_coverage_requires_property_id() -> None:
    """Missing property_id query param ⇒ 422."""
    client = _client(
        {
            "orchestrator": None,
            "scenarios_count": 0,
            "foundation_path": "",
            "case_store": _StubCaseStore(cases=[]),
            "rule_store": None,
        },
    )
    res = client.get("/api/admin/foundation/coverage")
    assert res.status_code == 422


# ── Excel parity — Stage Group / Stage strings ───────────── #


@pytest.mark.parametrize(
    ("stage_number", "stage_label", "expected_group", "expected_short"),
    [
        (1, "Pre-Booking / Inquiry", "Stage 1 — Pre-Booking / Inquiry", "Pre-booking"),
        (2, "Booking Confirmation", "Stage 2 — Booking Confirmation", "Booking confirmation"),
        (3, "Pre-Arrival", "Stage 3 — Pre-Arrival", "Pre-arrival"),
        (4, "Check-In Day", "Stage 4 — Check-In Day", "Check-in day"),
        (5, "During Stay", "Stage 5 — During Stay", "During stay"),
        (6, "Upsell / Revenue Opportunities", "Stage 6 — Upsell / Revenue Opportunities", "Upsell / revenue"),
        (7, "Check-Out", "Stage 7 — Check-Out", "Check-out"),
        (8, "Post-Stay", "Stage 8 — Post-Stay", "Post-stay"),
        (
            9,
            "Internal Operations / Vendor / Owner Workflows",
            "Stage 9 — Internal Operations / Vendor / Owner Workflows",
            "Internal operations",
        ),
    ],
)
def test_projection_emits_excel_stage_group_and_short(
    stage_number: int,
    stage_label: str,
    expected_group: str,
    expected_short: str,
) -> None:
    """For every stage 1-9 the projection emits Excel-shaped strings."""
    from api_server.routers.foundation_audit import _project_catalog_entry

    class _Entry:
        scenario_id = "s_test"
        title = ""
        trigger = ""
        signals_to_inspect: tuple[str, ...] = ()
        risk_level = ""
        ai_default_behavior = ""
        required_data_checks: tuple[str, ...] = ()
        should_auto_reply = ""
        should_escalate_to_pm = ""
        should_create_task = ""
        should_learn_pattern = ""
        pattern_to_learn = ""
        example_learned_pattern = ""
        memory_types: tuple[str, ...] = ()
        what_not_to_learn = ""
        future_behavior_impact = ""

    entry = _Entry()
    entry.stage_number = stage_number  # type: ignore[attr-defined]
    entry.stage_label = stage_label  # type: ignore[attr-defined]
    result = _project_catalog_entry(entry)
    assert result["stage_group"] == expected_group
    assert result["stage"] == expected_short


def test_projection_stage_group_falls_back_when_stage_number_missing() -> None:
    """No stage_number ⇒ stage_group degrades to bare stage_label."""
    from api_server.routers.foundation_audit import _project_catalog_entry

    class _Entry:
        scenario_id = "s_test"
        title = ""
        trigger = ""
        signals_to_inspect: tuple[str, ...] = ()
        risk_level = ""
        ai_default_behavior = ""
        required_data_checks: tuple[str, ...] = ()
        should_auto_reply = ""
        should_escalate_to_pm = ""
        should_create_task = ""
        should_learn_pattern = ""
        pattern_to_learn = ""
        example_learned_pattern = ""
        memory_types: tuple[str, ...] = ()
        what_not_to_learn = ""
        future_behavior_impact = ""
        stage_number = None
        stage_label = "Pre-Booking / Inquiry"

    result = _project_catalog_entry(_Entry())
    assert result["stage_group"] == "Pre-Booking / Inquiry"
    # ``stage`` short-form falls back to the same label when number
    # is missing (so the field is never empty).
    assert result["stage"] == "Pre-Booking / Inquiry"
