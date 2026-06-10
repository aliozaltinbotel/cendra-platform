# CENDRA-HOOK(T3): gate + brain memory injection for the agent_v2 loop.
"""Cendra brain layer for workflow agent runs (touchpoint T3).

Thin adapter between the agent node and the brain kernel — imports
brain, never the reverse (FORK_LEDGER.md).  Batch 4 scope:

- :func:`gate_agent_run` evaluates the kernel gate chain before the
  agent backend is invoked, under the same ``BRAIN_GATES_MODE`` /
  ``BRAIN_GATES_TENANTS`` rollout switches as T1 (off by default —
  upstream-identical).  In enforce mode a non-PROCEED verdict refuses
  the run with the gate's rationale.
- :func:`record_agent_run` appends the run to the tenant's episodic
  memory tier so consolidation evidence accumulates while a workspace
  observes.

Prompt-side memory *context injection* (working/semantic recall into
the agent request) activates with the per-tenant store wiring in
Batch 5 — recorded in PORTING_MAP; this module is where it lands.
"""

from __future__ import annotations

import logging
import threading

from core.brain.memory.episodic_memory import EpisodicMemory
from core.brain.runtime_gateway import evaluate_tool_dispatch

__all__ = ["gate_agent_run", "record_agent_run"]

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_episodic_by_tenant: dict[str, EpisodicMemory] = {}


def gate_agent_run(
    *,
    tenant_id: str,
    app_id: str,
    agent_id: str,
    conversation_id: str | None = None,
) -> str | None:
    """Run the gate chain for one agent run.

    Returns the refusal rationale when an enforced gate refuses the
    run; ``None`` means proceed (gating off, observe mode, or all
    gates passed).
    """
    decision = evaluate_tool_dispatch(
        tenant_id=tenant_id,
        app_id=app_id,
        tool_id=f"agent:{agent_id}",
        conversation_id=conversation_id,
    )
    if decision is not None and decision.verdict.value != "proceed":
        return f"refused by Cendra brain gates ({decision.verdict.value}): {decision.rationale}"
    return None


def record_agent_run(
    *,
    tenant_id: str,
    app_id: str,
    agent_id: str,
    workflow_run_id: str,
    status: str,
) -> None:
    """Append one agent run to the tenant's episodic memory tier.

    Best-effort: memory failures must never affect the run itself.
    """
    if not tenant_id:
        return
    try:
        with _lock:
            memory = _episodic_by_tenant.get(tenant_id)
            if memory is None:
                memory = EpisodicMemory(session_id=f"tenant:{tenant_id}")
                _episodic_by_tenant[tenant_id] = memory
        memory.add_episode(
            "agent_run",
            f"agent {agent_id} ran for app {app_id} ({status})",
            metadata={
                "app_id": app_id,
                "agent_id": agent_id,
                "workflow_run_id": workflow_run_id,
                "status": status,
            },
        )
    except Exception:
        logger.exception("brain episodic record failed (ignored)")
