"""Causal-navigation subsystem (Gap #3).

Public surface grows across the three commits that close Gap #3.  Part
one shipped the value objects and inference rules.  Part two adds the
graph builder and the navigation service; part three wires those into
the API.
"""

from __future__ import annotations

from brain_engine.causal.errors import (
    CausalError,
    CausalInferenceError,
    CausalNavigationError,
)
from brain_engine.causal.graph import CausalGraphBuilder
from brain_engine.causal.inference import (
    CausalInferenceRule,
    ResolutionRule,
    SharedEntityRule,
    TemporalProximityRule,
)
from brain_engine.causal.models import (
    CausalChain,
    CausalEdge,
    CausalGraph,
    CausalKind,
    event_key,
)
from brain_engine.causal.service import CausalNavigationService

__all__ = [
    "CausalChain",
    "CausalEdge",
    "CausalError",
    "CausalGraph",
    "CausalGraphBuilder",
    "CausalInferenceError",
    "CausalInferenceRule",
    "CausalKind",
    "CausalNavigationError",
    "CausalNavigationService",
    "ResolutionRule",
    "SharedEntityRule",
    "TemporalProximityRule",
    "event_key",
]
