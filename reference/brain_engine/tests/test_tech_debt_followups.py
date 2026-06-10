"""Tests for the three tech-debt follow-ups (post-Sprint 1-5).

Covers:

1. **CompositeNightlyRunner** — fans one nightly tick out to N
   underlying runners, isolates per-runner failures, merges the
   payloads under per-runner keys.
2. **DecisionClassifier fallback INFORM telemetry** —
   :class:`PrometheusExporter` exposes
   ``brain_decision_classifier_fallback_inform_total{scenario}``
   and the classifier emits one tick whenever both the LLM hint
   AND the keyword chain return ``INFORM`` (Aybüke Q3).

The migration ConfigMap update for ``deploy/postgres-migrations.yaml``
is verified by ``yaml.safe_load_all`` parse (executed at apply
time on the cluster); the YAML structure is too large for an
inline assertion suite, so its sanity check stays at the YAML
parser level.
"""

from __future__ import annotations

import pytest

from brain_engine.observability.exporters.prometheus_exporter import (
    build_default_exporter,
)
from brain_engine.scheduler.composite import CompositeNightlyRunner

# ---------------------------------------------------------------------------
# CompositeNightlyRunner
# ---------------------------------------------------------------------------


class _StubRunner:
    """NightlyRunner stub with configurable behaviour."""

    def __init__(
        self,
        *,
        result: dict | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.calls = 0
        self._result = result if result is not None else {}
        self._raise = raise_exc

    async def run_nightly(self) -> dict:
        self.calls += 1
        if self._raise is not None:
            raise self._raise
        return dict(self._result)


def test_constructor_rejects_empty_runners() -> None:
    with pytest.raises(ValueError):
        CompositeNightlyRunner(())


@pytest.mark.asyncio
async def test_invokes_every_runner_in_order() -> None:
    a = _StubRunner(result={"a": 1})
    b = _StubRunner(result={"b": 2})
    composite = CompositeNightlyRunner((a, b))
    out = await composite.run_nightly()
    assert a.calls == 1
    assert b.calls == 1
    assert out == {
        "_StubRunner": {"b": 2},  # second wins on key collision
    }


@pytest.mark.asyncio
async def test_aggregates_under_per_class_name() -> None:
    class _ConsolidatorStub(_StubRunner):
        pass

    class _ArchiverStub(_StubRunner):
        pass

    consolidator = _ConsolidatorStub(result={"step": "consolidated"})
    archiver = _ArchiverStub(result={"archived": 7})
    composite = CompositeNightlyRunner((consolidator, archiver))
    out = await composite.run_nightly()
    assert out == {
        "_ConsolidatorStub": {"step": "consolidated"},
        "_ArchiverStub": {"archived": 7},
    }


@pytest.mark.asyncio
async def test_failure_isolated_other_runners_still_ran() -> None:
    class _FailRunner(_StubRunner):
        pass

    class _OkRunner(_StubRunner):
        pass

    failing = _FailRunner(raise_exc=RuntimeError("db blip"))
    healthy = _OkRunner(result={"ok": True})
    composite = CompositeNightlyRunner((failing, healthy))
    out = await composite.run_nightly()
    assert healthy.calls == 1
    failing_payload = out["_FailRunner"]
    assert failing_payload["_failed"] is True
    assert failing_payload["error"] == "db blip"
    assert failing_payload["error_type"] == "RuntimeError"
    assert out["_OkRunner"] == {"ok": True}


@pytest.mark.asyncio
async def test_single_runner_passes_through() -> None:
    only = _StubRunner(result={"k": "v"})
    composite = CompositeNightlyRunner((only,))
    out = await composite.run_nightly()
    assert out == {"_StubRunner": {"k": "v"}}


# ---------------------------------------------------------------------------
# Aybüke Q3 — fallback INFORM telemetry
# ---------------------------------------------------------------------------


def test_fallback_inform_counter_records_label() -> None:
    exporter = build_default_exporter()
    exporter.record_classifier_fallback_inform(
        scenario="general",
    )
    exporter.record_classifier_fallback_inform(
        scenario="access_code_release",
    )
    exporter.record_classifier_fallback_inform(
        scenario="general",
    )
    from prometheus_client import generate_latest

    text = generate_latest(exporter._registry).decode()
    # Both scenarios distinct as Prometheus labels.
    assert (
        "brain_decision_classifier_fallback_inform_total"
        in text
    )
    assert 'scenario="general"' in text
    assert 'scenario="access_code_release"' in text


def test_classifier_emits_fallback_inform_when_no_signal() -> None:
    """End-to-end: blank message → both hint + keyword INFORM → counter ticks."""
    from brain_engine.conversation.models import BusinessFlags
    from brain_engine.patterns.classifier import DecisionClassifier

    classifier = DecisionClassifier()
    exporter = build_default_exporter()

    # Read counter value before the call so the assertion below is
    # robust to other tests in the same process bumping the same
    # series (Prometheus counters are process-global).
    from prometheus_client import generate_latest

    def _read_count(label_value: str) -> float:
        text = generate_latest(exporter._registry).decode()
        for line in text.splitlines():
            if line.startswith(
                "brain_decision_classifier_fallback_inform_total{"
                f'scenario="{label_value}"'
            ):
                return float(line.rsplit(" ", 1)[1])
        return 0.0

    before = _read_count("general")
    # Need BOTH non-empty response AND no decision keyword to land
    # on INFORM — empty response triggers DEFER fallback per
    # ``_classify_decision_type`` line 1338.
    classifier.classify(
        business_flags=BusinessFlags(),
        message_text="hi there",
        response_text="here is the wifi info you asked about",
    )
    after = _read_count("general")
    assert after == pytest.approx(before + 1.0)


def test_classifier_no_emit_when_keyword_committed() -> None:
    """Counter stays put when the keyword chain returned a non-INFORM."""
    from brain_engine.conversation.models import BusinessFlags
    from brain_engine.patterns.classifier import DecisionClassifier

    classifier = DecisionClassifier()
    exporter = build_default_exporter()

    from prometheus_client import generate_latest

    def _read_count(label_value: str) -> float:
        text = generate_latest(exporter._registry).decode()
        for line in text.splitlines():
            if line.startswith(
                "brain_decision_classifier_fallback_inform_total{"
                f'scenario="{label_value}"'
            ):
                return float(line.rsplit(" ", 1)[1])
        return 0.0

    before = _read_count("access_code_release")
    classifier.classify(
        business_flags=BusinessFlags(),
        message_text="What is the door code?",
        response_text=(
            "We will get back to you closer to your check-in"
        ),  # DEFER keyword
    )
    after = _read_count("access_code_release")
    # Keyword chain returned DEFER (not INFORM) → no fallback tick.
    assert after == before
