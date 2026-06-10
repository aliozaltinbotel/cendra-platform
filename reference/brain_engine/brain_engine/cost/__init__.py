"""Per-booking cost forecasting.

Reference: ``brain_engine_advisory.md`` §10.3.

The engine emits a :class:`CostEstimate` ahead of any work so the
caller can:

* surface a deterministic price to the customer pricing model;
* size capacity for the next 24 h based on expected booking mix;
* trip an anomaly detector when the *actual* spend exceeds 3× the
  predicted ceiling — typically a runaway loop or an injection
  attempting to harvest model time.
"""

from brain_engine.cost.booking_cost_predictor import (
    BookingFeatures,
    CostEstimate,
    CostModel,
    LinearCostModel,
    PropertyType,
)

__all__ = [
    "BookingFeatures",
    "CostEstimate",
    "CostModel",
    "LinearCostModel",
    "PropertyType",
]
