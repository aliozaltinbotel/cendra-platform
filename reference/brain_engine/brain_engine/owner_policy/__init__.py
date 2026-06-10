"""Owner-policy DSL (Moat #2).

A typed DSL for tenants to pin Planner styles, jurisdictions, and
extra action denylists per owner.  The compiler emits
:class:`PlannerStyleSpec` records and a Protocol-compatible
:class:`DSLOwnerResolver` that plugs into the Planner layer
(Moat #4) without any other glue.

Public surface:

- :class:`PolicyDocument` / :class:`OwnerBlock` — AST nodes.
- :class:`OwnerPolicyParser` — Lark-backed parser.
- :class:`OwnerPolicyCompiler` / :class:`CompiledPolicy` — semantic
  pass that produces runtime artefacts.
- :class:`DSLOwnerResolver` — concrete
  :class:`brain_engine.planner.OwnerStyleResolver`.
- :func:`load_owner_policy` — one-shot helper that runs the full
  pipeline (parse → compile → register).
- :class:`OwnerPolicyError` / :class:`OwnerPolicyParseError` /
  :class:`OwnerPolicyCompileError` — error hierarchy.

Defensibility (Moat #2): typed DSL whose primitives map to runtime
constraint envelopes for a regulated-domain agent.  Patent +
paper + open-standard surface (MCP-trajectory: spec → SDKs →
adopters → IETF Internet-Draft).  Z3 pre/post-condition
compilation lives in :mod:`brain_engine.owner_policy.z3_compiler`
(M22) — it is the SMT-grade verifier the audit pipeline calls
when machine-checkable proof of a candidate (owner_id,
action_kind, jurisdiction) tuple is required.
"""

from __future__ import annotations

from brain_engine.owner_policy.ast import (
    OwnerBlock,
    PolicyDocument,
)
from brain_engine.owner_policy.compiler import (
    CompiledPolicy,
    OwnerPolicyCompiler,
    derived_style_id,
)
from brain_engine.owner_policy.errors import (
    OwnerPolicyCompileError,
    OwnerPolicyError,
    OwnerPolicyParseError,
)
from brain_engine.owner_policy.parser import OwnerPolicyParser
from brain_engine.owner_policy.registry import (
    DSLOwnerResolver,
    load_owner_policy,
)
from brain_engine.owner_policy.z3_compiler import (
    OwnerVerifyOutcome,
    OwnerVerifyResult,
    Z3OwnerPolicyVerifier,
)


__all__ = [
    "CompiledPolicy",
    "DSLOwnerResolver",
    "OwnerBlock",
    "OwnerPolicyCompileError",
    "OwnerPolicyCompiler",
    "OwnerPolicyError",
    "OwnerPolicyParseError",
    "OwnerPolicyParser",
    "OwnerVerifyOutcome",
    "OwnerVerifyResult",
    "PolicyDocument",
    "Z3OwnerPolicyVerifier",
    "derived_style_id",
    "load_owner_policy",
]
