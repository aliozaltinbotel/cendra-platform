# Dify Capability Register

> **Owner:** Flow (Dify capability inventory lead)
> **Cross-links:** [Moat Fit Map](./moat-fit-map.md) · [Hospitality Productization Map](./hospitality-productization-map.md)
> **Last updated:** 2026-06-11 (bottom-up accuracy pass — every node/plugin/API claim below is verified against code on `main`; divergences from the top-down draft are noted inline)
> **Purpose:** "Know the base product cold." A structured inventory of everything Dify provides, how Cendra currently uses each capability, and where extension/configuration hooks exist. This is the input that Atlas uses to write the Moat Fit Map's attachment-point column.

> **Verification note:** This fork has extracted the workflow engine into a standalone **`graphon`** package and split vector backends and trace providers into separately-installed workspace packages. Several claims in the original top-down draft assumed monolithic upstream layout and were corrected. Attachment-point changes that affect the Moat Fit Map are flagged with **⚠ MOAT** and were reported in a comment on CEN-5.

---

## 1. Workflow / Graphon

### What it does
Dify's visual workflow builder is a DAG-based orchestration canvas. Operators compose pipelines from typed nodes connected by data edges. Workflows execute server-side; the runtime resolves node order, propagates variable bindings, and handles branching and iteration.

**Graphon is a real extracted Python package in this fork, not just a codename.** The engine, the built-in node implementations, and the node-type enum live in the installed `graphon` distribution (`graphon.enums`, `graphon.nodes.*`, `graphon.entities.*`). The Dify API consumes it via `api/core/workflow/node_factory.py`, which wires Dify-specific runtime services (model access, file managers, tool runtime, human-input adapter) into Graphon nodes. **⚠ MOAT:** the engine is a dependency boundary, not in-tree source — extension happens at the `node_factory` seam and through plugin categories, not by editing engine internals.

### Node inventory (verified against `graphon.enums.BuiltinNodeTypes` + `api/core/workflow/nodes/`)

**Built-in nodes (defined in the `graphon` package — `graphon/nodes/`):**

| Category | Built-in node types (enum value) |
|---|---|
| **Entry / response** | `start`, `end`, `answer` |
| **LLM** | `llm` (single node; model selected per node) |
| **Control flow** | `if-else`, `loop` (+ `loop-start`/`loop-end`), `iteration` (+ `iteration-start`) |
| **Routing / classification** | `question-classifier` |
| **Data shaping** | `variable-aggregator`, `variable-assigner` (legacy) / `assigner` (v2), `parameter-extractor`, `list-operator` |
| **Code / template** | `code` (Python/JS sandbox), `template-transform` (Jinja2) |
| **HTTP** | `http-request` |
| **Tools** | `tool` (any installed plugin tool — this is also how MCP tools and media/vision tools surface) |
| **Knowledge** | `knowledge-retrieval`, `document-extractor` |
| **Datasource** | `datasource` |
| **Agent** | `agent` |
| **Human** | `human-input` (HITL; see §8) |

**Fork-specific node adapters (in `api/core/workflow/nodes/`):**

| Node dir | Purpose |
|---|---|
| `agent_v2/` | Plugin-strategy agent node (`DifyAgentNode`); see §2 |
| `agent/` | Adapter wiring the built-in agent node to Dify's plugin agent-strategy resolver |
| `knowledge_index/` | Knowledge **write/index** path (`knowledge_index_node.py`) — document ingestion into a KB |
| `knowledge_retrieval/` | Dify-side wiring for the `knowledge-retrieval` built-in node |
| `datasource/` | Dify-side wiring for the `datasource` built-in node |
| `trigger_webhook/`, `trigger_schedule/`, `trigger_plugin/` | Trigger entry-point nodes (see §6) |

**Corrections to the prior draft:** there is **no** `Switch` node (branching is `if-else` cases), **no** separate `Chat`/`Completion` nodes (those are *app modes*, not workflow nodes), **no** dedicated `MCP` node (MCP servers are consumed through the `tool` node), and **no** `Image analysis`/`Audio-Video` nodes (those are plugin *tools* invoked via the `tool` node). Added: `question-classifier`, `list-operator`, `answer`/`start`/`end`, and the third trigger type (`trigger-plugin`).

### How Cendra currently uses it
Workflow canvas is the backbone for guest-communication automation flows, currently using stock built-in nodes only. No fork-specific node has Cendra customization beyond what ships on `main`. Brain gate invocation is the primary planned extension target (not yet wired; per G1 exit criteria).

### Extension / config points
- **⚠ MOAT (corrected):** new behavior attaches through **plugin categories** (tool / agent-strategy / datasource / trigger — see §5) and the `node_factory` runtime seam, **not** via arbitrary custom node types. Adding a brand-new node *type* means a `graphon` engine change (T-zone, Forge review).
- Variable schemas are typed and validated at edge-connection time
- Node-level model override, timeout, retry/error strategy (`fail-branch` / `default-value`)
- Workflow-level secrets via environment variables
- Import/export as DSL (YAML) — the `difyctl` CLI consumes DSL (see §11)

---

## 2. Agent Strategies & agent_v2

### What it does
Dify supports two agent execution models:

**Legacy agents** (`api/core/agent/`): `cot_agent_runner.py`, `fc_agent_runner.py`, plus chat/completion variants (`cot_chat_agent_runner.py`, `cot_completion_agent_runner.py`) and `base_agent_runner.py`. These loop over tool calls using a fixed ReAct (CoT) or Function-Calling strategy. Plugin-backed strategies are resolved through `api/core/agent/strategy/`.

**agent_v2** (`api/core/workflow/nodes/agent_v2/`, node class `DifyAgentNode`): delegates strategy execution to any installed **agent-strategy plugin**. The resolver/adapter lives in `api/core/workflow/nodes/agent/plugin_strategy_adapter.py` (`PluginAgentStrategyResolver`, `PluginAgentStrategyPresentationProvider`). This decouples the loop logic from the core runner so third-party or Cendra-owned strategy plugins control how the agent reasons and acts.

### How Cendra currently uses it
Not yet customized; using stock strategies. agent_v2 with a Brain-backed agent-strategy plugin is the architectural path for earned autonomy (gate-chain invocation from within the agent loop). Aspirational, not yet built.

### Extension / config points
- **⚠ MOAT:** agent-strategy plugins (PluginCategory `AgentStrategy`, value `agent-strategy`) — the supported seam for Brain-backed reasoning
- Tool set configured per agent node
- Max iterations, timeout configurable
- System prompt injected per workflow run context

---

## 3. RAG Pipeline & Knowledge Bases

### What it does
Full RAG infrastructure under `api/core/rag/` (verified subdirectories):

| Component | Location | What it does |
|---|---|---|
| **Ingestion** | `index_processor/`, `extractor/`, `splitter/`, `cleaner/`, `embedding/` | Parse → clean → chunk → embed → store |
| **Pipeline orchestration** | `pipeline/` | RAG-pipeline workflow type wiring |
| **Retrieval** | `retrieval/` | Top-k semantic search + keyword/hybrid |
| **Reranking** | `rerank/` | Reranker model applied before knowledge-node output |
| **Post-processing** | `data_post_processor/` | Score thresholding, dedup, reorder |
| **Doc/summary store** | `docstore/`, `summary_index/` | Document store + summary indexing |
| **Datasource connectors** | `datasource/` | File upload, web crawl, Notion, plugin datasources |
| **Knowledge bases** | Dify console UI | Named collections of indexed documents |

### How Cendra currently uses it
Knowledge bases for property documentation, house rules, and local-area guides. Ingestion and retrieval are inherited from Dify with no Cendra customization.

### Extension / config points
- Embedding model selectable per knowledge base
- Vector backend selectable (see §4)
- Chunking strategy (fixed size, paragraph, custom separator)
- Retrieval mode: semantic / keyword / hybrid
- Reranker model selectable
- Metadata filter expressions on retrieval
- **⚠ MOAT:** custom **datasource plugins** are now a first-class plugin category (see §5) — the supported seam for a Cendra/PMS knowledge connector

---

## 4. Vector Backends

### What it does
**Corrected architecture:** vector backends are no longer in-tree adapter files. Only the shared protocol and types remain in `api/core/rag/datasource/vdb/` (`vector_base.py`, `vector_factory.py` with `AbstractVectorFactory`, `vector_type.py`, `field.py`, `vector_backend_registry.py`). Each concrete backend ships as a **separately-installed workspace package** (`dify-vdb-*`) and registers an `importlib` entry point in group **`dify.vector_backends`**. `vector_backend_registry.py` discovers and lazily loads them by `VectorType` name.

**Backends actually wired in this fork** (~30 editable `dify_vdb_*` distributions installed): weaviate, qdrant, milvus, pgvector, pgvecto-rs, vastbase, chroma, opensearch, elasticsearch, tidb_vector, tidb_on_qdrant, oracle, tencent, baidu, vikingdb, upstash, oceanbase, lindorm, couchbase, myscale, analyticdb, alibabacloud_mysql, relyt, opengauss, tablestore, huawei_cloud, matrixone, clickzetta, iris, hologres. The full canonical set is the `VectorType` enum (`vector_type.py`).

### How Cendra currently uses it
Weaviate is the configured backend (`docs/weaviate/`, `WEAVIATE_MIGRATION_GUIDE` present). No custom adapter.

### Extension / config points
- Backend selected via `VECTOR_STORE` environment variable; switchable without code changes
- **⚠ MOAT (corrected):** a custom vector backend is added by **publishing a `dify-vdb-*` package with a `dify.vector_backends` entry point**, not by editing core. Lower-risk attachment surface than the draft implied (no T-zone edit).

---

## 5. Plugin System

### Architecture — **six** plugin categories (corrected from "four")
Verified against `PluginCategory` in `api/core/plugin/entities/plugin.py`:

| Category (enum) | What it provides |
|---|---|
| **Tool** | Callable tool for agent or workflow `tool` node |
| **Model** | A new LLM / embedding / rerank / TTS / STT provider |
| **Extension** | Endpoint plugin — exposes HTTP endpoints for external integration |
| **AgentStrategy** (`agent-strategy`) | Custom agent loop logic (used by agent_v2; see §2) |
| **Datasource** | **New.** Custom knowledge/data connectors for RAG ingestion (see §3) |
| **Trigger** | **New.** Plugin-provided workflow triggers (see §6) |

**⚠ MOAT:** `Datasource` and `Trigger` are first-class plugin categories the prior draft omitted entirely. They are direct attachment surfaces for a Cendra PMS connector and Cendra-owned event triggers — both relevant to the Moat Fit Map.

### Marketplace
Dify's plugin marketplace hosts community and vendor plugins. Cendra can publish private plugins (hosted or bundled).

### How Cendra currently uses it
No custom plugins deployed yet. A Brain agent-strategy plugin is the primary planned artifact (Forge owns).

### Extension / config points
- Plugin SDK publishes a manifest (`PluginDeclaration`) + implementation; declares tools/models/endpoints/datasources
- Plugins can call back into Dify APIs (backwards invocation under `api/core/plugin/backwards_invocation/`)
- Extension plugins expose HTTP endpoints (`EndpointProviderDeclaration`)
- Plugins are loaded/managed via the plugin daemon (`api/core/plugin/`)

---

## 6. Triggers & Channels

### What it does
**Three** native trigger node types (verified in `api/core/trigger/constants.py`):

| Trigger node | Value | Cendra relevance |
|---|---|---|
| **Webhook** | `trigger-webhook` | Inbound PMS events, channel-manager callbacks |
| **Schedule** | `trigger-schedule` | Nightly analytics, digest generation |
| **Plugin** | `trigger-plugin` | **⚠ MOAT:** plugin-provided triggers — a Trigger plugin (§5) can register a Cendra-owned event source |

Workflows and agents are also exposed through app channels:

| Channel | Mechanism | Cendra relevance |
|---|---|---|
| **Chat** | Streaming SSE chat (app mode) | Guest-facing chat; Cendra ops console |
| **Completion** | Single-shot prompt-response (app mode) | Internal tools |
| **Workflow API** | Structured input → output | Programmatic orchestration from Brain Engine |
| **Voice** | TTS/STT via model plugins | Future guest voice channel |
| **Email / Slack / etc.** | Via Extension (endpoint) plugins | PMS notification relay |

### How Cendra currently uses it
Webhook trigger for PMS event ingestion. Chat app mode for guest-facing touchpoints. Workflow API for Brain Engine→Dify handoff.

### Extension / config points
- Webhook shared secret, payload schema, response mapping
- Schedule: cron expression + timezone
- API key scoped per app or workspace
- Trigger plugins for custom event sources

---

## 7. Model Runtime

### What it does
Model runtime (`api/core/model_manager.py`, `api/core/provider_manager.py`) abstracts LLM/embedding/rerank/TTS/STT calls. Providers are supplied by **Model plugins** (PluginCategory `Model`) — OpenAI, Anthropic, Google, Azure, local Ollama, etc.

### How Cendra currently uses it
No provider restrictions set. Default model config per workflow. Model selection is a deployment-time concern, not a product differentiator.

### Extension / config points
- Model credentials per provider (encrypted at rest in Dify DB)
- Load balancing across multiple keys for a single provider
- Fallback model chain configurable
- Custom Model plugin (adds an entirely new provider)
- Per-node model override in workflows

---

## 8. Human Input (HITL)

### What it does
The `human-input` node pauses workflow execution and surfaces a structured form to an operator. Execution resumes when the human submits or approves. Wiring is via `api/core/workflow/human_input_adapter.py` (`adapt_node_config_for_graph`) and the `DifyHumanInputNodeRuntime`; the node implementation lives in the `graphon` package (`graphon/nodes/human_input/`). Workflow execution status includes a `paused` state for suspended runs.

### How Cendra currently uses it
Not yet wired to Brain gate output. Planned: when a Brain gate returns `REVIEW_REQUIRED`, the workflow emits a `human-input` node populated with the gate's reasoning and uncertainty score. Aspirational.

### Extension / config points
- Form schema defined per node (free text, select, approval boolean)
- Assignee: specific user, role, or "any available"
- Timeout and default-on-timeout configurable
- **⚠ MOAT:** the `human_input_adapter` seam is where SEMI_AUTO gate output would be injected — the supported attachment point for HITL wiring

---

## 9. MCP (Model Context Protocol)

### What it does
Dify supports MCP in both directions (verified under `api/core/mcp/`):

| Direction | Implementation | What it enables |
|---|---|---|
| **Outbound (Dify as client)** | `client/sse_client.py`, `client/streamable_client.py`, `mcp_client.py` | Dify workflows call external MCP servers as a tool source |
| **Inbound (Dify as server)** | `server/streamable_http.py` | Dify exposes its own tools/resources over MCP for external agents |

Auth: `auth/` + `auth_client.py` (OAuth / token-based). Sessions under `session/`. Shared types in `entities.py` / `types.py`.

### How Cendra currently uses it
MCP client for connecting PMS adapters that expose MCP endpoints. Inbound MCP server planned for the Brain Engine to call back into Dify workflow APIs from external agent runtimes.

### Extension / config points
- MCP server URL + auth configurable per tool source
- Inbound server: scope, resource exposure, session management
- Session persistence in `api/core/mcp/session/`

---

## 10. Observability / LLMOps

### What it does
`api/core/ops/` orchestrates tracing; the `ops_trace_manager.py` lazily imports trace providers, each shipped as a **separate workspace package under `api/providers/trace/`** (`trace-langfuse`, `trace-langsmith`, `trace-opik`, `trace-weave`, `trace-arize-phoenix`, `trace-aliyun`, `trace-tencent`, `trace-mlflow`).

**Supported providers** (verified `TracingProviderEnum` in `api/core/ops/entities/config_entity.py`): `arize`, `phoenix`, `langfuse`, `langsmith`, `opik`, `weave`, `aliyun`, `mlflow`, `databricks`, `tencent`. **Correction:** the prior draft listed "LangWatch" — it does **not** exist in this fork; it also missed Aliyun, MLflow, Databricks, and Tencent.

Traces include token counts, latency, node outputs, and tool calls; per-app tracing toggle.

### How Cendra currently uses it
Langfuse wired for trace visibility. Brain Engine outcome ledger is a separate, additive layer — not replacing Dify observability.

### Extension / config points
- LLMOps provider selectable per workspace/app
- Trace sampling rate
- Custom trace fields via metadata injection in workflows
- New provider = a `trace-*` workspace package (entry-point pattern, mirrors §4/§10 structure)

---

## 11. BaaS APIs & CLI

### What it does
Dify exposes a REST API surface for programmatic control:

| API group | What it covers |
|---|---|
| App / Workflow execution | Run workflows, stream responses, get run status |
| Knowledge base | CRUD documents, trigger re-index, query |
| Agent / conversation sessions | Multi-turn conversation management |
| Files | Upload, reference in workflow inputs |
| Annotations | Human-labeled Q&A for retrieval augmentation |
| Workspace / App management | Create/update apps, manage members |
| Model credentials | Provider config |

**CLI — `difyctl`** (verified: `cli/`, package `@langgenius/difyctl`). It is a **Bun-compiled TypeScript** client (not Python), distributed as a standalone binary via GitHub Actions artifacts. Features: browser device-flow signin, app list/inspect, run-with-structured-input, output as JSON/YAML/text, and DSL import/export (recent commit `#37232`). The HITL protocol surfaces here for agent/scripting use.

### How Cendra currently uses it
Brain Engine calls the Dify Workflow API to trigger execution. `difyctl` used in CI for DSL import/export validation.

---

## 12. Console Surfaces

What operators would see if we exposed raw Dify instead of the Cendra product console:

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

*This document is the "know the base product cold" inventory. It feeds the attachment-point column of the [Moat Fit Map](./moat-fit-map.md). Update it when a new Dify upstream capability is pulled in via rebase. Verified bottom-up against code on `main`, 2026-06-11.*
