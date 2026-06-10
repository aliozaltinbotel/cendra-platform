"""Sprint 6 W10 wiring tests — discovery → FL-15 clarifying questions.

Pins:

* :func:`_foundation_hint_scenarios` — deterministic stage-coverage
  selection, capped at ``_FOUNDATION_HINT_LIMIT``.
* :func:`_clarifying_question_block`:
    - Empty when the catalog is absent.
    - Empty when the PM description already covers every check.
    - Non-empty + well-formed when missed checks exist.
* The discovery system prompt concatenates the clarifying block
  between the sector context and the verbatim ``_DISCOVERY_PROMPT``
  template, with a trailing blank line for separation.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from brain_engine.patterns.foundation_registry import FoundationScenario
from brain_engine.rule_creation import workflow as workflow_module
from brain_engine.rule_creation.workflow import (
    _DISCOVERY_PROMPT,
    _clarifying_question_block,
    _foundation_hint_scenarios,
)

# ── fixtures ──────────────────────────────────────────────── #


def _scenario(
    *,
    scenario_id: str,
    title: str,
    stage_number: int,
    stage_label: str,
    required_data_checks: tuple[str, ...] = (),
) -> FoundationScenario:
    """Build a minimal :class:`FoundationScenario` for the helper."""
    return FoundationScenario(
        scenario_id=scenario_id,
        title=title,
        stage_number=stage_number,
        stage_label=stage_label,
        trigger="trigger body",
        required_data_checks=required_data_checks,
    )


@pytest.fixture
def _patched_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[
    list[FoundationScenario],
    None,
    None,
]:
    """Replace the cached foundation with a hand-crafted set.

    Yields the mutable scenario list so individual tests can
    inject the exact catalog they need without re-parsing the
    real markdown.  Restored automatically after each test.
    """
    bucket: list[FoundationScenario] = []

    def fake_load() -> tuple[FoundationScenario, ...]:
        return tuple(bucket)

    monkeypatch.setattr(
        workflow_module,
        "_load_foundation_cached",
        fake_load,
    )
    yield bucket


# ── _foundation_hint_scenarios ────────────────────────────── #


def test_hint_scenarios_empty_when_catalog_missing(
    _patched_cache: list[FoundationScenario],
) -> None:
    """Empty catalog ⇒ empty scenario list (no exception)."""
    assert _foundation_hint_scenarios() == []


def test_hint_scenarios_one_per_stage(
    _patched_cache: list[FoundationScenario],
) -> None:
    """Stage-coverage selection: first scenario seen per stage wins."""
    _patched_cache.extend(
        [
            _scenario(
                scenario_id="s1_a",
                title="A",
                stage_number=1,
                stage_label="Pre-Booking",
            ),
            _scenario(
                scenario_id="s1_b",
                title="B",
                stage_number=1,
                stage_label="Pre-Booking",
            ),
            _scenario(
                scenario_id="s2_a",
                title="C",
                stage_number=2,
                stage_label="Booking",
            ),
        ],
    )
    picked = _foundation_hint_scenarios()
    ids = [s.scenario_id for s in picked]
    assert ids == ["s1_a", "s2_a"]


def test_hint_scenarios_respect_limit(
    _patched_cache: list[FoundationScenario],
) -> None:
    """Output never exceeds ``_FOUNDATION_HINT_LIMIT``."""
    _patched_cache.extend(
        [
            _scenario(
                scenario_id=f"s{i}_first",
                title=f"S{i}",
                stage_number=i,
                stage_label=f"Stage {i}",
            )
            for i in range(1, 10)
        ],
    )
    picked = _foundation_hint_scenarios()
    assert len(picked) <= workflow_module._FOUNDATION_HINT_LIMIT


# ── _clarifying_question_block ───────────────────────────── #


def test_block_empty_when_catalog_missing(
    _patched_cache: list[FoundationScenario],
) -> None:
    """No catalog ⇒ no block (legacy fall-through)."""
    assert _clarifying_question_block("anything") == ""


def test_block_empty_when_every_check_covered(
    _patched_cache: list[FoundationScenario],
) -> None:
    """When the PM mentioned every check the block stays empty."""
    _patched_cache.append(
        _scenario(
            scenario_id="s1_check_in",
            title="Early check-in",
            stage_number=1,
            stage_label="Pre-Booking",
            required_data_checks=("cleaner ETA",),
        ),
    )
    block = _clarifying_question_block(
        "Allow early check-in when cleaner ETA is before 13:00",
    )
    assert block == ""


def test_block_lists_missed_checks(
    _patched_cache: list[FoundationScenario],
) -> None:
    """Missed checks land in the rendered block with scenario refs."""
    _patched_cache.append(
        _scenario(
            scenario_id="s1_check_in",
            title="Early check-in",
            stage_number=1,
            stage_label="Pre-Booking",
            required_data_checks=("cleaner ETA", "same-day turnover"),
        ),
    )
    block = _clarifying_question_block(
        "Allow early check-in when cleaner finishes",
    )
    assert "Foundation suggests asking the PM" in block
    # The matched scenario id appears verbatim so the LLM can
    # quote it back at the PM in iterative-questioning style.
    assert "s1_check_in" in block
    assert "same-day turnover" in block


def test_block_caps_with_default_top_k_and_per_scenario(
    _patched_cache: list[FoundationScenario],
) -> None:
    """The block size is bounded by FL-15's defaults.

    Even when many scenarios surface many missed checks each, the
    output cannot exceed ``DEFAULT_TOP_K * DEFAULT_QUESTIONS_PER_SCENARIO``
    questions (3 * 5 = 15 lines + 1 header).
    """
    for stage_number in range(1, 10):
        _patched_cache.append(
            _scenario(
                scenario_id=f"s{stage_number}_x",
                title=f"Stage {stage_number}",
                stage_number=stage_number,
                stage_label=f"Stage {stage_number}",
                required_data_checks=tuple(
                    f"check_{i}" for i in range(10)
                ),
            ),
        )
    block = _clarifying_question_block("")
    # 1 header line + at most 15 question lines.
    assert block.count("\n") <= 15


# ── discovery prompt composition ─────────────────────────── #


def test_discovery_prompt_concatenates_sector_and_clarifying(
    _patched_cache: list[FoundationScenario],
) -> None:
    """The system prompt carries sector hints + clarifying block + template.

    Builds the same string ``_run_discovery`` would produce given
    a populated catalog and a description that misses one check.
    The test does not invoke ``_run_discovery`` itself (that
    would hit ``litellm``); it pins the prompt-composition
    behaviour by exercising the helpers + the same concatenation
    rule used inside the handler.
    """
    _patched_cache.extend(
        [
            _scenario(
                scenario_id="s1_check_in",
                title="Early check-in",
                stage_number=1,
                stage_label="Pre-Booking",
                required_data_checks=("cleaner ETA", "same-day turnover"),
            ),
            _scenario(
                scenario_id="s5_in_stay",
                title="During stay info",
                stage_number=5,
                stage_label="During Stay",
            ),
        ],
    )
    hint_lines = workflow_module._foundation_hint_lines()
    sector_block = (
        "Hospitality sector context (Brain Engine foundation, "
        "representative scenarios — use as background only, do "
        "not reference them in the reply):\n"
        + "\n".join(hint_lines)
        + "\n\n"
    )
    clarifying = _clarifying_question_block("Allow early check-in")
    assert clarifying  # missed checks present
    system_prompt = sector_block + clarifying + "\n\n" + _DISCOVERY_PROMPT
    # Sector hints come first.
    assert system_prompt.startswith(
        "Hospitality sector context",
    )
    # Clarifying block sits between sector hints and the
    # discovery template.
    sector_end = system_prompt.index("Foundation suggests")
    template_start = system_prompt.index("You are the discovery agent")
    assert sector_end < template_start
