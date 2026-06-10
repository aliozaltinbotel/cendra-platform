"""Alternative property finder tool.

V1 demo scope: there is no GraphQL-sourced multi-property search yet
— the unified ``properties`` query covers only the active tenant and
does not expose availability filtering.  Until that surface lands the
tool deliberately defers instead of falling back to the PMS adapter
or mockup data, both of which produced fabricated answers in the
2026-04-28 demo.
"""

from __future__ import annotations

import logging

from brain_engine.tools.decorator import tool
from brain_engine.tools.runtime import ToolRuntime

logger = logging.getLogger(__name__)


_DEFER_MESSAGE = (
    "Cannot search alternative properties yet. Tell the guest you "
    "will look at the available options and get back to them shortly."
)


@tool(description=(
    "Find alternative properties when the current one is unavailable "
    "or the guest explicitly asks for alternatives. "
    "Currently returns a deferral so the agent never improvises "
    "alternatives — wait for the GraphQL multi-property availability "
    "search to land before relying on a positive answer."
))
async def alternative_property_finder(
    reason: str = "unavailable",
    check_in_date: str = "",
    check_out_date: str = "",
    guests: int = 1,
    runtime: ToolRuntime | None = None,
) -> str:
    """Return a deferral until GraphQL multi-property search ships.

    Args:
        reason: Why alternatives are needed (kept for log context).
        check_in_date: Desired check-in (kept for log context).
        check_out_date: Desired check-out (kept for log context).
        guests: Guest count (kept for log context).
        runtime: Injected runtime context.

    Returns:
        Static deferral message — agents must surface it verbatim.
    """
    del reason, check_in_date, check_out_date, guests, runtime
    return _DEFER_MESSAGE
