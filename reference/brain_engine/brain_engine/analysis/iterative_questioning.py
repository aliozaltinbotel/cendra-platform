# ruff: noqa: RUF002
# Module docstring quotes Ali's Turkish requirement verbatim;
# the Turkish letters are intentional, not typos.
"""Foundation-aware iterative questioning helper (FL-15).

Closes Ali's Turkish requirement #4 — *"LLM bu foundationdaki
senaryolari değerlendirerek diyecekki ya sen early check'in 'de
şu durum olsun istedin ama evin temizlik durumunu atladın buna
da bakmami istermisin"*.  When a PM authors a rule via natural
language (in the ``rule_creation`` discovery phase), the LLM
should not be left to guess which operational conditions matter;
instead, Brain Engine consults the top-K matched foundation
scenarios and surfaces clarifying questions about
``Required Data Checks`` the PM may have skipped.

The helper is pure compute:

* No LLM call.  The questions are deterministic prompts derived
  from the foundation catalog.  The caller is expected to feed
  them into its own LLM step (e.g. ``rule_creation`` discovery
  agent) as a context hint or to render them verbatim in the UI.
* No I/O.  The catalog rows are passed in by the caller after it
  ran the FL-16 orchestrator's match step.
* Idempotent.  Same PM text + same scenario set ⇒ same
  questions.

Sprint 5 ships the helper.  Wiring it into
``brain_engine/rule_creation/workflow.py`` discovery phase is the
FL-15b follow-up.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from brain_engine.patterns.foundation_registry import FoundationScenario

__all__ = [
    "DEFAULT_QUESTIONS_PER_SCENARIO",
    "DEFAULT_TOP_K",
    "IterativeQuestion",
    "build_clarifying_questions",
    "render_question_prompt",
]


# Limits keep the prompt small enough for an LLM round-trip — five
# missed checks per scenario across the top-K matches is enough
# context to triage every meaningful gap without ballooning the
# prompt.
DEFAULT_TOP_K: Final[int] = 3
DEFAULT_QUESTIONS_PER_SCENARIO: Final[int] = 5


# Words common enough that mentioning them in the PM description
# should not count as "this concept is covered" — they are noise
# from typical PM prose and would otherwise mask real coverage
# gaps.
_NOISE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "and",
        "or",
        "the",
        "a",
        "an",
        "to",
        "of",
        "for",
        "in",
        "is",
        "with",
        "on",
        "by",
        "be",
        "this",
        "that",
        "if",
        "when",
        "i",
        "we",
        "my",
        "our",
    },
)


_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class IterativeQuestion:
    """One clarifying question Brain Engine wants the PM to answer.

    Attributes:
        scenario_id: Foundation slug the question came from — lets
            the rule-creation UI link the question to the
            scenario rationale ("based on s1_16_guest_asks_for_
            early_check_in").
        scenario_title: Human-readable title for the same scenario.
        missed_check: The verbatim Required Data Check label from
            the foundation row that the PM description did not
            cover.
        question: A short, PM-facing sentence asking whether the
            missed check should apply to the rule.  Deterministic
            template so a future LLM refinement is opt-in.
    """

    scenario_id: str
    scenario_title: str
    missed_check: str
    question: str


def build_clarifying_questions(
    pm_description: str,
    scenarios: Iterable[FoundationScenario],
    *,
    top_k: int = DEFAULT_TOP_K,
    questions_per_scenario: int = DEFAULT_QUESTIONS_PER_SCENARIO,
) -> tuple[IterativeQuestion, ...]:
    """Surface clarifying questions for the PM's NL rule description.

    Args:
        pm_description: The PM's free-form description of the rule.
            Used to detect which foundation ``Required Data Checks``
            are already covered (mentioned at least once) and which
            should be surfaced as clarifying questions.
        scenarios: Top-K foundation scenarios from the matcher.
            Order matters — ``scenarios[0]`` is treated as the
            dominant match and its missed checks come first.
        top_k: Maximum number of scenarios consulted.  Defaults to
            :data:`DEFAULT_TOP_K`.  Extra scenarios past the cap
            are ignored so the resulting prompt stays small.
        questions_per_scenario: Cap on questions emitted per
            scenario.  Defaults to
            :data:`DEFAULT_QUESTIONS_PER_SCENARIO`.

    Returns:
        Deterministic tuple of :class:`IterativeQuestion` in the
        order ``(scenario rank ASC, Required Data Check order)``.
        Empty when no scenario contributes a missed check.

    Raises:
        ValueError: When ``top_k`` or ``questions_per_scenario``
            is not positive.
    """
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if questions_per_scenario <= 0:
        raise ValueError("questions_per_scenario must be positive")

    mentioned = _tokens(pm_description)
    questions: list[IterativeQuestion] = []
    emitted_checks: set[tuple[str, str]] = set()

    for scenario in tuple(scenarios)[:top_k]:
        scenario_questions = 0
        for check in scenario.required_data_checks:
            if scenario_questions >= questions_per_scenario:
                break
            normalised = check.strip()
            if not normalised:
                continue
            if _is_covered(normalised, mentioned):
                continue
            key = (scenario.scenario_id, normalised.lower())
            if key in emitted_checks:
                continue
            emitted_checks.add(key)
            questions.append(
                IterativeQuestion(
                    scenario_id=scenario.scenario_id,
                    scenario_title=scenario.title,
                    missed_check=normalised,
                    question=_format_question(scenario, normalised),
                ),
            )
            scenario_questions += 1
    return tuple(questions)


def render_question_prompt(
    questions: Iterable[IterativeQuestion],
) -> str:
    """Render the questions into a single LLM-friendly prompt block.

    Format:

    ::

        Foundation suggests asking the PM about these checks:
        - [s1_16_guest_asks_for_early_check_in: Guest asks for
          early check-in before booking] cleaner ETA — should
          this rule depend on cleaner ETA?
        - ...

    Returns an empty string when ``questions`` is empty so the
    caller can simply concatenate the block into a larger prompt
    without a branch.
    """
    lines: list[str] = []
    for question in questions:
        lines.append(
            f"- [{question.scenario_id}: {question.scenario_title}] "
            f"{question.missed_check} — {question.question}",
        )
    if not lines:
        return ""
    return (
        "Foundation suggests asking the PM about these checks:\n"
        + "\n".join(lines)
    )


# ── helpers ───────────────────────────────────────────────── #


def _tokens(text: str) -> frozenset[str]:
    """Return the set of meaningful lowercase tokens in ``text``.

    Splits on non-alphanumeric characters, lowercases, and removes
    the :data:`_NOISE_TOKENS` set.  Used by :func:`_is_covered` to
    decide whether the PM description already mentions a given
    Required Data Check.
    """
    raw = _TOKEN_RE.findall(text.lower())
    return frozenset(token for token in raw if token not in _NOISE_TOKENS)


def _is_covered(check: str, mentioned: frozenset[str]) -> bool:
    """Whether ``check`` is already covered by the PM's description.

    A check is considered covered when at least half of its
    *meaningful* tokens appear in ``mentioned``.  Half-coverage
    matches PM prose that paraphrases ("cleaner finishes by 13:00"
    covers "cleaner ETA") without false-positive matches on a
    single shared noise word.  Empty checks are treated as covered
    so the caller never emits a question for them.
    """
    check_tokens = _tokens(check)
    if not check_tokens:
        return True
    overlap = check_tokens & mentioned
    return len(overlap) * 2 >= len(check_tokens)


def _format_question(
    scenario: FoundationScenario,
    missed_check: str,
) -> str:
    """Build the PM-facing sentence for one missed Required Data Check."""
    return (
        f"Foundation scenario {scenario.scenario_id!r} "
        f"({scenario.title!r}) requires checking "
        f"{missed_check!r} — should this rule depend on "
        f"{missed_check} too?"
    )
