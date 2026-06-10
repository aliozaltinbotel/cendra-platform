# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Sandbox v2 Workspace (conditional — read only when relevant)

**This section applies ONLY when at least one of the following is true:**
- The current branch is `sandbox-v2` (`git branch --show-current`)
- The user mentions any of: `sandbox-v2`, `/sandbox-v2`, `brain engine`, AG-UI SSE, `missing_info_detected`, `learning_decision`, `propertyChannelId`, `conv:*` Redis keys, OpenAI quota, Azure OpenAI fallback, GraphQL switch, `PropertyProfileStore`, or related sandbox v2 concepts.

**Otherwise, skip this entire section** and follow the rest of CLAUDE.md as normal repository guidance.

When the criteria above match:

1. **Read the living log first** — full sandbox v2 state lives there:
   `D:/botelbrainv2/botelui/docs/superpowers/SANDBOX_V2_LOG.md`
   Spec + plan in the same tree: `D:/botelbrainv2/botelui/docs/superpowers/{specs,plans}/`.

2. **Branch rule (same for all three repos in the workspace, never break):**
   - Active development branch: `sandbox-v2`
   - Flow: `sandbox-v2` → `dev` (one-way only)
   - **`dev → sandbox-v2` merge is FORBIDDEN** — done once on Apr 28 to sync pre-rule commits, never again.
   - Hotfixes also go through `sandbox-v2`.

3. **Workspace state:**
   ```bash
   cd D:/botelbrainv2/brainengine && git checkout sandbox-v2 && git pull
   ```

4. **Known traps (full Bug 1–5b stories in the log):**
   - AG-UI handler (`api_server/server.py:_run_agent_stream`) manages Redis history under `conv:{property_id}:{guest_id}` via the shared `conversation_memory` module — same store the Cendra adapter uses.
   - `state.property_id` arrives as the short `propertyChannelId` (e.g. `"323133"`), not a UUID. The brain engine no longer resolves UUIDs (the previous resolver was reverted; resolution is the UI's job now).
   - `state.org_id` is the Cendra workspace UUID, not Auth0 `org_*`.
   - SSE event casing is mixed: manual `_sse_event(...)` calls emit UPPERCASE, while `AGUIEmitter` uses snake_case enum values. The frontend normalises with `toLowerCase()` in onmessage; keep this in mind when adding new event types.
   - LLM calls now have an Azure OpenAI fallback (`2f950ed`); when the primary OpenAI key hits quota the request is automatically retried against Azure.

5. **Key code paths:**
   - `api_server/server.py` — AG-UI SSE handler (`_run_agent_stream`)
   - `brain_engine/api/conversation_memory.py` — shared Redis history helpers
   - `brain_engine/conversation/service.py` — `_maybe_emit_missing_info` + `_emit_learning_decision_for_fact` hooks (the source of sandbox v2's PM Chat events)
   - `brain_engine/streaming/event_types.py` — new event types (`MISSING_INFO_DETECTED`, `LEARNING_DECISION`)
   - `brain_engine/streaming/emit_helpers.py` — emit helpers
   - `brain_engine/integrations/unified_data/` — new GraphQL conversation read path
   - `brain_engine/profiles/postgres_store.py` — Postgres-backed `PropertyProfileStore` (cache survives pod restart)

6. **Endpoints / credentials:**
   - Dev cluster ingress: `https://brain-engine-dev.botel.ai`
   - Redis (Azure Cache, NOT cluster-internal): `rediss://bookly.redis.cache.windows.net:6380/1` (config `deploy/brain-engine-dev.yaml:20`)
   - OpenAI Key: `deploy/brain-engine-dev.yaml:43` (rotated; the old `...IJ0A` is no longer valid)
   - Azure OpenAI Key: `deploy/brain-engine-dev.yaml:152` (active fallback)

**Whenever you ship a sandbox v2 feature or bugfix, update `SANDBOX_V2_LOG.md`** — the log is the live state; spec and plan are immutable history.

---

## Repo Overview

Brain Engine — cognitive platform for autonomous property management. FastAPI HTTP service + AG-UI streaming protocol + multi-tier memory (Working / Episodic / Semantic + Knowledge Graph) + autonomous learning (Mem0 + SurpriseDetector + nightly consolidation).

- **Live (dev):** https://brain-engine-dev.botel.ai (Swagger UI at `/docs`)
- **Deploy target:** Azure Kubernetes Service, namespace `dev`

---

## Build and Development

```bash
# Local dev dependencies (Redis + Postgres + Qdrant)
docker compose up -d

# Tests
pytest tests/ -v

# Local API server
uvicorn api_server.server:app --reload --port 8000
```

Reference docs: `docs/API.md` (169-endpoint listing), `docs/ARCHITECTURE.md`.

---

## Coding Conventions

- Python 3.12, async-first
- Pydantic v2 models everywhere
- `litellm` for all LLM calls (auto-routes OpenAI / Azure OpenAI based on env)
- structlog for logging (`logger.bind(component=...)`)
- All persistence behind Protocol interfaces (Mem0, FactStore, PropertyProfileStore, etc.) — InMemory variants for tests, Postgres / Qdrant for production.
