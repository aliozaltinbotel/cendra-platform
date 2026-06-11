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

> **Porter verification (2026-06-11, code-verified):** Part A rows below were checked against `reference/brain_engine/` and `api/core/brain/` on `origin/cendra/main` (Batches 1–5 merged through PR #5; **Batch 6 — live learning loops + console UI — is not yet on `cendra/main`**). The brain kernel and its Dify wiring live on the `cendra/*` fork branches, not on `main`. The **Attachment Point** column has been corrected to the *actual* wiring recorded in `FORK_LEDGER.md` (touchpoints T1–T8) and `PORTING_MAP.md`; the original speculative cells were wrong on most rows. **Status** is code-verified (`implemented` = live in the runtime path / wired; `partial` = kernel module + service API present but not in the live dispatch gate chain, or not minted/enforced; `planned` = not yet ported to `cendra/main`). Verdict labels are left **unchanged** for Atlas — items that may affect a verdict are flagged in the issue comment, not edited here.
>
> **Integration reality:** this is a *maintained fork*, not a no-core-edit plugin install. Runtime touchpoints **T1 (`api/core/workflow/node_runtime.py`)** and **T3 (`api/core/workflow/nodes/agent_v2/agent_node._run`)** edit upstream Dify files (marked `# CENDRA-HOOK(Tn)`). Only **T4 (moderation module)** is genuinely zero-core-edit. All gating defaults to **`BRAIN_GATES_MODE=off`**; current posture is one workspace in `observe`. The live dispatch chain (`core/brain/gates.py`) runs **Compliance → Certificate → Abstention → Risk**; TrustMeter, Z3-policy, and blockers are **not** in that chain.
>
> **Atlas adjudication (2026-06-11):** Porter's findings are accepted in full. Rulings: (1) All ten original **MOAT** verdicts **stand** — a MOAT verdict here is a ruling on *architectural defensibility* (does replication require replaying the operator's own history?), and implementation gaps delay a moat but do not erase one. What the gaps change is **maturity**, which is now first-class: see the "Moat maturity ruling" in the synthesis. (2) Verdict vocabulary for Part A is extended with one qualifier: **MOAT (supporting)** = a kernel mechanism whose defensibility derives entirely from a core mechanism it serves; it is not standalone-defensible and must not be sold as such. Rows #11–#13 are ruled MOAT (supporting); row #14 is ruled **not a moat** (defensible head start). (3) The "without touching Dify core" framing is **retired** — the maintained fork (T1/T3 core edits) is an accepted, governed cost of the moat (FORK_LEDGER.md + G1 rebase discipline), not a contradiction of it. No marketing or PRD may claim zero-core-edit integration.

| # | Brain Mechanism | Dify Attachment Point (code-verified) | What it governs there | Status | Verdict | Hospitality Expression |
|---|---|---|---|---|---|---|
| 1 | **Gate chain** (per-workflow earned autonomy pipeline: observe → abstract → gate → execute → certify) | Tool-dispatch wrapper in `core/workflow/node_runtime.py` (**T1**) + agent-loop gate via `agent_v2/cendra_brain_layer.gate_agent_run` called in `agent_node._run` (**T3**). Kernel chain = `core/brain/gates.py::DecisionPipelineAdapter`, composed in `core/brain/runtime_gateway.py`. **Fork touchpoint — edits Dify core, not zero-edit.** | Permits / refuses each workflow tool dispatch and agent run; `BRAIN_GATES_MODE` = off / observe / enforce, optional per-tenant allowlist | **implemented** (Batch 4; default OFF; "certify" step not minted — see #3) | **MOAT** | "Cendra only acts on your behalf when it has earned the right — every action carries a confidence score the operator can audit" |
| 2 | **TrustMeter** (per-workflow autonomy score that increases with verified positive outcomes and decays on errors or operator overrides) | `core/brain/autonomy/trust_meter.py` + `brain_autonomy` table; read via `GET /v1/brain/trust-meter/<property_id>` (`service_api/brain`). **Not** knowledge-base metadata / workflow variable / trace annotation. | Per-property autonomy score; intended to gate escalation thresholds | **partial** — substrate + read API present (Batch 2/5); **not consulted by the live gate chain** (`gates.py` has no TrustMeter gate) | **MOAT** | "Your Assistant's confidence grows with every successful booking or resolved issue — and resets if it makes a mistake, so you stay in control" |
| 3 | **Signed criticality certificates** (cryptographically signed records of gate decisions, reasoning, and confidence at the moment of autonomous action) | `core/brain/certificates/` (issuer/verifier, **HMAC** via dify_config). Verifier occupies a gate slot in `runtime_gateway`, but certs are **not minted at runtime** (placeholder key; cert step skipped). No wiring to run records / file attachments yet. | Would attach a signed decision record per autonomous action | **partial** — module ported (Batch 1); issuance unwired (Batch 5+ seam). Note: "signed" = HMAC (symmetric), not public-key | **MOAT** | "Every automated action has a tamper-proof receipt — if a guest disputes a charge the system handled, you have a signed log of exactly why and when" |
| 4 | **Calibrated abstention** (Brain refuses to act when uncertainty exceeds operator-set threshold; emits a structured HITL trigger instead of guessing) | `core/brain/abstention/` `AbstentionGate` in the `runtime_gateway` chain (T1/T3); persistent calibration in `brain_calibration` (`sa_store`, **T5**). In enforce mode it **refuses the dispatch with a rationale** — it does **not** currently route to a Dify HITL node (see new row #13). | Blocks low-confidence dispatch; records outcome to the calibration window | **implemented** (most-live gate); at generic dispatch confidence=1.0 → Wilson success-rate path active; conformal path sharpens once agent-loop confidence flows | **MOAT** | "Cendra tells you when it doesn't know — it never guesses on a guest-impacting decision without surfacing it to you first" |
| 5 | **Bi-temporal observation / belief memory** (each observation carries valid-time and decision-time timestamps; Brain can reconstruct what it believed at any past moment) | Redis-backed bi-temporal KG (`memory/knowledge_graph.py`, `kg_as_of.py`, `brain:kg:` keyspace) + epistemic Postgres store (`brain_epistemic`). Served to Dify via the **External Knowledge Base API loopback** — `POST /v1/brain/retrieval` (**T6**). **Not** vector-backend metadata + a "temporal filter" on the Knowledge Retrieval node. | Supplies as-of belief reconstruction to retrieval calls | **implemented** (Batch 2/3; retrieval endpoint Batch 5) | **MOAT** | "Cendra remembers what the booking situation looked like last Tuesday when it made that pricing decision — not just what it knows now" |
| 6 | **Owner-policy Z3 verification** (operator-declared business rules compiled to SMT constraints; gate chain verifies proposed action satisfies all rules before executing) | Kernel `core/brain/policy/` (lark grammar + `z3_compiler.py`); documents persist in `brain_owner_policies`; authored/verified (Z3 at save time) via `GET/POST /v1/brain/policies/<owner_id>`. **Not** a Dify Code-node Z3 subprocess; **not** in the live dispatch chain. | Compiles operator rules to SMT and verifies proposed actions | **partial** — compiler + registry + API ported (Batch 5); not yet a gate in `runtime_gateway` | **MOAT** | "You write your rules once ('never discount more than 15% without approval') and Cendra verifies every action against them mathematically — not by prompting" |
| 7 | **Three learning loops** (micro: within-run adaptation; meso: cross-run pattern extraction; macro: operator-level policy refinement from outcome history) | Celery **beat jobs** (`# CENDRA-HOOK(T5)` in `ext_celery.py`) reading the DecisionCase ledger / episodic memory and writing distilled patterns back to brain stores. **Not** Knowledge Index node / LLMOps trace reader. | Consolidation, mining, decay, and policy refinement off the outcome ledger | **partial → planned** — micro (critic/friction) ported (Batch 1); meso consolidation/mining beat jobs **log-and-skip until per-tenant wiring**; macro (`cognition/{policy,trainer,sleep}`, `continual_learning`) is **Batch 6 TODO**, not on `cendra/main` | **MOAT** | "Cendra gets better at your property, not just at hospitality in general — it learns your cancellation patterns, your guest preferences, your peak-season quirks" |
| 8 | **Pattern-mining → promotion** (recurring action patterns identified in trace history, surfaced to operator for approval, then promoted to autonomous workflow templates) | Kernel `patterns/` miner + `epistemic/promotion.py`; evidence from the `brain_decision` ledger (T7), exposed via `GET /v1/brain/cases`. The **pattern → DSL → `difyctl` import** promotion path is **not implemented**; mining beat jobs log-and-skip. | Mines recurring cases; promotion-gate scores candidates | **partial** — miner + promotion-gate code ported (Batch 1/2); not running live; DSL-promotion to workflows unbuilt | **MOAT** | "When Cendra notices it's answered the same question 50 times, it asks if you want to automate it — and shows you exactly what it would do" |
| 9 | **Compounding outcome ledger** (immutable per-operator append-only record of every action, outcome, override, and revenue impact; the ledger is what accrues operator-specific value over time) | `brain_decision` table **inside the Dify Postgres DB** (tenant-scoped `patterns/case_store.py`), captured by **T7** `callback_handler/cendra_decision_capture.py` from the T1 stream wrapper (idempotent, ON-CONFLICT-DO-NOTHING). **Not** an external store; **not** LLMOps-trace ETL. | Records every gated dispatch + outcome as a DecisionCase | **implemented** (when gates active); cases stored as `scenario="general"` (unclassified — not yet learnable) until Batch 5/6 classification | **MOAT** | "Your history with Cendra compounds — an operator on year three has a system that knows their property at a depth no competitor can replicate without those three years of data" |
| 10 | **Art. 12 / Art. 50 governance receipts** (EU AI Act-compliant human oversight records and end-user transparency notices, generated and stored per autonomous action) | Art. 50 disclosure + PII redaction via the **zero-edit moderation module** `core/moderation/cendra_brain` (**T4**, per-app console setting — the only genuinely zero-core-edit attachment). Compliance monitor (Reg 2024/1028, GDPR, never-AI) is the gate chain's first slot. Art. 12 decision-record factory is **unwired** (`audit_factory=None` in `runtime_gateway`). | Art. 50 transparency on outputs; compliance gate on dispatch | **partial** — Art. 50 + compliance monitor live (Batch 5); **Art. 12 per-action records not emitted at runtime**; depends on #3 certs which are not minted | **MOAT** | "Every autonomous guest interaction comes with a compliance receipt — if regulators ask, the answer is already in the system" |
| 11 | **CVaR tail-risk gate** *(added by Porter — in kernel, missing from map)* | `core/brain/risk/{cvar,gate}.py`; `RiskGate` is a live slot in the `runtime_gateway` chain. | Refuses actions whose Conditional-Value-at-Risk exceeds the per-action policy threshold | **partial** — wired but **permissive-by-absence**: no loss distributions at generic dispatch → returns `INSUFFICIENT_DATA` → passthrough until the planner supplies risk samples | **MOAT (supporting #1/#9)** — CVaR math is cloneable; defensible only once loss distributions are learned from the operator's own ledger | "Cendra won't take an action whose worst-case downside — an over-generous refund, an overbooking — exceeds the risk limit you set" |
| 12 | **Precondition blocker engine** *(added by Porter — in kernel, missing from map)* | `core/brain/patterns/{blockers,blocker_store}.py` + `brain_blockers` table. **Proposed attachment:** an additional gate slot in `runtime_gateway` / a pre-dispatch check in the T1 wrapper (not currently in the live chain). | Tenant-scoped precondition gating — an action is blocked until its preconditions are satisfied | **partial** — ported (Batch 2); not in the live dispatch chain | **MOAT (supporting #1)** — precondition gating alone is a commodity rules engine; defensible only as a slot in the earned-autonomy chain | "Cendra won't message a guest or modify a booking until the required preconditions — ID verified, payment cleared — are met" |
| 13 | **Confidence-routed approval gateway** *(added by Porter — the real HITL-routing substrate behind #4)* | `core/brain/autonomy/approval.py` + `brain_autonomy`. Non-blocking PENDING / resolve / expire routing by confidence. **Proposed attachment:** bind Dify **Human-Input (HITL) node** completion ↔ approval `resolve`, via `callback_handler`; timeout-sweep beat job. | Routes low-confidence actions to a human and resolves/expires them | **partial** — ported (Batch 2); HITL-node wiring + timeout sweep pending (the "route to human" path #4's narrative implies) | **MOAT (supporting #4)** — approval routing is a commodity queue; the calibrated confidence thresholds that drive it are the moat. Wiring this is the prerequisite for the Smart Escalation surface (Part C) | "Low-confidence actions wait for your one-tap approval and expire safely if you don't respond" |
| 14 | **Operator compliance stack (beyond receipts)** *(added by Porter — in kernel, missing from map)* | `core/brain/compliance/{never_ai_denylist,reg_2024_1028,data_subject_rights,retention,consent_store,encryption,pii_detector,redactor}.py`. Attaches via the compliance monitor (T1 chain first slot) + T4 moderation; DSAR/retention as proposed `service_api` endpoints. | Never-AI action denylist, EU Reg 2024/1028 STR data-sharing checks, GDPR DSAR / retention / consent | **partial** — checks/monitor/denylist/Reg-2024-1028 ported + in the gate chain (Batch 5); DSAR/retention/consent stores present; HASH redaction awaits per-tenant secret provider | **NOT a moat — defensible head start.** Compliance engineering is replicable with effort and has no history-dependence; productize it anchored to #10/#9 (receipts + ledger), never sell it standalone as differentiation | "Cendra blocks AI on legally-restricted actions and enforces STR data-sharing and guest data-rights obligations automatically" |

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
| **Compliance Receipts** (EU AI Act audit log view) | Dify EU AI Act guide | YES — powered by signed criticality certificates + Art. 12/50 records (Brain moat #3, #10) | **PRODUCTIZATION anchored to MOAT** | Ship the surface, but **no signed receipts are minted today** (#3 issuance unwired, Art. 12 records unemitted). Label receipts "pending" until that wiring lands; do not demo signed receipts as live. |
| **Confidence Score** (TrustMeter display in operator UI) | (No Dify equivalent) | YES — directly exposes Brain moat #2 | **PRODUCTIZATION anchored to MOAT** | Ship as **display-only now** (read API is live); TrustMeter does not yet gate anything in-chain (#2). The history claim holds only once tenants run gates ≥ observe. |
| **Smart Escalation** (HITL triggered by calibrated abstention) | HITL node | YES — gate chain threshold logic is Brain moat #4 | **PRODUCTIZATION anchored to MOAT** | Ship. The escalation threshold and reasoning are MOAT; the HITL node is TABLE-STAKES. Today abstention refuses with a rationale; routing into the HITL node lands with the #13 approval-gateway wiring. |
| **Knowledge Gap cards** (missing-info registry surface; corpus scenarios 433–434) *(adjudicated CEN-10)* | Plain unanswered-questions list (any fork can build one) | CONDITIONAL — anchor assigned to #4 + #5; emission wiring does not exist yet | **PRODUCTIZATION conditionally anchored — clone risk until wired** | Defensible only when each gap card is *emitted by the calibrated abstention gate* (#4) and *persisted in the epistemic store* (#5) with decision-time provenance — "here is what the system did not know when it abstained." Code-checked 2026-06-11: no gap-registry mechanism exists in `api/core/brain` on `cendra/main`; the wiring is net-new G2 work (same family as Property Knowledge bi-temporal anchoring). Until then, ship the surface with **no defensibility claim**. |
| **"Urgent — Safety Issue" critical escalation** (corpus Critical risk tier, 15 scenarios) *(adjudicated CEN-10)* | Generic priority-alert / notification | NO — by ruling. Nearest mechanism #14 was ruled *not a moat* (CEN-6) | **Permitted no-claim surface** | Ship as table-stakes operator responsibility; **never market as differentiation** (Compass position confirmed). Engineering guard (binding): bypassing the approval queue does **not** bypass the governance envelope — every critical escalation still writes a DecisionCase to the ledger (#9) and passes the compliance monitor (#10). The bypass is auditable; it is just not sellable. |
| **Draft mode** ("Ready for you to send") *(adjudicated CEN-10)* | Generic AI reply-drafting (every inbox tool) | YES — but **only as a ladder rung**: #1/#2 via the kernel's per-workflow `AutonomyState` | **PRODUCTIZATION anchored only as ladder rung — standalone framing is clone risk** | The standalone claim "AI drafts your replies" is a commodity and is forbidden in marketing. Permitted framing: Draft is the **OBSERVE** rung of the per-workflow earned-autonomy ladder — `AutonomyState` OBSERVE → SEMI_AUTO → AUTOPILOT (`core/brain/autonomy/models.py`), promotion gated on five reliability metrics (`PromotionGate`). This also resolves the suspected kernel vocabulary gap (productization map §1): Draft is an autonomy *state*, not a gate-chain output — no kernel change needed; record the mapping product-Draft ↔ kernel-OBSERVE. |

---

## Atlas Synthesis

### Moat Maturity Ruling (Atlas, 2026-06-11)

Porter's CEN-6 code verification (Part A note above) requires the synthesis to distinguish what is live from what is designed. This ruling is binding on G2 PRDs and the design-partner demo narrative:

- **Live today (demo-safe at observe posture):** calibrated abstention (#4), outcome-ledger capture (#9, when gates are active), bi-temporal memory via the retrieval loopback (#5), the gate-chain skeleton (#1 — default OFF, one workspace in `observe`, no tenant enforcing), and the compliance-monitor half of #10/#14.
- **Designed, not live (PRD as roadmap with explicit Batch dependencies; never demo as shipping):** TrustMeter in-chain (#2), certificate minting (#3 — and today's design is HMAC, not public-key), Z3 policy in-chain (#6), meso/macro learning loops (#7 — macro is Batch 6, not on `cendra/main`), pattern → DSL promotion (#8 — unbuilt), Art. 12 record emission (#10), blockers in-chain (#12), HITL approval routing (#13).
- **Demo-narrative rule:** the story is "Cendra is watching, scoring, and accruing your ledger today; autonomy is earned and switched on per workflow" — an honest observe-mode story. Any claim of live signed receipts, live TrustMeter gating, or live self-improvement is forbidden until the corresponding row reads `implemented`.
- **Ledger accrual is the schedule-critical moat.** The compounding ledger (#9) only compounds while gates run ≥ `observe` for real tenants. Every month of OFF posture is a month the moat does not widen — turning observe-mode on for design partners is therefore a G2 priority, not a nice-to-have.

### Genuinely Defensible Surfaces (3–5)

1. **The gate chain + TrustMeter + outcome ledger compound** — The single most defensible surface. Earned autonomy accrues per operator over time. A fork of Dify with the same UI cannot replicate this without replaying each operator's actual history. Every month an operator runs Cendra, the moat widens. *(Maturity: chain skeleton + ledger capture live; TrustMeter in-chain pending — #2.)*

2. **Calibrated abstention with signed governance receipts** — Competitors who auto-execute or who produce generic audit logs cannot match this. The combination of "refuses to act under uncertainty" + "cryptographically signed record of every autonomous decision" is a compliance and trust differentiator that a basic Dify fork cannot provide. *(Maturity: abstention live; receipts NOT minted — #3/#10 wiring is the gating dependency for this surface.)*

3. **Bi-temporal belief memory** — Knowing what the system believed at decision-time (not just now) is architecturally non-trivial and makes the outcome ledger auditable at a level generic RAG pipelines cannot match. *(Maturity: live via T6 retrieval loopback.)*

4. **Pattern-mining → promotion loop** — The workflow template library becomes defensible the moment it is powered by patterns mined from this operator's history. Generic workflow templates are a commodity; patterns distilled from 10,000 verified outcomes at a specific property are not. *(Maturity: roadmap — miner ported but not running; promotion path unbuilt — #8.)*

5. **Owner-policy Z3 verification** — Mathematical constraint verification against operator-declared business rules is a capability no LLM-based competitor can fake. If we can make policy authoring accessible to non-technical operators, this becomes a hard moat. *(Maturity: authoring + save-time verification live; in-chain enforcement pending — #6.)*

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

### CEN-10 Clone-Risk Adjudication (Atlas, 2026-06-11)

Ruling on the three flags Compass raised in the CEN-8 productization pass ([Hospitality Productization Map](./hospitality-productization-map.md) §4). All three Compass positions are confirmed; each flag gets a first-class disposition:

1. **Knowledge Gap cards → conditional anchor assigned (#4 + #5), clone-risk listed until wired.** The candidate anchor is architecturally sound: a gap card that records *what the system did not know at decision-time, emitted by the abstention gate and persisted in the epistemic store* cannot be replicated without the operator's own abstention history. But the mechanism does not exist — code check (2026-06-11, `origin/cendra/main`) finds no gap-registry concept anywhere in `api/core/brain`. The anchor is therefore **conditional on the emission wiring** (G2, scoped with Property Knowledge's #5 anchoring). Until it lands: ship the surface, zero defensibility claims.
2. **"Urgent — Safety Issue" → permitted no-claim surface.** Not added to the clone-risk table, because the clone-risk list tracks surfaces at risk of being *marketed* as differentiation, and this one is barred from differentiation claims outright (nearest mechanism #14 ruled *not a moat* in CEN-6). Two binding rules: (a) marketing/demo may present it only as table-stakes safety responsibility; (b) the approval-queue bypass stays inside the governance envelope — ledger capture (#9) and compliance monitoring (#10) apply to every critical escalation.
3. **DRAFT-mode standalone framing → clone-risk listed; ladder framing mandatory.** "AI drafts your replies" standalone is a commodity claim. The only permitted framing is Draft as the entry rung of the earned-autonomy ladder, which is not marketing fiction — it is the kernel's per-workflow `AutonomyState` machine (OBSERVE → SEMI_AUTO → AUTOPILOT, `core/brain/autonomy/models.py`) with five-metric promotion gating. Side ruling: the "kernel vocabulary gap — file with Porter" caveat in the productization map is **resolved without a kernel change** — product "Draft" maps to kernel `OBSERVE`; gate-chain outputs are a different axis (decision verdicts, not autonomy states).

### Clone-Risk Surfaces (unanchored PRODUCTIZATION)

| Surface | Risk | Recommendation |
|---|---|---|
| **Property Knowledge** (unanchored) | Any Dify fork with an STR template can replicate this | Anchor with bi-temporal metadata on all property docs (Brain moat #5). Timeline: G2. |
| **Generic STR workflow templates** (before pattern-mining) | Commodity — every STR-focused Dify fork will have these | Do not ship Automation Hub as a standalone differentiator. Gate the feature behind pattern-mining promotion (Brain moat #8) or clearly label hand-crafted templates as "starter templates, not Cendra intelligence." |
| **Knowledge Gap cards** (until #4/#5 emission wiring) *(CEN-10)* | A plain unanswered-questions list is replicable by any fork | Conditional anchor assigned: gap cards must be emitted by calibrated abstention (#4) writing to the epistemic store (#5) with decision-time provenance. No kernel gap-registry exists today — wiring is net-new G2 work. No defensibility claim until it ships. |
| **DRAFT-mode standalone framing** ("AI drafts your replies") *(CEN-10)* | Commodity claim every AI inbox tool makes | Forbidden standalone. All copy presents Draft as the OBSERVE rung of the earned-autonomy ladder (#1/#2, kernel `AutonomyState`). Ladder rung framing is anchored; the rung alone is not. |

> **Permitted no-claim surfaces** (not clone risks — differentiation claims are barred outright, so there is nothing to protect): **"Urgent — Safety Issue" critical escalation** (CEN-10 ruling above).

> **Board confirmation requested:** Before this map is used as the input to G2 PRDs or design-partner demo narrative, the board should confirm the differentiation verdicts above, particularly the "clone risk" designations and the five defensible surfaces listed in the synthesis.
