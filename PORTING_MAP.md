# PORTING_MAP.md — Brain Engine → cendra-platform

Source of truth for what moves where, in which batch, and its status. Source paths are relative to `reference/brain_engine/brain_engine/`; targets relative to repo root. Update the **Status** column (`TODO / IN PROGRESS / PORTED / DEFERRED / RETIRED`) in the same PR that changes it. Conversion rules live in `CLAUDE.md` → Porting checklist.

## Batch 1 — Pure kernels (no DB, no runtime wiring)

| Source | Target | Notes | Status |
|---|---|---|---|
| `patterns/wilson.py` | `api/core/brain/patterns/wilson.py` | Pure math | TODO |
| `abstention/` (models, calibrator, split_conformal, protocols, gate) | `api/core/brain/abstention/` | Pure; `mapie_calibrator.py` only if `uv add mapie` is clean, else DEFERRED | TODO |
| `certificates/` (tier, cert, policy, issuer, verifier) | `api/core/brain/certificates/` | Pure; HMAC key via dify_config/secret | TODO |
| `cognition_loops/{models,protocol,critic,friction}.py` | `api/core/brain/cognition/` | Pure; ACE×Memory-R1 protocol + Reflexion friction | TODO |
| `epistemic/{models,promotion}.py` + in-memory store | `api/core/brain/epistemic/` | Postgres store is Batch 2 | TODO |

## Batch 2 — Stores, models, migrations (runtime still untouched)

| Source | Target | Notes | Status |
|---|---|---|---|
| DecisionCase store (`bootstrap/decision_case.py` + store impls) | `api/models/brain_decision.py` + `api/core/brain/patterns/case_store.py` | Tenant-scoped; SQLAlchemy rewrite | TODO |
| `patterns/postgres_rule_store.py` + `temporal_resolver.py` | `api/models/brain_rules.py` + `api/core/brain/patterns/` | Bi-temporal fields verbatim (`valid_from…last_seen_at`) | TODO |
| Pattern miner + `patterns/ml_synthesizer.py` | `api/core/brain/patterns/miner.py` | `scikit-learn` dep, flag-gated like reference | TODO |
| `blockers/` | `api/core/brain/patterns/blockers.py` + model | | TODO |
| `epistemic/store.py` (Postgres) | `api/models/brain_epistemic.py` + store | Observation/Belief bi-temporal | TODO |
| `autonomy/` (engine, gate, trust_meter, metrics_collector, postgres_store) | `api/core/brain/autonomy/` + `api/models/brain_autonomy.py` | **`workflow_kinds.py` enum → per-tenant registry table** (vertical-agnostic requirement) | TODO |
| `approval/` (gateway, confidence_router) | `api/core/brain/autonomy/approval.py` | Maps verdicts → Dify Human Input (wiring in Batch 4) | TODO |

## Batch 3 — Memory tiers

| Source | Target | Notes | Status |
|---|---|---|---|
| `memory/working_memory.py`, `episodic_memory.py`, `episodic_dedup.py` | `api/core/brain/memory/` | Redis via Dify's redis extension; keep `conv:{property}:{guest}` keyspace for migration | TODO |
| `memory/semantic_memory.py`, `embedding_config.py` | `api/core/brain/memory/semantic.py` | Reuse existing Qdrant collections via Dify qdrant client (`core/rag/datasource/vdb`) | TODO |
| `memory/hybrid_search.py` (BM25 sparse + RRF) | `api/core/brain/memory/hybrid.py` | `fastembed` dep; flag `BRAIN_HYBRID_RETRIEVAL_ENABLED`; Qdrant named sparse vectors (T8 config) | TODO |
| `memory/knowledge_graph.py`, `kg_as_of.py` | `api/core/brain/memory/kg.py` | pgvector backend exists in Dify | TODO |
| `memory/surprise_detector.py`, `memory_consolidator.py`, `recency_decay.py`, `contradiction_detector.py` | `api/core/brain/memory/` + `api/tasks/brain_consolidation.py` | Consolidator → Celery beat (T5, Batch 4/5) | TODO |
| `memory/mem0_extractor.py` | — | EVALUATE: keep `mem0ai` dep or replace with llm_generator extraction | TODO |

## Batch 4 — Runtime wiring (touchpoints T1–T3, T6–T8)

| Item | Target | Notes | Status |
|---|---|---|---|
| Gate chain composition (`decision_pipeline/adapter.py`) | `api/core/brain/gates.py` | Sync interface: compliance → certificate → abstention → policy/risk; short-circuit semantics preserved | TODO |
| T1+T2 node_runtime/node_factory hooks | per CLAUDE.md | `CENDRA-HOOK` markers + ledger | TODO |
| T3 agent_v2 context+gate injection | per CLAUDE.md | Follow `plugin_strategy_adapter.py` pattern | TODO |
| T6 brain memory as retrieval source | per CLAUDE.md | Or zero-edit via external-knowledge loopback — decide here | TODO |
| T7 DecisionCase capture on run events | per CLAUDE.md | Idempotent ingest, conversation id join key | TODO |
| T8 docker/env | per CLAUDE.md | C/T entries in ledger | TODO |

## Batch 5 — Policy, compliance, scheduled jobs, public API

| Source | Target | Notes | Status |
|---|---|---|---|
| `owner_policy/` (grammar.lark, parser, ast, compiler, z3_compiler, registry) | `api/core/brain/policy/` | `lark`, `z3-solver` deps; registry → SQLAlchemy; keep `internet-drafts/` spec in `docs/` | TODO |
| `compliance/` (art12, art50, pii_detector, redactor, consent, retention, never_ai_denylist, encryption) | `api/core/brain/compliance/` + T4 moderation module | Key Vault → pluggable secret provider | TODO |
| T5 beat schedule entries | `api/extensions/ext_celery.py` | consolidation, mining, autonomy eval, friction decay | TODO |
| TrustMeter / policy / audit endpoints | `api/services/brain_*.py` + `api/controllers/{console,service_api}/brain/` | API-first, before any UI | TODO |

## Batch 6 — Learning live, console, packs

| Source | Target | Notes | Status |
|---|---|---|---|
| `cognition_loops/{policy,trainer,sleep}.py` (+ `grpo.py` non-prod) | `api/core/brain/cognition/` + `api/tasks/brain_sleep.py` | Wire ACE/Memory-R1 inputs from message/agent events | TODO |
| `continual_learning/` (recorder, grader, monthly_evaluator, skill_evolution, sop_parser) | `api/core/brain/cognition/continual/` | Beat-scheduled | TODO |
| TrustMeter + policy editor + audit viewer UI | `web/**/brain/` | After service_api stable | TODO |
| 482-scenario foundation doc + DSL vocab + tier defaults + workflow templates | `packs/hospitality/` | Content restructuring; seeds the WorkflowKind registry | TODO |

## Batch 7 (optional/deferred)

| Source | Disposition |
|---|---|
| `htn/`, `behavior_trees/`, `property_twin/` | Port-and-park: pure code ports cheaply; runtime integration deferred until a workflow need exists; RSSM/GRPO training stays out of production claims |
| `causal/`, `negotiation/`, `guest_intelligence/`, `upsell/` | Re-evaluate per design-partner demand; candidates for pack-level features |

## Retired (do NOT port — Dify replaces these)

`api_server/` (FastAPI app, AG-UI SSE, static test UI) · `conversation/service.py` pipeline shell · `prompt_assembler/` · `models/azure_routing.py` + litellm (→ `core/model_runtime`) · channel bootstraps: telegram/whatsapp/voice/elevenlabs (→ Dify plugins + trigger nodes) · `streaming/` · `mcp_client/` (→ Dify MCP) · `checkpointer/`, `channels/`, `pregel/`, `graph/` (→ Graphon) · deploy manifests (→ `docker/` + helm).

## Open decisions (resolve before the relevant batch)

1. **T6 mechanism**: in-tree retrieval source vs. zero-edit external-knowledge loopback — decide in Batch 4 on latency + rebase-surface grounds.
2. **mem0ai dep**: keep or replace (Batch 3).
3. **Embedding weight**: `sentence-transformers` in the api image vs. a sidecar embedding server (Batch 3, image-size driven).
4. **LangGenius license**: commercial terms vs. single-workspace-per-operator — Phase-0 gate, blocks nothing in Batches 1–2.
