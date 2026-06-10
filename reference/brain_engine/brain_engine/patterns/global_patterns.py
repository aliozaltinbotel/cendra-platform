"""Cross-property pattern miner.

Where :class:`brain_engine.patterns.pattern_miner.PatternMiner`
finds rules within a single property, the global miner operates
one tier higher: it ingests *already-condensed* per-property
observations and emits patterns that hold consistently across
many properties.

Use cases (from ``brain_engine_advisory.md`` §7.4):

* Universal advice that benefits every PM ("late-arrival protocol
  on Friday-night check-ins").
* Cross-tenant feature flags surfacing as defaults for new tenants
  before their own pattern history is large enough.
* The product-moat fly-wheel — every additional tenant raises the
  signal floor for the next.

The miner is intentionally *coarse*: it never sees raw
``DecisionCase`` rows, only aggregated counters with no PII.  This
keeps the cross-tenant boundary clean (advisory §9.4) and lets the
caller decide whether the upstream aggregator runs inside the
tenant boundary or in a separate compliance-cleared job.

This module is pure compute — no I/O, no async, deterministic.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DEFAULT_MIN_DOMINANCE",
    "DEFAULT_MIN_PROPERTIES",
    "DEFAULT_MIN_PROPERTY_CONFIDENCE",
    "GlobalMinerConfig",
    "GlobalObservation",
    "GlobalPattern",
    "GlobalPatternMiner",
    "GlobalPatternReport",
]


DEFAULT_MIN_PROPERTIES: Final[int] = 3
"""Minimum distinct properties that must agree on the dominant
action before a global pattern is emitted."""

DEFAULT_MIN_PROPERTY_CONFIDENCE: Final[float] = 0.5
"""Per-property confidence floor — observations weaker than this
are dropped before aggregation, to keep noisy properties from
inflating the cross-property signal."""

DEFAULT_MIN_DOMINANCE: Final[float] = 0.6
"""Minimum share of *contributing* properties that must pick the
dominant action.  ``1.0`` would require unanimity; the default
lets one in three properties disagree without sinking the
pattern."""


@dataclass(frozen=True, slots=True)
class GlobalObservation:
    """One property's view of one scenario.

    Attributes:
        tenant_id: Owner tenant id (used for diversity counting —
            patterns supported by a single tenant across many of
            its properties are weaker than patterns supported by
            multiple independent tenants).
        property_id: Stable property identifier inside the tenant.
        scenario: Scenario key (e.g. ``"late_check_in_friday"``).
        dominant_action: Action chosen most often for this scenario
            inside this property.
        support: Number of cases backing the dominant action inside
            this property.
        confidence: Per-property confidence in
            ``[0.0, 1.0]`` — typically
            ``support / total_scenario_cases``.
    """

    tenant_id: str
    property_id: str
    scenario: str
    dominant_action: str
    support: int
    confidence: float

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("tenant_id must not be empty")
        if not self.property_id:
            raise ValueError("property_id must not be empty")
        if not self.scenario:
            raise ValueError("scenario must not be empty")
        if not self.dominant_action:
            raise ValueError("dominant_action must not be empty")
        if self.support < 1:
            raise ValueError("support must be >= 1")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must lie in [0.0, 1.0]")


@dataclass(frozen=True, slots=True)
class GlobalPattern:
    """An action that wins across multiple properties.

    Attributes:
        scenario: Scenario key shared by every contributing
            observation.
        action: Action that won the cross-property vote.
        properties_count: Distinct ``property_id`` values that
            backed ``action``.
        tenants_count: Distinct ``tenant_id`` values that backed
            ``action`` — measures *independent* support.
        total_support: Sum of per-property ``support`` values.
        mean_confidence: Mean of per-property ``confidence`` values
            among the agreeing properties.
        dominance: Share of properties with any observation in this
            scenario that voted for ``action``.  Always lies in
            ``(0.0, 1.0]``.
    """

    scenario: str
    action: str
    properties_count: int
    tenants_count: int
    total_support: int
    mean_confidence: float
    dominance: float

    def __post_init__(self) -> None:
        if self.properties_count < 1:
            raise ValueError("properties_count must be >= 1")
        if self.tenants_count < 1:
            raise ValueError("tenants_count must be >= 1")
        if self.total_support < 1:
            raise ValueError("total_support must be >= 1")
        if not 0.0 <= self.mean_confidence <= 1.0:
            raise ValueError(
                "mean_confidence must lie in [0.0, 1.0]",
            )
        if not 0.0 < self.dominance <= 1.0:
            raise ValueError("dominance must lie in (0.0, 1.0]")


@dataclass(frozen=True, slots=True)
class GlobalPatternReport:
    """Counters emitted alongside :meth:`GlobalPatternMiner.mine`.

    Attributes:
        considered_observations: Total observations after dropping
            those below ``min_property_confidence``.
        rejected_low_confidence: Observations dropped due to
            ``confidence < min_property_confidence``.
        scenarios_seen: Distinct scenario keys after filtering.
        below_min_properties: Scenarios skipped because the dominant
            action did not reach ``min_properties`` distinct backers.
        below_dominance: Scenarios skipped because the dominant
            action's share fell below ``min_dominance``.
        emitted_patterns: Number of :class:`GlobalPattern` rows
            returned.
    """

    considered_observations: int = 0
    rejected_low_confidence: int = 0
    scenarios_seen: int = 0
    below_min_properties: int = 0
    below_dominance: int = 0
    emitted_patterns: int = 0


@dataclass(frozen=True, slots=True)
class GlobalMinerConfig:
    """Tuning knobs for :class:`GlobalPatternMiner`.

    Defaults match the advisory's §7.4 product-moat tier — surface
    a pattern only if at least three properties agree and the
    dominant action wins 60 % of the field.
    """

    min_properties: int = DEFAULT_MIN_PROPERTIES
    min_property_confidence: float = DEFAULT_MIN_PROPERTY_CONFIDENCE
    min_dominance: float = DEFAULT_MIN_DOMINANCE

    def __post_init__(self) -> None:
        if self.min_properties < 1:
            raise ValueError("min_properties must be >= 1")
        if not 0.0 <= self.min_property_confidence <= 1.0:
            raise ValueError(
                "min_property_confidence must lie in [0.0, 1.0]",
            )
        if not 0.0 < self.min_dominance <= 1.0:
            raise ValueError(
                "min_dominance must lie in (0.0, 1.0]",
            )


class GlobalPatternMiner:
    """Aggregate per-property observations into universal rules.

    The miner is stateless and deterministic — same input,
    same output, no hidden side effects.

    Args:
        config: :class:`GlobalMinerConfig` — defaults to the
            advisory baseline if omitted.
    """

    def __init__(self, config: GlobalMinerConfig | None = None) -> None:
        self._config = config or GlobalMinerConfig()

    def mine(
        self,
        observations: Iterable[GlobalObservation],
    ) -> tuple[Sequence[GlobalPattern], GlobalPatternReport]:
        """Return the patterns plus a :class:`GlobalPatternReport`.

        The returned sequence is sorted by descending
        ``properties_count``, then by descending ``total_support``,
        then by ``scenario`` for tie-breaking — making the order
        stable across runs.
        """
        cfg = self._config
        considered = 0
        rejected_lowconf = 0

        # scenario -> action -> list[GlobalObservation]
        bucket: defaultdict[
            str, defaultdict[str, list[GlobalObservation]]
        ] = defaultdict(lambda: defaultdict(list))

        for obs in observations:
            if obs.confidence < cfg.min_property_confidence:
                rejected_lowconf += 1
                continue
            considered += 1
            bucket[obs.scenario][obs.dominant_action].append(obs)

        below_props = 0
        below_dom = 0
        emitted: list[GlobalPattern] = []

        for scenario, by_action in bucket.items():
            scenario_property_count = sum(
                len({o.property_id for o in obs_list})
                for obs_list in by_action.values()
            )
            if scenario_property_count == 0:
                continue
            top_action, top_obs_list = max(
                by_action.items(),
                key=lambda kv: (
                    len({o.property_id for o in kv[1]}),
                    sum(o.support for o in kv[1]),
                    kv[0],
                ),
            )
            top_props = {o.property_id for o in top_obs_list}
            top_tenants = {o.tenant_id for o in top_obs_list}
            if len(top_props) < cfg.min_properties:
                below_props += 1
                continue
            dominance = len(top_props) / scenario_property_count
            if dominance < cfg.min_dominance:
                below_dom += 1
                continue
            mean_conf = sum(o.confidence for o in top_obs_list) / len(
                top_obs_list,
            )
            emitted.append(
                GlobalPattern(
                    scenario=scenario,
                    action=top_action,
                    properties_count=len(top_props),
                    tenants_count=len(top_tenants),
                    total_support=sum(o.support for o in top_obs_list),
                    mean_confidence=mean_conf,
                    dominance=dominance,
                ),
            )

        emitted.sort(
            key=lambda p: (
                -p.properties_count,
                -p.total_support,
                p.scenario,
            ),
        )

        report = GlobalPatternReport(
            considered_observations=considered,
            rejected_low_confidence=rejected_lowconf,
            scenarios_seen=len(bucket),
            below_min_properties=below_props,
            below_dominance=below_dom,
            emitted_patterns=len(emitted),
        )
        return tuple(emitted), report
