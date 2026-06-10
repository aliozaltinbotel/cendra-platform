"""Per-action EV / CVaR risk layer (Moat #9).

Every regulated AI action carries a loss distribution: a refund
might cost €0 (if accepted) or €350 (if charged back), a vendor
dispatch might run on time or slip the SLA, a discount might lift
a booking or train guests to demand cheaper rates next month.
This module turns those distributions into two numbers the audit
log records on every action:

- **EV** — expected (probability-weighted) loss.
- **CVaR_α** — Conditional Value-at-Risk at tail ``α``: the mean
  of the worst ``α`` fraction of outcomes.  Default ``α=0.05``
  (worst 5%).

The :class:`RiskGate` refuses to proceed when CVaR exceeds the
policy threshold, returning a :class:`RiskGateDecision` carrying
the full estimate plus a one-line rationale.

Defensibility (Moat #9): per-action worst-case loss bound for
regulated-domain agents.  Domain axis D — none of the 16
surveyed proptech competitors ships this (latest_research §2 row
D).  USPTO Examples-47-49-fit claim covers the ``OutcomeSample
→ EV/VaR/CVaR → policy threshold gate`` pipeline.

References (background, not training data):
    Rockafellar / Uryasev (2000).  *Optimization of conditional
    value-at-risk*.  Journal of Risk 2(3), 21–41.
"""

from __future__ import annotations

from core.brain.risk.cvar import (
    DEFAULT_ALPHA,
    compute_risk,
)
from core.brain.risk.gate import (
    DEFAULT_CVAR_THRESHOLD,
    DEFAULT_MIN_SAMPLES,
    RiskGate,
    RiskGateDecision,
)
from core.brain.risk.models import (
    OutcomeSample,
    RiskEstimate,
    RiskVerdict,
)

__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_CVAR_THRESHOLD",
    "DEFAULT_MIN_SAMPLES",
    "OutcomeSample",
    "RiskEstimate",
    "RiskGate",
    "RiskGateDecision",
    "RiskVerdict",
    "compute_risk",
]
