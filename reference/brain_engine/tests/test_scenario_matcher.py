"""Tests for :class:`ScenarioMatcher` — Layer 2.

The matcher embeds canonical scenario trigger sentences once and
answers ``top_k(message)`` queries with cosine-ranked candidate
ids.  Tests pin:

* Empty / whitespace input ⇒ empty tuple (no raise).
* Known triggers rank themselves first (sanity).
* Multilingual queries hit the right scenario without per-language
  keywords.
* Determinism (ties break by scenario_id).
* Constructor rejects empty examples or duplicate ids.
* ``k`` must be positive.
* ``__len__`` reports the indexed scenario count.
* :func:`examples_from_mapping` skips empty texts silently.

These tests download the multilingual MiniLM model on first run
(~50 MB, cached for the rest of the CI run).
"""

from __future__ import annotations

import pytest

from brain_engine.patterns.scenario_matcher import (
    ScenarioCandidate,
    ScenarioExample,
    ScenarioMatcher,
    examples_from_mapping,
)


_REGISTRY: dict[str, str] = {
    "access_code_release": (
        "Guest asks for the door code or access credentials "
        "before arriving at the property."
    ),
    "early_checkin": (
        "Guest requests to arrive earlier than the official "
        "check-in time."
    ),
    "late_checkout": (
        "Guest requests to leave the property later than the "
        "official check-out time."
    ),
    "cancellation_request": (
        "Guest wants to cancel the reservation or asks for "
        "a refund."
    ),
    "damage_report": (
        "Guest reports something is broken, damaged, or "
        "malfunctioning inside the property."
    ),
    "noise_complaint": (
        "Guest complains about loud neighbours, construction, "
        "or noise at night."
    ),
    "parking_request": (
        "Guest asks about parking options near the property "
        "or how to park their car."
    ),
    "pet_policy_exception": (
        "Guest asks to bring a pet — dog, cat, or other "
        "animal — into the property."
    ),
}


@pytest.fixture(scope="module")
def matcher() -> ScenarioMatcher:
    """Module-scoped matcher — cold-start cost amortised once."""
    return ScenarioMatcher(examples_from_mapping(_REGISTRY))


# ── core behaviour ─────────────────────────────────────────── #


def test_empty_text_returns_empty_tuple(
    matcher: ScenarioMatcher,
) -> None:
    """Empty input must not invoke the embedder."""
    assert matcher.top_k("") == ()
    assert matcher.top_k("   \n  ") == ()


def test_known_trigger_ranks_itself_first(
    matcher: ScenarioMatcher,
) -> None:
    """Embedding a registered trigger surfaces it as top-1."""
    text = _REGISTRY["access_code_release"]
    top = matcher.top_k(text, k=1)
    assert len(top) == 1
    assert top[0].scenario_id == "access_code_release"
    assert top[0].similarity > 0.9


def test_top_k_returns_at_most_k(
    matcher: ScenarioMatcher,
) -> None:
    """The output never exceeds the requested ``k``."""
    top = matcher.top_k("door code", k=3)
    assert len(top) == 3
    top = matcher.top_k("door code", k=100)
    assert len(top) == len(_REGISTRY)


def test_top_k_orders_by_descending_similarity(
    matcher: ScenarioMatcher,
) -> None:
    """The ``ScenarioCandidate`` tuple is sorted high → low."""
    top = matcher.top_k("door code please", k=5)
    similarities = [c.similarity for c in top]
    assert similarities == sorted(similarities, reverse=True)


# ── multilingual cross-coverage ───────────────────────────── #


@pytest.mark.parametrize(
    "text,expected_scenario",
    [
        ("Can I get the door code?", "access_code_release"),
        (
            "Şifre alabilir miyim?",  # Turkish — access code
            "access_code_release",
        ),
        (
            "Можно ли получить код от двери?",  # Russian
            "access_code_release",
        ),
        (
            "Erken giriş yapabilir miyim?",  # TR — early checkin
            "early_checkin",
        ),
        (
            "Может мы выедем попозже?",  # RU — late checkout
            "late_checkout",
        ),
    ],
)
def test_multilingual_query_hits_right_scenario(
    matcher: ScenarioMatcher,
    text: str,
    expected_scenario: str,
) -> None:
    """No per-language keywords needed — embeddings handle it.

    The test passes when the expected scenario lands in the top-3
    (not necessarily top-1) — the embedding ranker is fuzzy and a
    closely related scenario (e.g. ``early_checkin`` vs
    ``access_code_release``) might tie.  Production usage hands
    the top-K to the LLM for the final pick.
    """
    top_ids = {c.scenario_id for c in matcher.top_k(text, k=3)}
    assert expected_scenario in top_ids


# ── determinism + validation ──────────────────────────────── #


def test_deterministic_output(
    matcher: ScenarioMatcher,
) -> None:
    """Two queries on the same text yield identical rankings."""
    first = matcher.top_k("the door code")
    second = matcher.top_k("the door code")
    assert first == second


def test_len_reports_indexed_count(
    matcher: ScenarioMatcher,
) -> None:
    """``len(matcher)`` matches the registry size."""
    assert len(matcher) == len(_REGISTRY)


def test_constructor_rejects_empty_examples() -> None:
    """At least one example is required."""
    with pytest.raises(ValueError, match="examples"):
        ScenarioMatcher(examples=())


def test_constructor_rejects_duplicate_ids() -> None:
    """Two examples with the same ``scenario_id`` are rejected."""
    with pytest.raises(ValueError, match="duplicate scenario_id"):
        ScenarioMatcher(
            examples=[
                ScenarioExample(scenario_id="x", text="a"),
                ScenarioExample(scenario_id="x", text="b"),
            ],
        )


def test_top_k_rejects_non_positive_k(
    matcher: ScenarioMatcher,
) -> None:
    """``k <= 0`` is a programmer bug, not a runtime fallback."""
    with pytest.raises(ValueError, match="k"):
        matcher.top_k("anything", k=0)
    with pytest.raises(ValueError, match="k"):
        matcher.top_k("anything", k=-1)


def test_scenario_example_rejects_empty_id() -> None:
    """``scenario_id`` is required."""
    with pytest.raises(ValueError, match="scenario_id"):
        ScenarioExample(scenario_id="", text="some text")


def test_scenario_example_rejects_empty_text() -> None:
    """``text`` is required."""
    with pytest.raises(ValueError, match="text"):
        ScenarioExample(scenario_id="x", text="")
    with pytest.raises(ValueError, match="text"):
        ScenarioExample(scenario_id="x", text="   ")


def test_scenario_candidate_rejects_out_of_range_similarity() -> None:
    """``similarity`` outside ``[-1, 1]`` is rejected."""
    with pytest.raises(ValueError, match="similarity"):
        ScenarioCandidate(
            scenario_id="x", similarity=1.5, text="t",
        )


def test_examples_from_mapping_skips_empty_texts() -> None:
    """The convenience helper drops blank rows silently."""
    examples = examples_from_mapping(
        {"a": "first", "b": "", "c": "  ", "d": "fourth"},
    )
    ids = {e.scenario_id for e in examples}
    assert ids == {"a", "d"}


def test_load_is_idempotent() -> None:
    """Calling ``load`` twice does not re-embed."""
    instance = ScenarioMatcher(examples_from_mapping(_REGISTRY))
    instance.load()
    vectors_first = instance._vectors  # noqa: SLF001 - probe
    instance.load()
    vectors_second = instance._vectors  # noqa: SLF001 - probe
    assert vectors_first is vectors_second
