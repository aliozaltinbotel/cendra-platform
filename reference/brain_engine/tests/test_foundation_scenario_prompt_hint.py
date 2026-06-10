"""Tests for foundation-scenario → LLM-prompt wiring (R7).

The orchestrator (``ConversationService._run_foundation_analysis``)
matches every incoming message against the 469-row FL-01 catalog
mined from ``FOUNDATION_469_SCENARIOS.xlsx`` and
``Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md``.
When a candidate clears the similarity floor the result is written
to ``state.foundation_analysis.foundation_match.dominant_catalog_entry``.

Pre-R7 that entry was consumed by telemetry / DecisionCase
logging / SSE side effects only — the LLM never saw the matched
scenario, so the Sandbox UI reply looked generic even when the
Postman ``/foundation/analyze`` endpoint surfaced a confident
match.

This module pins the new wiring:

* :func:`_format_foundation_scenario_hint` renders the matched
  scenario as a Markdown ``## Matched Foundation Scenario`` block
  the LLM treats as authoritative.
* ``ConversationService._assemble_prompt`` splices the block into
  the system prompt next to the operational-policy block (R1).
* Every "scenario absent" branch (analysis ``None`` / empty match
  / no catalog entry) collapses to ``""`` so the prompt stays
  byte-identical for events the catalog cannot place.
"""

from __future__ import annotations

import pytest

from brain_engine.analysis.models import (
    AnalysisResult,
    FoundationMatch,
    FoundationMatchCandidate,
)
from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
)
from brain_engine.conversation.service import (
    ConversationService,
    _format_foundation_scenario_hint,
)
from brain_engine.customer.models import CustomerSettings
from brain_engine.patterns.foundation_registry import FoundationScenario
from brain_engine.patterns.models import PatternOrigin


def _scenario(**overrides: object) -> FoundationScenario:
    """Build a FoundationScenario with sensible defaults."""
    base: dict[str, object] = {
        "scenario_id": "s1_001_late_checkout",
        "title": "Late Check-out Enquiry (Pre-booking)",
        "stage_number": 1,
        "stage_label": "Pre-Booking / Inquiry",
        "trigger": "guest asks about late check-out",
        "ai_default_behavior": (
            "Share standard checkout time; never promise late "
            "checkout without confirmation."
        ),
        "should_auto_reply": "Conditional",
        "should_escalate_to_pm": "No",
        "required_data_checks": ("check_out_time", "cleaning_schedule"),
        "what_not_to_learn": (
            "Never promise free late checkout from a single approved case."
        ),
    }
    base.update(overrides)
    return FoundationScenario(**base)  # type: ignore[arg-type]


def _analysis(entry: FoundationScenario | None) -> AnalysisResult:
    """Wrap ``entry`` in the AnalysisResult shape the orchestrator emits."""
    if entry is None:
        match = FoundationMatch()
    else:
        match = FoundationMatch(
            candidates=(
                FoundationMatchCandidate(
                    scenario_id=entry.scenario_id,
                    similarity=0.92,
                    catalog_entry=entry,
                ),
            ),
            dominant_scenario_id=entry.scenario_id,
            dominant_catalog_entry=entry,
        )
    return AnalysisResult(
        event_id="e1",
        foundation_match=match,
        origin=PatternOrigin(),
    )


# -- renderer: empty branches -------------------------------------------


def test_format_foundation_scenario_hint_none_analysis() -> None:
    """Orchestrator unwired / disabled → empty string so the prompt
    stays byte-identical for legacy deployments."""
    assert _format_foundation_scenario_hint(None) == ""


def test_format_foundation_scenario_hint_empty_match() -> None:
    """The matcher ran but found no candidate above the similarity
    floor — the prompt must NOT carry an empty header."""
    analysis = AnalysisResult(
        event_id="e1",
        foundation_match=FoundationMatch(),
        origin=PatternOrigin(),
    )
    assert _format_foundation_scenario_hint(analysis) == ""


def test_format_foundation_scenario_hint_no_catalog_entry() -> None:
    """The matcher had candidates but the catalog lookup returned no
    scenario (Q5-A similarity-gate trip) — still skip the block."""
    match = FoundationMatch(
        candidates=(
            FoundationMatchCandidate(
                scenario_id="s1_001_late_checkout",
                similarity=0.92,
            ),
        ),
        dominant_scenario_id="s1_001_late_checkout",
        dominant_catalog_entry=None,
    )
    analysis = AnalysisResult(
        event_id="e1",
        foundation_match=match,
        origin=PatternOrigin(),
    )
    assert _format_foundation_scenario_hint(analysis) == ""


# -- renderer: rich match -----------------------------------------------


def test_format_foundation_scenario_hint_renders_identity_and_policy() -> None:
    """A confident match must surface title + id + stage label +
    AI default behaviour + auto-reply policy + escalation rule."""
    rendered = _format_foundation_scenario_hint(_analysis(_scenario()))

    assert rendered.startswith("## Matched Foundation Scenario")
    assert "Late Check-out Enquiry (Pre-booking)" in rendered
    assert "``s1_001_late_checkout``" in rendered
    assert "Pre-Booking / Inquiry" in rendered
    assert "AI Default Behavior" in rendered
    assert "Auto-reply policy" in rendered
    assert "Conditional" in rendered
    assert "Escalate to PM" in rendered


def test_format_foundation_scenario_hint_renders_required_data() -> None:
    """Required data checks must surface as bullets so the LLM
    knows to defer when the data is physically missing."""
    rendered = _format_foundation_scenario_hint(_analysis(_scenario()))
    assert "Required Data Checks" in rendered
    assert "  - check_out_time" in rendered
    assert "  - cleaning_schedule" in rendered


def test_format_foundation_scenario_hint_renders_safety_note() -> None:
    """When ``what_not_to_learn`` is set the LLM must see it as
    a hard prohibition on committing to that promise."""
    rendered = _format_foundation_scenario_hint(_analysis(_scenario()))
    assert "Safety — what NOT to commit to" in rendered
    assert "Never promise free late checkout" in rendered


def test_format_foundation_scenario_hint_skips_blank_optional_fields() -> None:
    """A minimal scenario (no behaviour / no required data / no
    safety note) must render only the identity line — no empty
    section headers."""
    minimal = _scenario(
        ai_default_behavior="",
        should_auto_reply="",
        should_escalate_to_pm="",
        should_create_task="",
        required_data_checks=(),
        what_not_to_learn="",
    )
    rendered = _format_foundation_scenario_hint(_analysis(minimal))

    assert "AI Default Behavior" not in rendered
    assert "Auto-reply policy" not in rendered
    assert "Escalate to PM" not in rendered
    assert "Required Data Checks" not in rendered
    assert "Safety" not in rendered
    # Identity line still present.
    assert "Late Check-out Enquiry" in rendered


# -- integration with _assemble_prompt ----------------------------------


def _state_with_analysis(analysis: AnalysisResult | None) -> PipelineState:
    request = ConversationRequest(customer_id="C1", property_id="P1")
    state = PipelineState(request=request)
    if analysis is not None:
        state.foundation_analysis = analysis
    return state


def test_assemble_prompt_includes_scenario_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: when ``state.foundation_analysis`` carries a
    confident match the assembled system prompt must contain the
    scenario hint header AND the matched title."""
    svc = ConversationService.__new__(ConversationService)
    state = _state_with_analysis(_analysis(_scenario()))
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert "## Matched Foundation Scenario" in out.system_prompt
    assert "Late Check-out Enquiry (Pre-booking)" in out.system_prompt
    assert "Conditional" in out.system_prompt  # auto-reply policy


def test_assemble_prompt_skips_block_when_analysis_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No analysis on the state → no scenario block in the prompt
    so legacy deployments stay byte-identical."""
    svc = ConversationService.__new__(ConversationService)
    state = _state_with_analysis(None)
    settings = CustomerSettings(customer_id="C1")

    out = svc._assemble_prompt(state, settings)

    assert "## Matched Foundation Scenario" not in out.system_prompt
