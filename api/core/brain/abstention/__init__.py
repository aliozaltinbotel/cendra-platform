"""Bi-temporal Wilson + Conformal Abstention layer.

Combines three primitives Brain Engine already has into one runtime
gate that no published frontier system pairs in the same path:

- *Bi-temporal lifecycle* — :mod:`core.brain.patterns.postgres_rule_store`
  records ``valid_from`` / ``valid_to`` / ``invalid_at`` /
  ``deactivated_at`` / ``last_seen_at`` per rule.
- *Wilson lower bound* — :func:`core.brain.patterns.wilson.wilson_lower_bound`
  gives a calibrated lower bound on empirical success rates.
- *Conformal coverage check* — the alpha-quantile of confidences
  observed when a tool failed; below that level the model is in a
  region historically associated with errors.

Public surface:

- :class:`AbstentionVerdict` — three-valued result enum.
- :class:`CalibrationSample` — one historical observation.
- :class:`AbstentionDecision` — structured verdict carrying every
  input the audit log needs.
- :class:`CalibrationStore` Protocol +
  :class:`InMemoryCalibrationStore` default.
- :class:`ConformalCalibrator` — Wilson LB + conformal threshold
  derivations from a calibration store.
- :class:`AbstentionGate` — entry point used by the runtime.

Defensibility (Moat #1): the temporal-filter → Wilson LB →
conformal coverage gate sequence is the architectural fact a
USPTO Examples-47-49-fit independent claim is staked on.  Each
component has prior art in isolation; the integrated runtime gate
in a regulated-domain agent does not.
"""

from __future__ import annotations

from core.brain.abstention.calibrator import (
    DEFAULT_ALPHA,
    DEFAULT_MIN_SAMPLES,
    ConformalCalibrator,
)
from core.brain.abstention.gap_registry import (
    AGGREGATE_RUN_ID_CAP,
    GapRecord,
    GapStatus,
    GapStore,
    InMemoryGapStore,
    aggregate_gaps,
    build_gap_record,
    serialize_gap,
)
from core.brain.abstention.gate import (
    DEFAULT_WILSON_THRESHOLD,
    AbstentionGate,
)
from core.brain.abstention.mapie_calibrator import (
    DEFAULT_MAPIE_CONFORMITY_SCORE,
    MapieAbstainGate,
    MapieSplitConformalCalibrator,
)
from core.brain.abstention.models import (
    AbstentionDecision,
    AbstentionVerdict,
    CalibrationSample,
)
from core.brain.abstention.protocols import (
    DEFAULT_WINDOW_SIZE,
    CalibrationStore,
    InMemoryCalibrationStore,
)
from core.brain.abstention.split_conformal import (
    DEFAULT_ALPHA_CONFORMAL,
    DEFAULT_MIN_CALIBRATION,
    ConformalAbstainGate,
    ConformalAbstainResult,
    ConformalLabel,
    ConformalSet,
    NonConformityFn,
    SplitConformalCalibrator,
    binary_inverse_confidence,
    empirical_conformal_quantile,
)

__all__ = [
    "AGGREGATE_RUN_ID_CAP",
    "DEFAULT_ALPHA",
    "DEFAULT_ALPHA_CONFORMAL",
    "DEFAULT_MAPIE_CONFORMITY_SCORE",
    "DEFAULT_MIN_CALIBRATION",
    "DEFAULT_MIN_SAMPLES",
    "DEFAULT_WILSON_THRESHOLD",
    "DEFAULT_WINDOW_SIZE",
    "AbstentionDecision",
    "AbstentionGate",
    "AbstentionVerdict",
    "CalibrationSample",
    "CalibrationStore",
    "ConformalAbstainGate",
    "ConformalAbstainResult",
    "ConformalCalibrator",
    "ConformalLabel",
    "ConformalSet",
    "GapRecord",
    "GapStatus",
    "GapStore",
    "InMemoryCalibrationStore",
    "InMemoryGapStore",
    "MapieAbstainGate",
    "MapieSplitConformalCalibrator",
    "NonConformityFn",
    "SplitConformalCalibrator",
    "aggregate_gaps",
    "binary_inverse_confidence",
    "build_gap_record",
    "empirical_conformal_quantile",
    "serialize_gap",
]
