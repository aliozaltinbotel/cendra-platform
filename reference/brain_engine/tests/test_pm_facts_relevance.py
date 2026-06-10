# ruff: noqa: RUF001, RUF002, RUF003
# RUF001 / RUF002 (ambiguous unicode in strings / docstrings) are
# suppressed file-wide because the live Sandbox traffic this
# diagnostic targets is Turkish-first.  Turkish letters ``ı`` / ``ş``
# / ``ğ`` look similar to Latin counterparts but are semantically
# distinct — flattening them would corrupt the test fixtures.
"""Tests for ``compute_pm_fact_relevance_stats``.

Tester complaint #4 (Sandbox round, 2026-05-19) said every PM-
confirmed fact is dumped into the system prompt as a flat
``MANAGER-CONFIRMED KNOWLEDGE`` appendix, regardless of whether the
fact has anything to do with the current guest turn.  The fix could
be topic-relevant retrieval, but the closing log dated 2026-05-20
recommended **measure-before-fix**: ship a pure-observability
diagnostic first, then decide on real data.

These tests pin the helper that the diagnostic relies on:

* Pure function — no I/O, no globals, no env reads.
* No hardcoded vocabularies — every output is a function of the
  inputs (``[[feedback_no_hardcode]]``).
* Whitespace-only facts excluded from the count, matching the
  rendered bulleted list in
  :meth:`ConversationService._append_pm_facts`.
* Tokenisation is Unicode-aware so Turkish / Cyrillic messages
  produce non-zero Jaccard when there is real overlap.
* Empty message OR empty fact list returns zeroed Jaccard fields —
  the caller treats ``count`` as the source of truth for "did the
  numbers carry signal".
"""

from __future__ import annotations

import logging

import pytest

from brain_engine.conversation.pm_facts import (
    PmFactRelevanceStats,
    compute_pm_fact_relevance_stats,
    log_pm_fact_relevance,
)

# ── Empty / degenerate inputs ────────────────────────────────────


def test_empty_fact_list_returns_zero_stats() -> None:
    stats = compute_pm_fact_relevance_stats([], "guest message")
    assert stats == PmFactRelevanceStats(
        count=0,
        total_chars=0,
        message_chars=len("guest message"),
        jaccard_max=0.0,
        jaccard_mean=0.0,
        jaccard_min=0.0,
    )


def test_empty_message_zeroes_jaccard_but_preserves_counts() -> None:
    """No message tokens → no relevance signal, but the diagnostic
    still records fact volume so the team can see "many facts, no
    message tokens" in the logs."""
    stats = compute_pm_fact_relevance_stats(
        ["WiFi password is abcdef", "Parking on street"],
        "",
    )
    assert stats.count == 2
    assert stats.total_chars == len("WiFi password is abcdef") + len(
        "Parking on street",
    )
    assert stats.message_chars == 0
    assert stats.jaccard_max == 0.0
    assert stats.jaccard_mean == 0.0
    assert stats.jaccard_min == 0.0


def test_whitespace_only_facts_excluded_from_count() -> None:
    """Mirrors the rendered bulleted list — whitespace-only fact
    text is dropped before joining, so the diagnostic must agree
    with what the LLM actually sees."""
    stats = compute_pm_fact_relevance_stats(
        ["   ", "\n\t", "real fact about wifi"],
        "wifi please",
    )
    assert stats.count == 1
    assert stats.total_chars == len("real fact about wifi")


# ── Overlap maths ────────────────────────────────────────────────


def test_single_fact_with_full_overlap_jaccard_is_one() -> None:
    stats = compute_pm_fact_relevance_stats(
        ["pet policy allowed"],
        "pet policy allowed",
    )
    assert stats.count == 1
    assert stats.jaccard_max == pytest.approx(1.0)
    assert stats.jaccard_mean == pytest.approx(1.0)
    assert stats.jaccard_min == pytest.approx(1.0)


def test_single_fact_with_zero_overlap_jaccard_is_zero() -> None:
    stats = compute_pm_fact_relevance_stats(
        ["parking is on the street"],
        "wifi password",
    )
    assert stats.count == 1
    assert stats.jaccard_max == 0.0
    assert stats.jaccard_mean == 0.0
    assert stats.jaccard_min == 0.0


def test_partial_overlap_jaccard_in_open_interval() -> None:
    """One shared token (``wifi``) out of a 4-element union
    (``wifi``, ``password``, ``code``, ``door``) — Jaccard = 1/4."""
    stats = compute_pm_fact_relevance_stats(
        ["wifi password"],
        "wifi door code",
    )
    assert stats.count == 1
    assert stats.jaccard_max == pytest.approx(0.25)
    assert stats.jaccard_mean == pytest.approx(0.25)
    assert stats.jaccard_min == pytest.approx(0.25)


def test_multiple_facts_min_max_mean_aggregate_correctly() -> None:
    """The aggregate fields cover the spread the diagnostic is
    designed to surface — one highly relevant fact alongside two
    irrelevant ones should show as ``max≈1`` but ``mean`` pulled
    down by the long tail."""
    stats = compute_pm_fact_relevance_stats(
        [
            "wifi password please",  # full overlap with msg
            "parking is on the street",  # no overlap
            "extra cleaning fee charged",  # no overlap
        ],
        "wifi password please",
    )
    assert stats.count == 3
    assert stats.jaccard_max == pytest.approx(1.0)
    assert stats.jaccard_min == 0.0
    # Mean = (1 + 0 + 0) / 3 ≈ 0.333
    assert stats.jaccard_mean == pytest.approx(1.0 / 3)


# ── Unicode tokenisation ─────────────────────────────────────────


def test_turkish_tokens_produce_real_overlap() -> None:
    """The live Sandbox traffic is Turkish-first — tokenisation must
    treat Turkish letters (``ş``, ``ı``, ``ğ``, ``ü``, ``ö``,
    ``ç``) as word characters so the diagnostic does not silently
    show zero overlap for a Turkish message that does match a
    Turkish fact."""
    stats = compute_pm_fact_relevance_stats(
        ["ek ücret olup olmadığı bilgisi"],
        "ek ücret olup olmadığı",
    )
    assert stats.count == 1
    # 4 shared tokens / 5 unique tokens in the union = 0.8
    assert stats.jaccard_max == pytest.approx(0.8)


def test_tokenisation_is_case_insensitive() -> None:
    """``WiFi`` in the fact and ``wifi`` in the message must
    match — otherwise the diagnostic underreports relevance for
    the most common live shape."""
    stats = compute_pm_fact_relevance_stats(
        ["WiFi password is ABCDEF"],
        "wifi password please",
    )
    assert stats.count == 1
    # Shared: {wifi, password}; union: {wifi, password, is, abcdef,
    # please} → 2 / 5 = 0.4
    assert stats.jaccard_max == pytest.approx(0.4)


# ── No-hardcode and purity ───────────────────────────────────────


def test_function_has_no_side_effects() -> None:
    """Two calls with the same inputs must return equal stats —
    pins that no caching / accumulation creeps in."""
    inputs = (["wifi password"], "wifi door code")
    first = compute_pm_fact_relevance_stats(*inputs)
    second = compute_pm_fact_relevance_stats(*inputs)
    assert first == second


def test_count_and_total_chars_skip_whitespace_only_entries() -> None:
    """Total chars must exclude whitespace-only entries — otherwise
    the diagnostic over-reports prompt-budget consumption."""
    stats = compute_pm_fact_relevance_stats(
        ["", " \n ", "a"],
        "a",
    )
    assert stats.count == 1
    assert stats.total_chars == 1


# ── log_pm_fact_relevance — emit contract ────────────────────────


def test_log_helper_emits_single_info_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One call → one INFO line tagged ``pm_facts.relevance``."""
    target = logging.getLogger("test_pm_fact_relevance_emit")
    with caplog.at_level(logging.INFO, logger=target.name):
        stats = log_pm_fact_relevance(
            ["wifi password please"],
            "wifi password",
            property_id="prop1",
            customer_id="cust1",
            logger=target,
        )

    records = [r for r in caplog.records if "pm_facts.relevance" in r.message]
    assert len(records) == 1
    assert stats.count == 1
    payload = records[0].getMessage()
    assert "count=1" in payload
    assert "property=prop1" in payload
    assert "customer=cust1" in payload
    assert "jaccard_max=" in payload


def test_log_helper_returns_stats_for_reuse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The helper returns the stats so callers can chain follow-up
    emissions (structured events, metrics) without recomputing."""
    target = logging.getLogger("test_pm_fact_relevance_return")
    with caplog.at_level(logging.INFO, logger=target.name):
        stats = log_pm_fact_relevance(
            ["wifi password", "parking on street"],
            "wifi password",
            property_id="prop1",
            customer_id="cust1",
            logger=target,
        )

    assert isinstance(stats, PmFactRelevanceStats)
    assert stats.count == 2
    # First fact full overlap, second zero overlap → mean = 0.5
    assert stats.jaccard_max == pytest.approx(1.0)
    assert stats.jaccard_min == 0.0


def test_log_helper_uses_module_logger_when_caller_omits_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The default logger is the module-local one so direct callers
    (scripts, ad-hoc REPL sessions) still see the diagnostic."""
    with caplog.at_level(
        logging.INFO,
        logger="brain_engine.conversation.pm_facts.relevance",
    ):
        log_pm_fact_relevance(
            ["wifi"],
            "wifi",
            property_id="prop1",
            customer_id="cust1",
        )

    assert any("pm_facts.relevance" in r.message for r in caplog.records)
