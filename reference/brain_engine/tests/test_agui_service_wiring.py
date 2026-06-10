"""Tests that ConversationService receives R2 + R3 deps on the AG-UI path.

R2 (PR #308) added the ``owner_profile_store`` constructor argument
to :class:`ConversationService` so the conversation pipeline can
surface owner flexibility rules (amenity carve-outs, fee rules,
check-in policies, local recommendations) in the system prompt.
R3 (PR #307) added the ``guardrail_pipeline`` constructor argument
so the AG-UI path validates LLM-drafted replies before they reach
the SSE bridge (Format, Lexical, Repeat, RepeatQuestion,
Contradiction, Hallucination tiers).

Both PRs shipped the surface but left the runtime wiring to a
follow-up — the ``ConversationService(...)`` call inside
:func:`api_server.server._run_agent_stream` did not pass either
argument, so the new behaviour was inert on dev.  This module
pins the follow-up wiring:

* ``_run_agent_stream`` constructs ``ConversationService`` with
  ``owner_profile_store=_owner_profile_store`` (the same instance
  the orchestrator's preference tier already uses).
* ``_run_agent_stream`` constructs ``ConversationService`` with
  ``guardrail_pipeline=_full_system.guardrails`` (the same
  pipeline the Cendra adapter uses).
* The wiring is guarded against a pre-readiness ``_full_system``
  (lifespan still initialising) so a startup race cannot raise on
  the first request.

The tests inspect the bytecode constants and source text rather
than running the AG-UI handler end-to-end — the handler depends
on Redis / Qdrant / Postgres that are out of scope for a unit
test, but the wiring contract is a string-level guarantee.
"""

from __future__ import annotations

import inspect

import api_server.server as server_module


def test_run_agent_stream_passes_owner_profile_store() -> None:
    """The AG-UI handler must forward the module-level
    ``_owner_profile_store`` into ``ConversationService(...)`` so
    R2's owner-flexibility surface fires on the live path."""
    source = inspect.getsource(server_module._run_agent_stream)
    assert "owner_profile_store=_owner_profile_store" in source


def test_run_agent_stream_passes_guardrail_pipeline() -> None:
    """The AG-UI handler must forward ``_full_system.guardrails``
    into ``ConversationService(...)`` so R3's response-validation
    step fires on the live path."""
    source = inspect.getsource(server_module._run_agent_stream)
    assert "guardrail_pipeline=" in source
    assert "_full_system.guardrails" in source


def test_run_agent_stream_passes_memory_system() -> None:
    """The AG-UI handler must forward ``_full_system.memory`` into
    ``ConversationService(...)``.  The fan-out already WRITES every
    guest turn to episodic / semantic / KG; without ``memory_system``
    the service had no read handle so ``_load_memory_context``
    short-circuited and the guest agent could never recall a stored
    fact.  Pin the read-side wiring."""
    source = inspect.getsource(server_module._run_agent_stream)
    assert "memory_system=" in source
    assert "_full_system.memory" in source


def test_run_agent_stream_guards_pre_readiness_full_system() -> None:
    """``_full_system`` is initialised in the lifespan startup hook;
    a pre-readiness request must not raise.  Pin the ``None`` guard
    so a future refactor cannot accidentally drop it."""
    source = inspect.getsource(server_module._run_agent_stream)
    assert "_full_system is not None" in source


def test_module_level_stores_are_already_initialized() -> None:
    """Both globals must be in place at module import time so the
    AG-UI handler can read them without an additional lifespan
    handshake.  ``_owner_profile_store`` falls back to the in-memory
    implementation for tests / dev; ``_full_system`` starts as
    ``None`` and lifespan replaces it on startup."""
    assert hasattr(server_module, "_owner_profile_store")
    assert server_module._owner_profile_store is not None
    assert hasattr(server_module, "_full_system")
