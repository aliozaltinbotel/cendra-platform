"""Tests for the FL-15 iterative questioning helper.

Pins:

* :func:`build_clarifying_questions` invariants — only emits a
  question when the PM description does NOT cover the Required
  Data Check; ``top_k`` and ``questions_per_scenario`` caps; empty
  scenarios / blank description; case-insensitive matching;
  deterministic order; positional cap rejects zero / negative.
* :func:`render_question_prompt` formats the prompt block, and
  returns an empty string for an empty iterable.
"""

from __future__ import annotations

import pytest

from brain_engine.analysis import (
    DEFAULT_QUESTIONS_PER_SCENARIO,
    DEFAULT_TOP_K,
    IterativeQuestion,
    build_clarifying_questions,
    render_question_prompt,
)
from brain_engine.patterns.foundation_registry import FoundationScenario

# ── fixtures ──────────────────────────────────────────────── #


def _scenario(
    *,
    scenario_id: str = "s1_16_guest_asks_for_early_check_in",
    title: str = "Guest asks for early check-in before booking",
    required_data_checks: tuple[str, ...] = (),
) -> FoundationScenario:
    """Build a minimal :class:`FoundationScenario` for the helper."""
    return FoundationScenario(
        scenario_id=scenario_id,
        title=title,
        stage_number=1,
        stage_label="Pre-Booking / Inquiry",
        trigger="trigger body",
        required_data_checks=required_data_checks,
    )


# ── input validation ──────────────────────────────────────── #


def test_top_k_must_be_positive() -> None:
    """``top_k`` ≤ 0 raises."""
    with pytest.raises(ValueError, match="top_k"):
        build_clarifying_questions("anything", [], top_k=0)


def test_questions_per_scenario_must_be_positive() -> None:
    """``questions_per_scenario`` ≤ 0 raises."""
    with pytest.raises(ValueError, match="questions_per_scenario"):
        build_clarifying_questions(
            "anything",
            [],
            questions_per_scenario=0,
        )


def test_empty_scenarios_returns_empty_tuple() -> None:
    """No scenarios ⇒ no questions."""
    assert build_clarifying_questions("anything", []) == ()


# ── coverage detection ────────────────────────────────────── #


def test_check_covered_by_description_suppresses_question() -> None:
    """When the PM mentions the check, no question is emitted."""
    scenario = _scenario(
        required_data_checks=("cleaner ETA",),
    )
    questions = build_clarifying_questions(
        "Allow early check-in when cleaner ETA is before 13:00",
        [scenario],
    )
    assert questions == ()


def test_check_not_covered_emits_question() -> None:
    """A missed check produces exactly one ``IterativeQuestion``."""
    scenario = _scenario(
        required_data_checks=("same-day turnover",),
    )
    questions = build_clarifying_questions(
        "Allow early check-in when cleaner finishes",
        [scenario],
    )
    assert len(questions) == 1
    question = questions[0]
    assert question.scenario_id == (
        "s1_16_guest_asks_for_early_check_in"
    )
    assert question.missed_check == "same-day turnover"
    assert "same-day turnover" in question.question


def test_matching_is_case_insensitive() -> None:
    """Capitalisation differences do not split coverage detection."""
    scenario = _scenario(
        required_data_checks=("Cleaner ETA",),
    )
    questions = build_clarifying_questions(
        "ALLOW EARLY CHECK-IN WHEN CLEANER ETA IS READY",
        [scenario],
    )
    assert questions == ()


def test_paraphrased_mention_counts_as_coverage() -> None:
    """Half-token overlap counts as covered (PM prose paraphrasing).

    The matcher considers a check covered when at least half of
    its meaningful tokens appear in the PM description.  This
    keeps a paraphrase like "cleaner finishes" from triggering a
    question about "cleaner ETA" — sharing "cleaner" satisfies
    1-of-2 = 50%.
    """
    scenario = _scenario(
        required_data_checks=("cleaner ETA",),
    )
    questions = build_clarifying_questions(
        "Allow early check-in when cleaner finishes",
        [scenario],
    )
    assert questions == ()


def test_blank_description_emits_all_missed_checks() -> None:
    """An empty PM description leaves every check uncovered."""
    scenario = _scenario(
        required_data_checks=(
            "cleaner ETA",
            "same-day turnover",
        ),
    )
    questions = build_clarifying_questions("   ", [scenario])
    missed = {q.missed_check for q in questions}
    assert missed == {"cleaner ETA", "same-day turnover"}


# ── caps + ordering ──────────────────────────────────────── #


def test_top_k_caps_consulted_scenarios() -> None:
    """Only the first ``top_k`` scenarios contribute questions."""
    s1 = _scenario(
        scenario_id="s1_first",
        title="First",
        required_data_checks=("alpha",),
    )
    s2 = _scenario(
        scenario_id="s2_second",
        title="Second",
        required_data_checks=("beta",),
    )
    s3 = _scenario(
        scenario_id="s3_third",
        title="Third",
        required_data_checks=("gamma",),
    )
    questions = build_clarifying_questions(
        "",
        [s1, s2, s3],
        top_k=2,
    )
    ids = {q.scenario_id for q in questions}
    assert ids == {"s1_first", "s2_second"}


def test_questions_per_scenario_caps_emissions() -> None:
    """Each scenario emits at most ``questions_per_scenario``."""
    scenario = _scenario(
        required_data_checks=tuple(
            f"check_{i}" for i in range(10)
        ),
    )
    questions = build_clarifying_questions(
        "",
        [scenario],
        questions_per_scenario=3,
    )
    assert len(questions) == 3


def test_ordering_is_deterministic() -> None:
    """Output order matches ``(scenario rank, check index)``."""
    s1 = _scenario(
        scenario_id="s1_a",
        title="A",
        required_data_checks=("a1", "a2"),
    )
    s2 = _scenario(
        scenario_id="s2_b",
        title="B",
        required_data_checks=("b1",),
    )
    questions = build_clarifying_questions("", [s1, s2])
    ids = [
        (q.scenario_id, q.missed_check) for q in questions
    ]
    assert ids == [
        ("s1_a", "a1"),
        ("s1_a", "a2"),
        ("s2_b", "b1"),
    ]


def test_default_caps_match_module_constants() -> None:
    """The defaults are 3 scenarios / 5 questions per scenario."""
    assert DEFAULT_TOP_K == 3
    assert DEFAULT_QUESTIONS_PER_SCENARIO == 5


# ── render_question_prompt ────────────────────────────────── #


def test_render_question_prompt_empty() -> None:
    """Empty iterable returns empty string."""
    assert render_question_prompt([]) == ""


def test_render_question_prompt_formats_block() -> None:
    """The rendered prompt is multi-line and references scenario ids."""
    questions = (
        IterativeQuestion(
            scenario_id="s1_a",
            scenario_title="Title A",
            missed_check="alpha",
            question="should this rule depend on alpha?",
        ),
        IterativeQuestion(
            scenario_id="s2_b",
            scenario_title="Title B",
            missed_check="beta",
            question="should this rule depend on beta?",
        ),
    )
    rendered = render_question_prompt(questions)
    assert "Foundation suggests asking the PM" in rendered
    assert "[s1_a: Title A]" in rendered
    assert "[s2_b: Title B]" in rendered
    assert rendered.count("\n") == 2
