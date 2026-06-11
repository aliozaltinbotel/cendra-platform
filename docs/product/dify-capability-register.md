# Dify Capability Register

> **Owner:** Flow (Dify capability inventory lead)
> **Cross-links:** [Moat Fit Map](./moat-fit-map.md) · [Hospitality Productization Map](./hospitality-productization-map.md)
> **Last updated:** 2026-06-11
> **Purpose:** "Know the base product cold." A structured inventory of everything Dify provides, how Cendra currently uses each capability, and where extension/configuration hooks exist. This is the input that Atlas uses to write the Moat Fit Map's attachment-point column.

---

## 1. Workflow / Graphon

### What it does
Dify's visual workflow builder (codename Graphon in the fork) is a DAG-based orchestration canvas. Operators compose pipelines from typed nodes connected by data edges. Workflows execute server-side; the runtime resolves node order, propagates variable bindings, and handles branching and iteration.

### Node inventory (as of current branch)

| Category | Nodes | Notes |
|---|---|---|
| **LLM** | LLM, Chat, Completion | Multi-model; model selected per node |
| **Control flow** | If/Else, Switch, Loop/Iteration, Variable Aggregator | Loop supports array map |
| **Knowledge** | Knowledge Retrieval | Calls RAG pipeline; configurable top-k, rerank, filter |
| **Agents** | Agent node (legacy CoT/FC strategies), agent_v2 node | agent_v2 supports plugin-strategy adapter |
| **Code** | Code (Python/JS sandbox) | Arbitrary compute; no network in sandbox |
| **Template** | Template Transform (Jinja2) | String rendering from variables |
| **Data** | Parameter Extractor, Variable Assigner | Structured extraction from LLM output |
| **HTTP** | HTTP Request | Outbound webhooks and API calls |
| **Triggers** | Webhook trigger node, Schedule trigger node | Entry-point nodes only |
| **Human** | Human Input (HITL) | Pauses execution; resumes on human approval/input |
| **MCP** | Tool-call node via MCP | Both outbound (Dify calls MCP server) and inbound MCP server exposure |
| **Tools** | Tool node (marketplace tools) | Any installed plugin tool |
| **Knowledge index** | Knowledge Index node | Write path — document ingestion |
| **Document** | Document Extractor | Parses uploaded files into text |
| **Media** | Image analysis, Audio/Video tools | Via vision-capable models |

### How Cendra currently uses it
Workflow canvas is the backbone for all automation flows. Currently used for standard guest communication workflows. Brain gate invocation is the primary extension target (not yet wired per G1 exit criteria).

### Extension / config points
- Custom node types via plugin SDK (Python)
- Variable schemas are typed and validated at edge connection time
- Node-level model override, timeout, retry policy
- Workflow-level secrets via environment variables
- Import/export as DSL (YAML) — `difyctl run app` consumes DSL

---

## 2. Agent Strategies & agent_v2

### What it does
Dify supports two agent execution models:

**Legacy agents** (CoT + Function Calling runners): `cot_agent_runner.py`, `fc_agent_runner.py` — loop over tool calls using a fixed ReAct or FC strategy.

**agent_v2** (plugin-strategy adapter): The `plugin_strategy_adapter.py` in `api/core/workflow/nodes/agent/` delegates strategy execution to any installed agent-strategy plugin. This decouples the loop logic from the core runner and allows third-party or Cendra-owned strategy plugins to control how the agent reasons and acts.

### How Cendra currently uses it
Not yet customized; using base CoT strategy. agent_v2 with a Brain-backed strategy plugin is the architectural path for earned autonomy (gate chain invocation from within the agent loop).

### Extension / config points
- Agent strategy plugins (install via plugin marketplace or local)
- Tool set configured per agent node
- Max iterations, timeout configurable
- System prompt injected per workflow run context

---

## 3. RAG Pipeline & Knowledge Bases

### What it does
Full RAG infrastructure under `api/core/rag/`:

| Component | Location | What it does |
|---|---|---|
| **Ingestion pipeline** | `index_processor/`, `extractor/`, `splitter/` | Document parsing, chunking, embedding, storage |
| **Retrieval** | `retrieval/` | Top-k semantic search + keyword hybrid |
| **Reranking** | `rerank/` | Cross-encoder reranking before knowledge node output |
| **Data post-processing** | `data_post_processor/` | Score thresholding, dedup, citation injection |
| **Knowledge bases** | Dify console UI | Named collections of indexed documents |
| **Datasource connectors** | `datasource/` | Notion, web crawl, file upload, custom connectors |

### How Cendra currently uses it
Knowledge bases for property documentation, house rules, and local area guides. Ingestion and retrieval are fully inherited from Dify with no Cendra customization.

### Extension / config points
- Embedding model selectable per knowledge base
- Vector backend selectable (see §4)
- Chunking strategy (fixed size, paragraph, custom separator)
- Retrieval mode: semantic / keyword / hybrid
- Reranker model selectable
- Metadata filter expressions on retrieval
- Custom datasource plugins

---

## 4. Vector Backends

### What it does
Dify abstracts vector storage behind a provider interface. Supported backends (via `api/core/rag/datasource/`):

Weaviate, Qdrant, Milvus, pgvector (PostgreSQL), Chroma, OpenSearch, Elasticsearch, Tidb Vector, Oracle, Tencent Vector, Baidu, MyScale, OceanBase, Lindorm, Couchbase, Vikingdb, Upstash, and others.

### How Cendra currently uses it
Weaviate (`docs/weaviate/`) is the configured backend. No custom adapter.

### Extension / config points
- Backend selected via environment variable; switchable without code changes
- Custom vector backend plugin possible via plugin SDK

---

## 5. Plugin System

### Architecture
Four plugin types, all loaded via the plugin daemon (`api/core/plugin/`):

| Type | What it provides |
|---|---|
| **Tool** | Callable tool for agent or workflow Tool node |
| **Model** | A new LLM, embedding, rerank, or TTS provider |
| **Extension** | HTTP webhook extension for external integration |
| **Agent Strategy** | Custom agent loop logic (used by agent_v2) |

### Marketplace
Dify's plugin marketplace hosts community and vendor plugins. Cendra can publish private plugins (hosted or bundled).

### How Cendra currently uses it
No custom plugins deployed yet. Brain gate strategy plugin is the primary planned artifact (Forge owns).

### Extension / config points
- Plugin SDK: Python, publishes a manifest + implementation
- Plugins can call back into Dify APIs (backwards invocation at `api/core/plugin/backwards_invocation/`)
- Plugin endpoints exposed as HTTP (for extension type)
- Plugin marketplace publishable via `difyctl`

---

## 6. Triggers & Channels

### What it does
Dify exposes workflows and agents through multiple channels:

| Trigger / Channel | Mechanism | Cendra relevance |
|---|---|---|
| **Webhook trigger** | HTTP POST wakes a workflow | Inbound PMS events, channel manager callbacks |
| **Schedule trigger** | Cron-based workflow execution | Nightly analytics, digest generation |
| **Chat interface** | Streaming SSE chat | Guest-facing chat; Cendra ops console |
| **Completion API** | Single-shot prompt-response | Internal tools |
| **Agent API** | Multi-turn agent session | Operator assistant |
| **Workflow API** | Structured input → output | Programmatic orchestration from Brain Engine |
| **Voice** | TTS/STT via model plugins | Future guest voice channel |
| **Email / Slack** | Via extension plugins | PMS notification relay |

### How Cendra currently uses it
Webhook trigger for PMS event ingestion. Chat interface for guest-facing touchpoints. Workflow API for Brain Engine→Dify handoff.

### Extension / config points
- Webhook shared secret, payload schema, response mapping
- Schedule: cron expression + timezone
- API key scoped per app or workspace

---

## 7. Model Runtime

### What it does
Model runtime (`api/core/model_manager.py`, `api/core/provider_manager.py`) abstracts LLM/embedding/rerank/TTS/STT calls. Supports 100+ models via provider plugins (OpenAI, Anthropic, Google, HuggingFace, Azure, local Ollama, etc.).

### How Cendra currently uses it
No provider restrictions set. Default model config per workflow. Model selection is a deployment-time concern, not a product differentiator.

### Extension / config points
- Model credentials per provider (encrypted at rest in Dify DB)
- Load balancing across multiple keys for a single provider
- Fallback model chain configurable
- Custom model plugin (adds an entirely new provider)
- Per-node model override in workflows

---

## 8. Human Input (HITL)

### What it does
The Human Input node pauses workflow execution and surfaces a structured form to a named operator or any online operator. Execution resumes when the human submits or approves. Backed by a queue; supports timeout and escalation paths.

### How Cendra currently uses it
Not yet wired to Brain gate output. Planned: when Brain gate returns `REVIEW_REQUIRED`, the workflow emits a HITL node populated with the gate's reasoning and uncertainty score.

### Extension / config points
- Form schema defined per node (free text, select, approval boolean)
- Assignee: specific user, role, or "any available"
- Timeout and default-on-timeout configurable
- Webhook callback on completion (for external notification)

---

## 9. MCP (Model Context Protocol)

### What it does
Dify supports MCP in both directions:

| Direction | Implementation | What it enables |
|---|---|---|
| **Outbound (Dify as client)** | `api/core/mcp/client/` + `mcp_client.py` | Dify workflows can call any external MCP server as a tool source |
| **Inbound (Dify as server)** | `api/core/mcp/server/` | Dify exposes its own tools/resources over MCP, so external agents (Claude Code, etc.) can call Dify apps |

Auth: OAuth / token-based (`api/core/mcp/auth/`).

### How Cendra currently uses it
MCP client active for connecting PMS adapters that expose MCP endpoints. Inbound MCP server planned for Brain Engine to call back into Dify workflow APIs from external agent runtimes.

### Extension / config points
- MCP server URL + auth configurable per tool plugin
- Inbound server: scope, resource exposure, session management
- Session persistence in `api/core/mcp/session/`

---

## 10. Observability / LLMOps

### What it does
`api/core/ops/` integrates with external LLMOps platforms:

- LangSmith, Langfuse, LangWatch, Opik, Arize Phoenix, Weave (W&B)
- Per-app tracing toggle; traces include token counts, latency, node outputs, tool calls

Dify also has internal telemetry: `api/enterprise/telemetry/DATA_DICTIONARY.md` describes the event schema used for internal usage analytics.

### How Cendra currently uses it
Langfuse wired for trace visibility. Brain Engine outcome ledger is a separate, additive layer — not replacing Dify observability.

### Extension / config points
- LLMOps provider selectable per workspace
- Trace sampling rate
- Custom trace fields via metadata injection in workflows

---

## 11. BaaS APIs

### What it does
Dify exposes a full REST API surface for programmatic control:

| API group | What it covers |
|---|---|
| App / Workflow execution | Run workflows, stream responses, get run status |
| Knowledge base | CRUD documents, trigger re-index, query |
| Agent sessions | Multi-turn conversation management |
| Files | Upload, reference in workflow inputs |
| Annotations | Human-labeled Q&A for retrieval augmentation |
| Workspace / App management | Create/update apps, manage members |
| Model credentials | Provider config |

CLI: `difyctl` exposes a subset for agent and scripting use (auth, app list/describe/run, DSL import/export, HITL protocol).

### How Cendra currently uses it
Brain Engine calls Dify Workflow API to trigger execution. `difyctl` used in CI for DSL import/export validation.

---

## 12. Console Surfaces

These are what operators would see if we exposed raw Dify instead of the Cendra product console:

| Surface | What it is | Cendra posture |
|---|---|---|
| **Studio** | Workflow/agent builder canvas | Hide from operators; reserved for Cendra internal config |
| **Explore** | App marketplace browser | Expose curated templates only via Cendra wrapper |
| **Knowledge** | Knowledge base CRUD | Exposed as "Property Knowledge" in Cendra console |
| **Tools** | Plugin tool browser | Not exposed to operators |
| **Monitoring** | LLMOps dashboard, annotation UI | Internal ops only |
| **Settings** | Workspace, model credentials, team members | Internal ops only; operators managed via Cendra RBAC |

> **License note (Forge must sign off):** Dify's console chrome (logos, nav, branded components) falls under the LangGenius license. Renaming or hiding Dify-branded elements requires the commercial agreement. The table above describes what to *expose or hide from operators*, not what to rebrand at the HTML level. See [Hospitality Productization Map](./hospitality-productization-map.md) §License-Safe Scoping.

---

*This document is the "know the base product cold" inventory. It feeds the attachment-point column of the [Moat Fit Map](./moat-fit-map.md). Update it when a new Dify upstream capability is pulled in via rebase.*
