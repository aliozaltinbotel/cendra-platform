"""Virtual node constants for graph entry and exit points.

``START`` and ``END`` are sentinel strings used as source/target in
``add_edge`` calls to mark where execution begins and terminates.
"""

from __future__ import annotations

START: str = "__start__"
"""Virtual entry node — edges from START define the graph's entry point."""

END: str = "__end__"
"""Virtual exit node — edges to END mark terminal states."""
