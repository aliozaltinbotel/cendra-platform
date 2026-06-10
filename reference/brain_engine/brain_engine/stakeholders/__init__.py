"""Multi-stakeholder negotiation layer (Moat #6).

A Pareto / Nash-bargaining solver across the five default
stakeholder roles in an STR decision: guest / owner / cleaner /
neighbor / regulator.  Each stakeholder is represented by a
:class:`UtilityFunction`; the engine searches the Pareto frontier
of candidate actions and applies the chosen
:class:`BargainingSolution` (Nash / egalitarian / utilitarian).

This module is *complementary* to
:mod:`brain_engine.negotiation` — the latter drives concrete
multi-round bargain conversations with one counterparty (cleaner,
vendor); :mod:`brain_engine.stakeholders` is the abstract
multi-utility solver that selects an action across many
stakeholders simultaneously.

Public surface:

- :class:`StakeholderId` — five-role enum (extensible by PR).
- :class:`BargainingSolution` — solution concept enum.
- :class:`ActionCandidate` — one action under consideration.
- :class:`NegotiationOutcome` — structured result + audit trail.
- :class:`UtilityFunction` Protocol +
  :class:`LinearUtilityFunction` default.
- :class:`UtilityRoster` — per-stakeholder bundle.
- :class:`StakeholderNegotiationEngine` — entry point.
- :func:`pareto_frontier` / :func:`dominates` — pure-Python
  helpers exposed for callers wanting to skip the full engine
  when only the frontier is needed.

Defensibility (Moat #6): multi-stakeholder utility-bargaining
runtime for regulated-domain agents.  None of the 16 surveyed
proptech competitors ships this (latest_research §2 row C).
USPTO system-claim covers the hard-veto filter +
Pareto-frontier search + bargaining-solution selector pipeline.
"""

from __future__ import annotations

from brain_engine.stakeholders.engine import (
    StakeholderNegotiationEngine,
)
from brain_engine.stakeholders.models import (
    DEFAULT_STAKEHOLDER_PRIORITIES,
    ActionCandidate,
    BargainingSolution,
    NegotiationOutcome,
    StakeholderId,
)
from brain_engine.stakeholders.pareto import (
    dominates,
    pareto_frontier,
)
from brain_engine.stakeholders.utility import (
    DEFAULT_UTILITY_FLOOR,
    LinearUtilityFunction,
    UtilityFunction,
    UtilityRoster,
)


__all__ = [
    "ActionCandidate",
    "BargainingSolution",
    "DEFAULT_STAKEHOLDER_PRIORITIES",
    "DEFAULT_UTILITY_FLOOR",
    "LinearUtilityFunction",
    "NegotiationOutcome",
    "StakeholderId",
    "StakeholderNegotiationEngine",
    "UtilityFunction",
    "UtilityRoster",
    "dominates",
    "pareto_frontier",
]
