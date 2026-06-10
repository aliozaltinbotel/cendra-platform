"""Tests for the foundation-document parser.

Pins the contract:

* The parser walks ``# Stage N — Label`` + ``## N. Title`` +
  ``### Trigger`` blocks deterministically.
* Each scenario produces a stable, unique ``scenario_id`` slug.
* Scenarios missing ``### Trigger`` are dropped by the loader
  with a WARNING log entry, never silently corrupting the index.
* The live
  ``Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_
  Foundation.md`` parses to ``>= 469`` examples — the count the
  document advertises in Section 3.
* Empty input ⇒ empty output (no raise).
* ``load_foundation_examples`` returns ``()`` for a missing file
  rather than raising, so callers can degrade gracefully.

FL-01 additions extend the contract with the full 14-field
sub-section parser; the test suite below pins each new field, the
bullet-list handling, the multi-label memory routing, and the
hash-based change detection helper.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from brain_engine.patterns.foundation_registry import (
    FoundationScenario,
    compute_doc_hash,
    load_foundation_examples,
    load_foundation_scenarios,
    parse_foundation_document,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FOUNDATION = (
    _REPO_ROOT
    / "Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md"
)


# ── parser unit tests ─────────────────────────────────────── #


def test_parse_minimal_document() -> None:
    """A hand-crafted minimal document parses cleanly."""
    markdown = textwrap.dedent(
        """\
        # Stage 1 — Pre-Booking / Inquiry


        ## 1. First scenario title

        ### Stage
        Pre-booking

        ### Trigger
        First scenario fires when a guest asks about availability.

        ### Risk Level
        Low


        ## 2. Second scenario title

        ### Trigger
        Second scenario fires on payment status.
        """,
    )
    parsed = parse_foundation_document(markdown)
    assert len(parsed) == 2

    first = parsed[0]
    assert first.stage_number == 1
    assert first.stage_label == "Pre-Booking / Inquiry"
    assert first.title == "First scenario title"
    assert "availability" in first.trigger
    # FL-01: Risk Level captured.
    assert first.risk_level == "Low"

    second = parsed[1]
    assert second.title == "Second scenario title"
    assert "payment status" in second.trigger
    # FL-01: Risk Level absent ⇒ empty string, not raise.
    assert second.risk_level == ""


def test_scenario_ids_are_unique_across_stages() -> None:
    """The slug prefix guarantees uniqueness across stages."""
    markdown = textwrap.dedent(
        """\
        # Stage 1 — Pre-Booking / Inquiry


        ## 1. Late inquiry

        ### Trigger
        Stage-1 late inquiry.


        # Stage 5 — During Stay


        ## 1. Late inquiry

        ### Trigger
        Stage-5 late inquiry.
        """,
    )
    parsed = parse_foundation_document(markdown)
    ids = [s.scenario_id for s in parsed]
    assert len(ids) == len(set(ids))
    assert ids[0].startswith("s1_")
    assert ids[1].startswith("s5_")


def test_empty_document_returns_empty_tuple() -> None:
    """Empty input ⇒ empty output (no raise)."""
    assert parse_foundation_document("") == ()


def test_scenario_without_trigger_keeps_empty_trigger() -> None:
    """A scenario block missing ``### Trigger`` keeps ``trigger=''``."""
    markdown = textwrap.dedent(
        """\
        # Stage 1 — Pre-Booking / Inquiry


        ## 1. Some title

        ### Stage
        Pre-booking

        ### Risk Level
        Low
        """,
    )
    parsed = parse_foundation_document(markdown)
    assert len(parsed) == 1
    assert parsed[0].trigger == ""
    # FL-01: Risk Level still captured even though trigger missing.
    assert parsed[0].risk_level == "Low"


def test_scenarios_outside_stage_are_ignored() -> None:
    """``##`` headers before any ``# Stage`` line are skipped."""
    markdown = textwrap.dedent(
        """\
        ## 999. Premature scenario

        ### Trigger
        Should be ignored.


        # Stage 2 — Booking Confirmation


        ## 1. Real scenario

        ### Trigger
        First valid scenario.
        """,
    )
    parsed = parse_foundation_document(markdown)
    assert len(parsed) == 1
    assert parsed[0].title == "Real scenario"


def test_foundation_scenario_validates_stage_number() -> None:
    """``stage_number`` must be in [1, 9]."""
    with pytest.raises(ValueError, match="stage_number"):
        FoundationScenario(
            scenario_id="x",
            title="t",
            stage_number=0,
            stage_label="",
            trigger="",
        )
    with pytest.raises(ValueError, match="stage_number"):
        FoundationScenario(
            scenario_id="x",
            title="t",
            stage_number=10,
            stage_label="",
            trigger="",
        )


def test_foundation_scenario_constructs_with_minimal_args() -> None:
    """Backward compat: the original 5-field constructor still works.

    The matcher test fixtures and several Sprint H/I tests build
    scenarios with the minimum keyword set.  FL-01 must not break
    those by promoting any new field to required.
    """
    scenario = FoundationScenario(
        scenario_id="s1_1_test",
        title="t",
        stage_number=1,
        stage_label="",
        trigger="trigger body",
    )
    assert scenario.risk_level == ""
    assert scenario.required_data_checks == ()
    assert scenario.signals_to_inspect == ()
    assert scenario.memory_types == ()


# ── FL-01: 14-field sub-section parsing ───────────────────── #


def test_parses_all_fourteen_subsections() -> None:
    """Every sub-section maps to the right FoundationScenario field."""
    markdown = textwrap.dedent(
        """\
        # Stage 4 — Check-In Day


        ## 209. Guest reports gas smell

        ### Stage
        Check-in day

        ### Trigger
        Guest reports gas smell via OTA inbox.

        ### Signals to Inspect
        - guest location
        - time of day
        - access method
        - photo/video evidence

        ### Risk Level
        Critical

        ### AI Default Behavior
        Treat as immediate operational incident.

        ### Required Data Checks
        - reservation identity
        - access-code status
        - emergency contacts

        ### Should AI Auto-Reply?
        No

        ### Should AI Escalate to PM?
        Yes

        ### Should AI Create Task?
        Yes

        ### Should AI Learn Pattern?
        No

        ### Pattern to Learn
        No durable pattern by default.

        ### Example Learned Pattern
        Propose scoped rule after 3+ comparable cases.

        ### Memory Type
        - Property knowledge
        - Reservation context memory

        ### What Not to Learn
        Do not generalize emergency handling.

        ### Future Behavior Impact
        Cendra should recognise the incident class faster.
        """,
    )
    parsed = parse_foundation_document(markdown)
    assert len(parsed) == 1

    row = parsed[0]
    assert row.title == "Guest reports gas smell"
    assert row.stage_number == 4
    assert row.risk_level == "Critical"
    assert row.ai_default_behavior == (
        "Treat as immediate operational incident."
    )
    assert row.required_data_checks == (
        "reservation identity",
        "access-code status",
        "emergency contacts",
    )
    assert row.signals_to_inspect == (
        "guest location",
        "time of day",
        "access method",
        "photo/video evidence",
    )
    assert row.should_auto_reply == "No"
    assert row.should_escalate_to_pm == "Yes"
    assert row.should_create_task == "Yes"
    assert row.should_learn_pattern == "No"
    assert row.pattern_to_learn == "No durable pattern by default."
    assert row.example_learned_pattern == (
        "Propose scoped rule after 3+ comparable cases."
    )
    assert row.memory_types == (
        "Property knowledge",
        "Reservation context memory",
    )
    assert row.what_not_to_learn == "Do not generalize emergency handling."
    assert row.future_behavior_impact == (
        "Cendra should recognise the incident class faster."
    )


def test_bullet_lists_skip_non_bullet_lines() -> None:
    """Prose mixed with bullets in a sub-section keeps only bullets."""
    markdown = textwrap.dedent(
        """\
        # Stage 1 — Pre-Booking / Inquiry


        ## 1. Mixed bullets

        ### Trigger
        Triggered.

        ### Signals to Inspect
        Lead paragraph describing the context.
        - first bullet
        - second bullet
        Trailing prose that should be ignored.
        """,
    )
    parsed = parse_foundation_document(markdown)
    assert parsed[0].signals_to_inspect == (
        "first bullet",
        "second bullet",
    )


def test_multi_label_memory_type_preserves_order() -> None:
    """Memory Type list is multi-label and preserves bullet order."""
    markdown = textwrap.dedent(
        """\
        # Stage 5 — During Stay


        ## 7. Multi-tier routing

        ### Trigger
        Multi-tier example.

        ### Memory Type
        - PM preference memory
        - Task workflow memory
        - SOP candidate memory
        """,
    )
    parsed = parse_foundation_document(markdown)
    assert parsed[0].memory_types == (
        "PM preference memory",
        "Task workflow memory",
        "SOP candidate memory",
    )


def test_subsection_without_body_keeps_empty_default() -> None:
    """A heading immediately followed by another heading ⇒ empty value."""
    markdown = textwrap.dedent(
        """\
        # Stage 1 — Pre-Booking / Inquiry


        ## 1. Empty section example

        ### Trigger
        Something.

        ### Risk Level

        ### AI Default Behavior
        Has a body.
        """,
    )
    parsed = parse_foundation_document(markdown)
    assert parsed[0].risk_level == ""
    assert parsed[0].ai_default_behavior == "Has a body."


# ── live document integration ─────────────────────────────── #


@pytest.mark.skipif(
    not _FOUNDATION.is_file(),
    reason="foundation markdown not present in this checkout",
)
def test_live_foundation_parses_at_least_469_scenarios() -> None:
    """The shipped foundation document yields >= 469 examples."""
    examples = load_foundation_examples(_FOUNDATION)
    assert len(examples) >= 469


@pytest.mark.skipif(
    not _FOUNDATION.is_file(),
    reason="foundation markdown not present in this checkout",
)
def test_live_foundation_examples_have_unique_ids() -> None:
    """Every scenario id from the live document is unique."""
    examples = load_foundation_examples(_FOUNDATION)
    ids = [e.scenario_id for e in examples]
    assert len(ids) == len(set(ids))


@pytest.mark.skipif(
    not _FOUNDATION.is_file(),
    reason="foundation markdown not present in this checkout",
)
def test_live_foundation_examples_all_carry_non_empty_text() -> None:
    """The loader drops empty-trigger rows; every survivor has text."""
    examples = load_foundation_examples(_FOUNDATION)
    for example in examples:
        assert example.text
        assert example.text.strip()


@pytest.mark.skipif(
    not _FOUNDATION.is_file(),
    reason="foundation markdown not present in this checkout",
)
def test_live_foundation_full_load_has_fifteen_critical_scenarios() -> None:
    """The 9-stage catalog contains exactly 15 Critical risk scenarios.

    The number is taken from Section 4 *Risk Levels* of the
    foundation document — emergencies like gas smell, flooding,
    medical help, lockout.  A drift here means a sub-section parser
    regression, not a doc change.
    """
    scenarios = load_foundation_scenarios(_FOUNDATION)
    criticals = [s for s in scenarios if s.risk_level == "Critical"]
    assert len(criticals) == 15


@pytest.mark.skipif(
    not _FOUNDATION.is_file(),
    reason="foundation markdown not present in this checkout",
)
def test_live_foundation_safety_scenarios_forbid_pattern_learning() -> None:
    """The six safety-only Critical scenarios carry Learn Pattern = No.

    Not every Critical scenario disables learning — operational
    criticals (smart-lock failure, no electricity, flooding) DO
    learn because the PM accumulates real patterns there.  But the
    six pure-safety scenarios below must always disable learning so
    pattern miners never derive a reusable "answer" for gas, fire,
    medical, or violence incidents.

    The id list is locked verbatim — a doc edit that renames or
    drops one of these scenarios must surface as a test failure so
    the safety guardrail stays explicit.
    """
    safety_only_critical_ids = {
        "s4_209_guest_reports_gas_smell",
        "s4_211_guest_reports_broken_glass_or_injury",
        "s5_241_guest_asks_for_medical_help",
        "s5_242_guest_reports_safety_or_security_concern",
        "s5_295_guest_reports_carbon_monoxide_alarm",
        "s8_412_guest_reports_injury_after_stay",
    }
    scenarios = load_foundation_scenarios(_FOUNDATION)
    by_id = {s.scenario_id: s for s in scenarios}
    missing = safety_only_critical_ids - by_id.keys()
    assert missing == set(), (
        "Foundation document is missing safety scenarios: "
        f"{sorted(missing)}"
    )
    leaks = [
        scenario_id
        for scenario_id in safety_only_critical_ids
        if by_id[scenario_id].should_learn_pattern != "No"
    ]
    assert leaks == [], (
        "Safety-only Critical scenarios must disable pattern "
        f"learning: {leaks}"
    )


@pytest.mark.skipif(
    not _FOUNDATION.is_file(),
    reason="foundation markdown not present in this checkout",
)
def test_live_foundation_memory_types_cover_known_tiers() -> None:
    """Every Memory Type label is one of the catalog-observed tiers.

    Section 8 *Memory Types* names sixteen abstract tiers, but the
    Section 9 catalog also uses ``Operational workflow memory`` —
    a label that belongs to the Stage 9 internal-operations
    workflows even though Section 8 calls it ``Task workflow
    memory`` in the abstract.  The known set below tracks the
    catalog reality, not the abstract list, so a parser regression
    that mangles a label surfaces immediately.

    Tiers from Section 8 that no Section 9 scenario routes into
    (``Compensation / Refund memory``, ``Escalation memory``,
    ``Confirmed SOP memory``, ``Guest preference memory``) do not
    appear in this set — they are reserved for future scenarios
    or for the customer-facing tier (FL-14).
    """
    known_tiers = {
        "Property knowledge",
        "PM preference memory",
        "PM behavior memory",
        "Reservation context memory",
        "Guest profile memory",
        "Guest risk memory",
        "Owner preference memory",
        "Vendor memory",
        "Task workflow memory",
        "Operational workflow memory",
        "Channel-specific behavior memory",
        "Missing-info registry",
        "SOP candidate memory",
    }
    scenarios = load_foundation_scenarios(_FOUNDATION)
    seen: set[str] = set()
    for scenario in scenarios:
        seen.update(scenario.memory_types)
    unexpected = seen - known_tiers
    assert unexpected == set(), (
        f"Unexpected Memory Type labels: {sorted(unexpected)}"
    )
    # Spot-check: at least the three highest-traffic tiers from
    # the quantitative analysis must always be present.
    assert "Guest profile memory" in seen
    assert "Property knowledge" in seen
    assert "PM preference memory" in seen


# ── loader fallback ───────────────────────────────────────── #


def test_load_missing_file_returns_empty_tuple(
    tmp_path: Path,
) -> None:
    """A non-existent path returns ``()`` instead of raising."""
    missing = tmp_path / "does_not_exist.md"
    assert load_foundation_examples(missing) == ()


def test_load_with_string_path(tmp_path: Path) -> None:
    """The loader accepts both ``Path`` and ``str`` inputs."""
    md = tmp_path / "tiny.md"
    md.write_text(
        textwrap.dedent(
            """\
            # Stage 1 — Pre-Booking / Inquiry

            ## 1. Tiny scenario

            ### Trigger
            Single sample.
            """,
        ),
        encoding="utf-8",
    )
    examples_path = load_foundation_examples(md)
    examples_str = load_foundation_examples(str(md))
    assert len(examples_path) == len(examples_str) == 1
    assert examples_path[0].scenario_id == examples_str[0].scenario_id


def test_load_scenarios_returns_full_objects(
    tmp_path: Path,
) -> None:
    """``load_foundation_scenarios`` returns the rich 14-field rows."""
    md = tmp_path / "rich.md"
    md.write_text(
        textwrap.dedent(
            """\
            # Stage 1 — Pre-Booking / Inquiry

            ## 1. Rich scenario

            ### Trigger
            Body.

            ### Risk Level
            Medium

            ### Memory Type
            - Guest profile memory
            """,
        ),
        encoding="utf-8",
    )
    scenarios = load_foundation_scenarios(md)
    assert len(scenarios) == 1
    assert scenarios[0].risk_level == "Medium"
    assert scenarios[0].memory_types == ("Guest profile memory",)


def test_load_scenarios_missing_file_returns_empty(
    tmp_path: Path,
) -> None:
    """``load_foundation_scenarios`` degrades gracefully too."""
    assert load_foundation_scenarios(tmp_path / "nope.md") == ()


# ── hash helper ───────────────────────────────────────────── #


def test_compute_doc_hash_changes_with_content(
    tmp_path: Path,
) -> None:
    """SHA-256 differs when the markdown changes."""
    md = tmp_path / "doc.md"
    md.write_text("first content", encoding="utf-8")
    first = compute_doc_hash(md)
    md.write_text("second content", encoding="utf-8")
    second = compute_doc_hash(md)
    assert first is not None
    assert second is not None
    assert first != second


def test_compute_doc_hash_stable_for_unchanged_file(
    tmp_path: Path,
) -> None:
    """Repeated hashing of the same file yields the same digest."""
    md = tmp_path / "doc.md"
    md.write_text("payload", encoding="utf-8")
    assert compute_doc_hash(md) == compute_doc_hash(md)


def test_compute_doc_hash_missing_file_returns_none(
    tmp_path: Path,
) -> None:
    """No file ⇒ ``None`` (consistent with the loader fallback)."""
    assert compute_doc_hash(tmp_path / "absent.md") is None
