"""A/B testing framework for skills and prompt variants.

See ``brain_engine_advisory.md`` §10.2.  Three pure-compute
modules sit behind this package:

* :mod:`brain_engine.experiments.traffic_splitter` — deterministic
  hash-based assignment, repeatable across processes.
* :mod:`brain_engine.experiments.statistical_significance` —
  two-proportion z-test plus a sequential SPRT-lite verdict.
* :mod:`brain_engine.experiments.ab_test_engine` — the experiment
  registry and the verdict pipeline that ties splitter + stats
  together.

Nothing here performs I/O or async work; experiment durability
lives one tier up in the runtime.
"""

from __future__ import annotations

from brain_engine.experiments.ab_test_engine import (
    Experiment,
    ExperimentRegistry,
    ExperimentVerdict,
    Variant,
    VariantOutcome,
)
from brain_engine.experiments.statistical_significance import (
    SignificanceResult,
    two_proportion_z_test,
)
from brain_engine.experiments.traffic_splitter import (
    DeterministicTrafficSplitter,
    SplitDecision,
    TrafficSplit,
)

__all__ = [
    "DeterministicTrafficSplitter",
    "Experiment",
    "ExperimentRegistry",
    "ExperimentVerdict",
    "SignificanceResult",
    "SplitDecision",
    "TrafficSplit",
    "Variant",
    "VariantOutcome",
    "two_proportion_z_test",
]
