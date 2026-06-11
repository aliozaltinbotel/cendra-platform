# Moat Fit Map

> **Owner:** Atlas (CTO — synthesis and differentiation verdict)
> **Inputs:** Porter (Brain moat-mechanism catalog) · Flow ([Dify Capability Register](./dify-capability-register.md))
> **Cross-links:** [Dify Capability Register](./dify-capability-register.md) · [Hospitality Productization Map](./hospitality-productization-map.md)
> **Last updated:** 2026-06-11
> **Purpose:** For every Brain Engine moat mechanism and every Dify table-stakes capability, one row: mechanism → Dify attachment point → what it governs → differentiation verdict → hospitality expression. This is the canonical defensibility reference for G2 PRDs and design-partner demo narrative.
>
> **Board confirmation pending** on the differentiation verdict section before it is treated as the product north star.

---

## Verdict Definitions

| Label | Meaning | Rule |
|---|---|---|
| **TABLE-STAKES** | Dify provides it; Cendra integrates and reuses it. Do NOT claim it as differentiation. Do NOT rebuild it. | Copy, configure, ship. No Cendra IP here. |
| **MOAT** | Brain Engine provides it. No fork can replicate it without replaying each operator's own history. Productize hard. | These are what we sell and defend. |
| **PRODUCTIZATION** | Hospitality framing of a generic capability. Necessary for the product but NOT defensible by itself. | **Must be anchored to a MOAT mechanism or it is a clone risk.** Flag every unanchored one. |

---

## The Crux Test

> "Dify's workflow canvas renamed 'Guest Journey Builder' is PRODUCTIZATION — cloneable — UNLESS the nodes inside it invoke the Brain gate chain and feed the outcome ledger, at which point the same surface becomes the face of a MOAT. Same pixels, completely different defensibility."

Apply this test to every row: *does removing the Brain Engine mechanism leave the surface defensible?* If yes → TABLE-STAKES or unanchored PRODUCTIZATION (clone risk). If no → MOAT.

---

## Part A — Brain Engine Moat Mechanisms

| # | Brain Mechanism | Dify Attachment Point | What it governs there | Verdict | Hospitality Expression |
|---|---|---|---|---|---|
| 1 | **Gate chain** (per-workflow earned autonomy pipeline: observe → abstract → gate → execute → certify) | Workflow nodes: agent_v2 plugin strategy + HITL node + Code node (gate evaluator) | Controls whether a workflow action executes autonomously, routes to human review, or abstains; attached at the agent_v2 strategy plugin hook so no Dify core change is needed | **MOAT** | "Cendra only acts on your behalf when it has earned the right — every action carries a confidence score the operator can audit" |
| 2 | **TrustMeter** (per-workflow autonomy score that increases with verified positive outcomes and decays on errors or operator overrides) | Knowledge base metadata + workflow variable + LLMOps trace annotation | Persists as a numeric field on workflow run metadata; gates escalation threshold in gate node | **MOAT** | "Your Assistant's confidence grows with every successful booking or resolved issue — and resets if it makes a mistake, so you stay in control" |
| 3 | **Signed criticality certificates** (cryptographically signed records of gate decisions, reasoning, and confidence at the moment of autonomous action) | LLMOps trace output + file attachment on Dify run record | Attached to each workflow execution record; retrievable via Workflow API | **MOAT** | "Every automated action has a tamper-proof receipt — if a guest disputes a charge the system handled, you have a signed log of exactly why and when" |
| 4 | **Calibrated abstention** (Brain refuses to act when uncertainty exceeds operator-set threshold; emits a structured HITL trigger instead of guessing) | HITL node (triggered by gate chain output when confidence < threshold) | Prevents low-confidence action from completing; routes to human with structured context | **MOAT** | "Cendra tells you when it doesn't know — it never guesses on a guest-impacting decision without surfacing it to you first" |
| 5 | **Bi-temporal observation / belief memory** (each observation carries valid-time and decision-time timestamps; Brain can reconstruct what it believed at any past moment) | Knowledge base document metadata + custom vector backend metadata fields | Stored as vector metadata; retrieved by Knowledge Retrieval node with temporal filter | **MOAT** | "Cendra remembers what the booking situation looked like last Tuesday when it made that pricing decision — not just what it knows now" |
| 6 | **Owner-policy Z3 verification** (operator-declared business rules compiled to SMT constraints; gate chain verifies proposed action satisfies all rules before executing) | Code node (Z3 solver subprocess) + workflow variable binding for policy constraint set | Code node runs Z3 on the action + constraint set; result propagates as a boolean gate input | **MOAT** | "You write your rules once ('never discount more than 15% without approval') and Cendra verifies every action against them mathematically — not by prompting" |
| 7 | **Three learning loops** (micro: within-run adaptation; meso: cross-run pattern extraction; macro: operator-level policy refinement from outcome history) | Workflow API callbacks + Knowledge base write path (Knowledge Index node) + LLMOps trace reader | Micro loop is in-context; meso/macro loops read trace history and write distilled patterns back to knowledge base via Knowledge Index node | **MOAT** | "Cendra gets better at your property, not just at hospitality in general — it learns your cancellation patterns, your guest preferences, your peak-season quirks" |
| 8 | **Pattern-mining → promotion** (recurring action patterns identified in trace history, surfaced to operator for approval, then promoted to autonomous workflow templates) | LLMOps trace API reader + Dify DSL import/export (`difyctl`) + workflow template library | Mined patterns exported as DSL candidates; operator approves in Cendra console; `difyctl` imports approved DSL as live workflows | **MOAT** | "When Cendra notices it's answered the same question 50 times, it asks if you want to automate it — and shows you exactly what it would do" |
| 9 | **Compounding outcome ledger** (immutable per-operator append-only record of every action, outcome, override, and revenue impact; the ledger is what accrues operator-specific value over time) | Workflow API run records + external ledger store (append-only, outside Dify DB) + LLMOps trace metadata | Dify run records are the event source; Brain Engine ETL writes to the ledger; ledger powers TrustMeter, learning loops, and the operator ROI dashboard | **MOAT** | "Your history with Cendra compounds — an operator on year three has a system that knows their property at a depth no competitor can replicate without those three years of data" |
| 10 | **Art. 12 / Art. 50 governance receipts** (EU AI Act-compliant human oversight records and end-user transparency notices, generated and stored per autonomous action) | LLMOps trace metadata + HITL completion records + signed criticality certificates | Gate chain generates the Art. 12 log entries; HITL completions generate Art. 50 transparency records; both attach to run records | **MOAT** | "Every autonomous guest interaction comes with a compliance receipt — if regulators ask, the answer is already in the system" |

---

## Part B — Dify Table-Stakes Capabilities

These are what Dify provides. Cendra integrates, configures, and reuses them. We do not claim them as differentiation and we do not rebuild them.

| Dify Capability | What Cendra does with it | Verdict | Notes |
|---|---|---|---|
| Workflow orchestration canvas (Graphon) | Build and run automation flows | **TABLE-STAKES** | The canvas is the face of MOAT only when Brain gate nodes are wired inside it |
| Agent strategies (CoT / FC) | Base agent reasoning for non-gate workflows | **TABLE-STAKES** | |
| RAG pipeline (ingest, retrieve, rerank) | Property docs, house rules, local area knowledge | **TABLE-STAKES** | |
| Knowledge bases | Named document collections per property | **TABLE-STAKES** | |
| Vector backends (Weaviate etc.) | Storage for knowledge and bi-temporal memory | **TABLE-STAKES** | Brain bi-temporal memory *uses* the vector backend; the backend itself is TABLE-STAKES |
| Plugin system (tool/model/extension/agent-strategy) | Extensibility substrate | **TABLE-STAKES** | Brain gate strategy plugin *runs on* this system; the plugin system itself is TABLE-STAKES |
| Marketplace | Plugin discovery | **TABLE-STAKES** | |
| Model runtime (100+ models) | LLM/embedding/rerank provider abstraction | **TABLE-STAKES** | |
| Webhook / Schedule triggers | Event-driven workflow entry points | **TABLE-STAKES** | |
| Chat / Completion / Workflow APIs | Channel delivery for guest and operator interactions | **TABLE-STAKES** | |
| Human Input (HITL) node | Escalation surface | **TABLE-STAKES** | HITL is TABLE-STAKES; the gate chain *triggering* HITL at the right threshold is MOAT |
| MCP client + server | Tool integration substrate | **TABLE-STAKES** | |
| LLMOps / observability (Langfuse etc.) | Trace visibility | **TABLE-STAKES** | |
| BaaS APIs | Programmatic control | **TABLE-STAKES** | |
| DSL import/export (`difyctl`) | Workflow portability | **TABLE-STAKES** | |
| EU AI Act baseline documentation | Compliance framing for Dify deployers | **TABLE-STAKES** | Brain governance receipts are MOAT; the Dify compliance guide is TABLE-STAKES |

---

## Part C — Productization Surfaces: Anchor Test

Every hospitality rename or UX framing must pass the anchor test: *is it backed by a MOAT mechanism?*

| Productization Surface | Generic Dify Equivalent | Anchored to MOAT? | Verdict | Recommendation |
|---|---|---|---|---|
| **Guest Journey Builder** (workflow canvas for guest-facing flows) | Workflow canvas | YES — nodes invoke gate chain (Brain moat #1) and feed outcome ledger (Brain moat #9) | **PRODUCTIZATION anchored to MOAT** | Ship. Name it. The anchor is the Brain-wired nodes inside. Without those nodes wired, this is a clone risk. |
| **Property Knowledge** (knowledge base for property docs) | Knowledge base | NO | **Unanchored PRODUCTIZATION → clone risk** | Anchor by combining with bi-temporal belief memory (Brain moat #5) — tag all docs with valid-time metadata and make the retrieval node time-aware. Until anchored, do not call it a differentiator. |
| **Cendra Assistant** (operator-facing agent) | Dify agent | YES — uses agent_v2 with Brain gate strategy plugin (Brain moat #1, #4) | **PRODUCTIZATION anchored to MOAT** | Ship. The agent is defensible because the gate chain and calibrated abstention are inside it. |
| **Automation Hub** (template library of common STR workflows) | Dify Explore / app marketplace | PARTIAL — templates are defensible only after pattern-mining promotion (Brain moat #8) | **PRODUCTIZATION partially anchored** | Anchor by surfacing promoted patterns (Brain moat #8) as the primary template source. Hand-crafted templates alone are cloneable; patterns mined from your operators' outcome ledger are not. |
| **Outcome Dashboard** (operator ROI view) | LLMOps monitoring | YES — powered by compounding outcome ledger (Brain moat #9) | **PRODUCTIZATION anchored to MOAT** | Ship. The ledger is the moat; the dashboard is the face of it. |
| **Compliance Receipts** (EU AI Act audit log view) | Dify EU AI Act guide | YES — powered by signed criticality certificates + Art. 12/50 records (Brain moat #3, #10) | **PRODUCTIZATION anchored to MOAT** | Ship. Competitors who copy the Dify compliance guide don't get the signed per-action receipts. |
| **Confidence Score** (TrustMeter display in operator UI) | (No Dify equivalent) | YES — directly exposes Brain moat #2 | **PRODUCTIZATION anchored to MOAT** | Ship. No Dify fork can replicate this without the underlying TrustMeter history. |
| **Smart Escalation** (HITL triggered by calibrated abstention) | HITL node | YES — gate chain threshold logic is Brain moat #4 | **PRODUCTIZATION anchored to MOAT** | Ship. The escalation threshold and reasoning are MOAT; the HITL node is TABLE-STAKES. |

---

## Atlas Synthesis

### Genuinely Defensible Surfaces (3–5)

1. **The gate chain + TrustMeter + outcome ledger compound** — The single most defensible surface. Earned autonomy accrues per operator over time. A fork of Dify with the same UI cannot replicate this without replaying each operator's actual history. Every month an operator runs Cendra, the moat widens.

2. **Calibrated abstention with signed governance receipts** — Competitors who auto-execute or who produce generic audit logs cannot match this. The combination of "refuses to act under uncertainty" + "cryptographically signed record of every autonomous decision" is a compliance and trust differentiator that a basic Dify fork cannot provide.

3. **Bi-temporal belief memory** — Knowing what the system believed at decision-time (not just now) is architecturally non-trivial and makes the outcome ledger auditable at a level generic RAG pipelines cannot match.

4. **Pattern-mining → promotion loop** — The workflow template library becomes defensible the moment it is powered by patterns mined from this operator's history. Generic workflow templates are a commodity; patterns distilled from 10,000 verified outcomes at a specific property are not.

5. **Owner-policy Z3 verification** — Mathematical constraint verification against operator-declared business rules is a capability no LLM-based competitor can fake. If we can make policy authoring accessible to non-technical operators, this becomes a hard moat.

### Honest Reuse List (this is just Dify)

- Workflow orchestration and the visual canvas
- RAG ingestion, chunking, embedding, and retrieval
- Agent reasoning loops (CoT, FC)
- Model runtime and provider integrations
- Plugin system and marketplace
- Webhook / schedule triggers and all API channels
- LLMOps observability integrations
- Vector backend infrastructure
- DSL import/export

We use all of these. We do not claim them as differentiation. We do not rebuild them. We version-track the upstream and rebase on schedule.

### Clone-Risk Surfaces (unanchored PRODUCTIZATION)

| Surface | Risk | Recommendation |
|---|---|---|
| **Property Knowledge** (unanchored) | Any Dify fork with an STR template can replicate this | Anchor with bi-temporal metadata on all property docs (Brain moat #5). Timeline: G2. |
| **Generic STR workflow templates** (before pattern-mining) | Commodity — every STR-focused Dify fork will have these | Do not ship Automation Hub as a standalone differentiator. Gate the feature behind pattern-mining promotion (Brain moat #8) or clearly label hand-crafted templates as "starter templates, not Cendra intelligence." |

> **Board confirmation requested:** Before this map is used as the input to G2 PRDs or design-partner demo narrative, the board should confirm the differentiation verdicts above, particularly the "clone risk" designations and the five defensible surfaces listed in the synthesis.
