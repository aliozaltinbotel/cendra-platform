# PRD: Builder Surfaces — Guest Journey Builder + Automation Hub

> **Owner:** Compass (Product Lead)
> **Issue:** [CEN-24](/CEN/issues/CEN-24) — Wave 3 of the [CEN-11](/CEN/issues/CEN-11) G2 slate
> **Status:** Draft for Atlas review (accepted by merge)
> **Date:** 2026-06-11
> **Canonical inputs:** [Moat Fit Map](../moat-fit-map.md) (board-confirmed north star, CEN-4) · [Hospitality Productization Map](../hospitality-productization-map.md) (terminology FINAL) · Observe-Mode Activation PRD (CEN-17 — the posture this surface assumes)

---

## 1. Problem & Why Now

The Guest Journey Builder is the map's own Crux Test row:

> "Dify's workflow canvas renamed 'Guest Journey Builder' is PRODUCTIZATION — cloneable — UNLESS the nodes inside it invoke the Brain gate chain and feed the outcome ledger, at which point the same surface becomes the face of a MOAT. Same pixels, completely different defensibility."

The builder is the surface where the moat is either wired in or faked. Every workflow a design partner builds without gate-wired nodes is traffic that never reaches the ledger (#9) — and per the Moat Maturity Ruling, **ledger accrual is the schedule-critical moat**. The observe-mode program ([CEN-17](/CEN/issues/CEN-17)) defines the posture; this PRD defines the surface that ensures partner-built automations actually flow through it.

The Automation Hub has the inverse problem: hand-crafted STR templates are on the map's clone-risk list ("Generic STR workflow templates" — commodity, every STR-focused Dify fork will have them). The Hub is only defensible once it is fed by pattern-mining promotion (#8), and that path is **unbuilt**. Shipping the Hub honestly therefore means: starter templates clearly labeled as such, pre-wired for governance so they accrue ledger from day one, and a promoted-template roadmap that is named but never demoed.

This PRD defines both surfaces for G2: what ships, what is labeled, what is gate-wired at launch, and what stays roadmap.

## 2. User & Job-to-be-Done

**Primary user:** the **design-partner PM** (property manager — the corpus persona for "operator").

**Job-to-be-done (Builder):** *"Let me set up and adjust the automations that run my guest journey — stage by stage, in my vocabulary — and have every guest-facing action they take be watched and recorded by Cendra, so the track record that earns autonomy later is building from the automations I actually run."*

**Job-to-be-done (Hub):** *"Don't make me start from a blank canvas. Give me working starting points for the situations my property actually hits — and be honest about which ones are generic starters versus things Cendra learned from real operations."*

**Secondary user:** the **Cendra program team**. JTBD: maximize governed coverage — the share of live guest-facing automations whose dispatches flow through the gate chain — because ungoverned automations accrue nothing and weaken the G3 evidence pack.

**Explicit non-user:** the guest. Builder and Hub are PM-facing surfaces; guests never see them.

## 3. Map-Row Citations (canonical input: `docs/product/moat-fit-map.md` on `main`)

| Map row | Mechanism / surface | Status (map, 2026-06-11) | Role in this PRD |
|---|---|---|---|
| **#1** | Gate chain (T1 tool-dispatch wrapper in `core/workflow/node_runtime.py` + T3 agent-loop gate in `agent_v2/agent_node._run`; `BRAIN_GATES_MODE` off/observe/enforce, per-tenant allowlist) | **implemented** (Batch 4; default OFF; "certify" not minted) | One of the two anchors. A builder node is "gate-wired" iff its execution passes through T1 or T3. |
| **#9** | Compounding outcome ledger (`brain_decision`, tenant-scoped; captured by T7 from the T1 stream wrapper, idempotent) | **implemented** (when gates active); cases persist as `scenario="general"` (unclassified) until Batch 5/6 | The second anchor. Gate-wired nodes feed this ledger; that accrual is what makes the canvas defensible. |
| **#8** | Pattern-mining → promotion (kernel miner + `epistemic/promotion.py`; evidence from `brain_decision`; pattern → DSL → `difyctl` import path) | **partial** — miner ported, **not running** (beat jobs log-and-skip); promotion-gate code ported; **DSL-promotion path unbuilt** | The Automation Hub's defensible template source — **roadmap only** in this PRD (§7). Never demoed as live. |
| **Part C — Guest Journey Builder** | Workflow canvas for guest-facing flows | **PRODUCTIZATION anchored to MOAT (#1 + #9)** — "Ship. Name it. The anchor is the Brain-wired nodes inside. Without those nodes wired, this is a clone risk." | The product definition in §4 is the enforcement of this row. |
| **Part C — Automation Hub** | Template library of common STR workflows | **PRODUCTIZATION partially anchored (#8)** — "Hand-crafted templates alone are cloneable; patterns mined from your operators' outcome ledger are not." | §5 ships the honest version: labeled starters + governance pre-wiring; promoted templates are §7 roadmap. |
| **Clone-Risk — Generic STR workflow templates** | Commodity risk | listed | Binding mitigation adopted verbatim: "clearly label hand-crafted templates as 'starter templates, not Cendra intelligence.'" |
| **Crux Test + Moat Maturity Ruling (Synthesis)** | — | binding | The defensibility test §4 enforces, and the live-vs-designed boundary §7 respects. |

Integration reality (binding): the gate chain attaches via **fork touchpoints T1/T3 that edit Dify core** — a maintained fork is a governed cost of the moat. **No claim of zero-core-edit integration appears in this PRD or any material derived from it.** The canvas itself is Dify's workflow orchestration (Part B, TABLE-STAKES): we configure and skin it; we do not rebuild it and do not claim it.

## 4. Product Definition — Guest Journey Builder

### 4.1 What it is

The Dify workflow canvas, presented to PMs as the **Guest Journey Builder**, organized by the nine corpus journey stages (terminology FINAL): Inquiry Handling / Availability Answers, Booking Confirmation, Pre-arrival Sequence, Check-in Support, In-stay Support, Revenue Opportunities, Checkout Sequence, Guest Recovery / Review Management, Operations Automation. Stage vocabulary is organizational framing (usability, no differentiation claim); the defensibility lives in §4.2.

### 4.2 Gate-wired node types at launch (the Crux Test row, made concrete)

A node is **gate-wired** when its execution flows through the live gate chain and therefore writes to the outcome ledger. At launch, per the code-verified attachment points (#1):

| Node type | Gate path | Ledger feed | Launch status |
|---|---|---|---|
| **Tool node** (incl. PMS-adapter and channel tools — the guest-facing action surface: messaging, booking changes, fee collection) | T1 tool-dispatch wrapper (`node_runtime.py`) | T7 capture → `brain_decision` (#9) | **Gate-wired** (live at observe posture) |
| **Agent node** (agent_v2 — Cendra Assistant steps inside a journey flow) | T3 `gate_agent_run` | via gated dispatches | **Gate-wired** (live at observe posture) |
| LLM, Code, IF/ELSE, template/transform, HTTP-request, trigger nodes | none | none | **Not gate-wired.** Honest consequence: a flow whose only externally-visible action is an ungated node accrues no ledger. |
| Knowledge Retrieval | T6 retrieval loopback (#5) when bound to Property Knowledge | n/a (retrieval, not action) | Governed retrieval is [CEN-21](/CEN/issues/CEN-21)'s lane, not re-scoped here. |

The exact node-type enumeration of "what dispatches through T1" is verified by Forge at implementation (platform ask P1); product rule is structural: **every guest-facing action a journey flow takes must execute through a gate-wired node type.** Curated starter templates (§5) satisfy this by construction.

### 4.3 The "Guest Journey Automation" label is earned, not default

Per the terminology table, the label **Guest Journey Automation** is *reserved* for Brain-gated workflows. Product enforcement:

- A workflow earns the label (and the builder's per-flow **"Cendra-governed"** indicator) only when its action nodes are gate-wired **and** the tenant's posture is ≥ observe. Everything else is a plain **Automation**.
- The builder shows a per-node governance marker on gate-wired nodes, with observe-honest copy (§8): *"Cendra watches and records this step."*
- A governed flow surfaces its accrual: *"Cendra has recorded N decisions from this automation"* (read from `GET /v1/brain/cases`; per-automation filtering is platform ask P2).

This is the crux test running inside the product: same canvas, two labels, and the difference is exactly whether the moat mechanisms are engaged.

### 4.4 What the Builder does NOT do in G2

No autonomy controls, no enforce-mode toggles, no approval routing (#13 is `partial`), no TrustMeter display in the builder (Confidence Level is [CEN-19](/CEN/issues/CEN-19)'s surface), no promotion of flows up the autonomy ladder. The builder at G2 is: compose flows, see which steps are governed, see the ledger accruing. Autonomy is earned per workflow later — never advertised ahead of its promotion gate.

## 5. Product Definition — Automation Hub

### 5.1 What it is

A curated template library (wrapping Dify Explore per Ruling Q4 — Dify Explore branding is never shown to operators; attribution inside the wrapper is the board-owned license track) presenting **starter templates** for the nine journey stages, instantiable into a PM's portfolio in one step.

### 5.2 Starter templates: labeled and governance-pre-wired

- **Labeling (binding, from the clone-risk row):** every hand-crafted template carries the label **"Starter template — not Cendra intelligence"** at every surface where templates are browsed, previewed, or instantiated. No exceptions, no demo-mode suppression. The honest pitch: *"a working starting point, built by our hospitality team — Cendra starts learning only after it runs at your property."* (And per the honest-data caveat, "learning" itself stays off-copy until classification ships — see §8 rule C5.)
- **Governance pre-wiring (the anchoring move available today):** every starter template ships with its guest-facing action steps built on gate-wired node types (§4.2), so an instantiated template starts accruing DecisionCases the moment the tenant runs ≥ observe. Starter *content* is cloneable; the accrual it kicks off is not. This is the honest bridge between "partially anchored" today and #8-powered defensibility later.
- **Catalog scope at launch:** at least one starter per journey stage, drawn from the Packs 473-scenario corpus (highest-volume scenarios per stage — e.g. check-in instruction timing, keycode dispatch, review request). Catalog curation is Packs' lane; per-template specs are implementation issues after this PRD is accepted.

### 5.3 Promoted templates: the defensible source — roadmap, not launch

The Hub's defensible template source is the pattern-mining → promotion path (#8): recurring cases mined from the tenant's own ledger, surfaced as **Suggested Automations** ("When Cendra notices it's answered the same question 50 times, it asks if you want to automate it"). Status is `partial`: miner ported but **not running** (beat jobs log-and-skip until per-tenant wiring), and the **pattern → DSL → `difyctl` import promotion path is unbuilt**. Therefore:

- The Hub ships **no** "Suggested Automations" section at launch — not an empty teaser, not a "coming soon" rail. The surface appears when the mechanism does.
- The roadmap dependency chain is named in §7 and in the Hub's internal docs, so nobody scope-creeps it into G2.
- No demo, deck, or partner conversation presents mined/promoted templates as live.

## 6. Success Metrics

| Metric | Definition | Target posture |
|---|---|---|
| **Governed coverage** (primary) | Share of live guest-facing automations per tenant whose action nodes are gate-wired (carry the governed indicator) | Approaches 100% for design partners; every ungoverned guest-facing flow is a named exception |
| **Ledger accrual per builder flow** (primary) | DecisionCases per governed automation per week (per-automation view of CEN-17's accrual metric) | Nonzero for every governed flow with real traffic; a governed flow accruing zero is a wiring bug or dead flow — flagged either way |
| **Template instantiation → accrual** | % of instantiated starter templates that produce ≥1 DecisionCase within 7 days of activation | High — validates governance pre-wiring (§5.2) end to end |
| **Label integrity** (guardrail) | Workflows carrying the "Guest Journey Automation" label / governed indicator without gate-wired action nodes | **Zero.** Any instance is a crux-test violation and a release blocker |

Honest-measurement rule: governed coverage counts flows whose dispatches *actually appear in the ledger*, not flows that merely contain a gate-wired node type on canvas.

## 7. Maturity: Live vs. Roadmap (with Batch dependencies)

**Live today (this PRD builds only on these):**

- Gate chain at T1/T3, observe posture, per-tenant allowlist (#1 — `implemented`, Batch 4)
- DecisionCase capture to the tenant-scoped ledger (#9 — `implemented` when gates active; T7, idempotent)
- Case read API `GET /v1/brain/cases` (live)
- Dify canvas, DSL import/export (`difyctl`), Explore wrapper substrate (Part B — TABLE-STAKES, reused not rebuilt)

**Roadmap (named so nobody reads them into scope; never demoed as live):**

| Dependency | Map row | Status | What it unblocks (not in this PRD) |
|---|---|---|---|
| Pattern miner running live (per-tenant beat-job wiring) | #8 | `partial` — log-and-skip today | Mining recurring cases from real ledgers |
| Case classification (replaces `scenario="general"`) | #9 caveat | Batch 5/6 | Learnable cases — the miner's useful input; "Cendra learns your property" copy |
| Pattern → DSL → `difyctl` promotion path | #8 | **unbuilt** | Suggested Automations in the Hub (§5.3); the Hub's full anchor |
| Promotion-gate scoring against accrued evidence | #8 / #2 | `partial` | "Approve this suggested automation" flow |
| HITL approval routing | #13 | `partial` | Needs Your Attention integration from builder flows |
| TrustMeter in-chain + display | #2 | `partial` — display-only ruling | Per-automation Confidence Level in the builder ([CEN-19](/CEN/issues/CEN-19) surface) |
| Z3 policy in-chain | #6 | `partial` | "Verified against your house rules" badges on builder nodes |

The Hub's defensibility upgrade (starter-dominant → promoted-dominant) is therefore gated on **three** stacked dependencies: classification (Batch 5/6) → miner live → promotion path built. This PRD treats that entire chain as roadmap.

## 8. Hospitality Copy Rules (builder + hub)

Vocabulary is the FINAL terminology table (productization map §1). Dify branding is never touched or shown to operators (board-owned license track; Ruling Q4 governs attribution inside the Explore wrapper).

- **C1 — Demo-narrative rule, verbatim in spirit:** all builder/hub copy derives from "Cendra is **watching, scoring, and accruing your ledger** today; autonomy is **earned and switched on per workflow**." Forbidden: present-tense autonomy claims ("Cendra handles your check-ins"), signed-receipt claims, self-improvement claims, any autonomy level not cleared by its promotion gate.
- **C2 — Surface names:** Workflow canvas → **Guest Journey Builder**; gated workflow → **Guest Journey Automation** (label reserved per §4.3); ungated workflow → **Automation**; template library → **Automation Templates** (the Hub); mined candidates → **Suggested Automation** (roadmap-only term, must not appear in G2 UI). Journey stages use the FINAL stage vocabulary (§4.1). Never shown: "workflow," "DSL," "node," "Dify," "gate chain," "DecisionCase."
- **C3 — Starter labeling (binding):** "**Starter template — not Cendra intelligence**" on every starter template at browse, preview, and instantiation. No copy implies a starter template was learned, mined, or personalized.
- **C4 — Governance copy is observe-honest:** the governed indicator says Cendra *watches and records* — "Cendra doesn't act, block, or send anything on its own in this mode." Past-conditional framing for would-have decisions follows CEN-17 §8.
- **C5 — Accrual, not learning:** "recording," "building this automation's track record" — yes. "Learning," "getting smarter" — not until classification ships (#9 caveat). Inherited verbatim from CEN-17 §6.
- **C6 — No zero-core-edit claims** anywhere, including developer-facing Hub/template docs.
- **C7 — Persona:** builder and hub copy addresses the PM ("you," "your property," "your guests"). Guests are never addressed by these surfaces.

## 9. Brain / service_api Capabilities Consumed

| Capability | Mechanism | Status |
|---|---|---|
| Gate chain at tool dispatch + agent run | #1 — T1/T3, `BRAIN_GATES_MODE` + per-tenant allowlist | live (observe posture per CEN-17) |
| DecisionCase capture per gated dispatch | #9 — T7 → `brain_decision` | live (when gates active) |
| Case read API | `GET /v1/brain/cases` (service_api/brain) | live — powers accrual counts (§4.3); per-automation filtering needs P2 |
| Pattern miner + promotion gate | #8 — kernel `patterns/` + `epistemic/promotion.py` | **partial — NOT consumed at launch**; roadmap §7 |
| DSL import (`difyctl`) | Part B table-stakes | live as tooling; promotion-path consumption is roadmap §7 |

## 10. Platform Asks (for Atlas — to be converted into interface issues for Forge's org; not created from this issue)

- **P1 — Gate-wiring introspection.** A supported way for the console to determine, per workflow, whether its action nodes execute through the gate chain (T1/T3) — i.e., a per-workflow/per-node "governed" status read. This powers the §4.3 label rule and the governed indicator structurally; today nothing tells a surface whether a given flow's dispatches are gated. Includes the authoritative enumeration of which node types dispatch through T1.
- **P2 — Per-automation ledger filtering.** `GET /v1/brain/cases` (or sibling) filterable/aggregatable by workflow/automation identifier, so the builder can render "N decisions recorded from this automation" and §6's per-flow accrual metric. (CEN-17's P2 asked for per-tenant aggregates; this is the per-automation cut of the same need — Atlas may merge them.)
- **P3 — Template provenance field.** A first-class provenance marker on Hub templates (`starter` vs `promoted`) so §5.2's labeling is structural, not copy-only, and the future Suggested Automations surface (§5.3) keys off data rather than curation convention.
- **P4 — Promotion-path seam definition (roadmap design ask, not a build ask).** When #8 work is scheduled, the pattern → DSL → `difyctl` promotion interface should be specced as the contract the Hub consumes (input: promoted PatternRule + evidence; output: importable Automation Blueprint with provenance=`promoted`). Named now so the Hub's roadmap section has a defined seam to point at; explicitly **not** G2 scope.

Builder/Hub UI build-out (stage framing, governed indicators, labels, template browse/instantiate) is **Pixel's lane**; starter-template catalog content is **Packs' lane** — both scoped as implementation issues after this PRD is accepted, not platform asks.

## 11. Acceptance Criteria

1. **Crux-test enforcement:** a flow built in the Guest Journey Builder whose action nodes are gate-wired, at a tenant running observe, writes DecisionCases to that tenant's `brain_decision` ledger — verified end to end on a real design-partner tenant (builds on CEN-17 acceptance #2).
2. **Label integrity:** the "Guest Journey Automation" label and governed indicator appear **only** on flows meeting §4.3's conditions (verified via P1 or interim manual audit); the §6 guardrail metric reads zero.
3. **Ungated honesty:** a flow with no gate-wired action nodes renders as plain "Automation," shows no governed indicator, and no accrual counter.
4. **Starter labeling:** every Hub template displays the §8 C3 label at browse, preview, and instantiation; instantiated starters have gate-wired action nodes by construction and produce ledger accrual under real traffic (§6 instantiation metric).
5. **No promoted-template surface:** the G2 Hub contains no "Suggested Automations" UI, copy, or demo path; #8's status is documented in the Hub's internal docs as roadmap with the §7 dependency chain.
6. **Copy compliance:** all shipped builder/hub strings pass §8 C1–C7; nothing claims autonomy, learning, signed receipts, zero-core-edit integration, or an autonomy level that hasn't cleared its promotion gate.

## 12. Out of Scope

- Enforce mode, autonomy promotion, approval routing, and any operator-facing autonomy controls (later rungs; separate PRDs).
- Suggested Automations / promoted templates (#8 roadmap — §5.3, §7).
- Confidence Level / TrustMeter display ([CEN-19](/CEN/issues/CEN-19)), Property Knowledge bi-temporal anchoring ([CEN-21](/CEN/issues/CEN-21)), Needs Your Attention queue (#13).
- Rebuilding any Part B table-stakes capability (canvas, DSL, Explore); any change to deployment topology or the board-owned license track; any modification of Dify branding.

## 13. Risks

- **R1 — Clone-risk window.** Until promoted templates exist, the Hub's visible content is commodity. Mitigation: binding starter labels (C3), governance pre-wiring (§5.2) so the *accrual* differentiates even when the *content* doesn't, and no defensibility marketing for the Hub until #8 ships.
- **R2 — Label drift.** Sales/demo pressure to call every flow a "Guest Journey Automation." Mitigation: §4.3 + §6 guardrail metric is a release blocker; the label rule is structural once P1 lands.
- **R3 — Governed-coverage theater.** Flows that contain a gate-wired node type but route real guest actions through ungated nodes (e.g., HTTP-request side effects) would show governed while accruing nothing meaningful. Mitigation: §6 honest-measurement rule (ledger rows, not canvas shapes); P1's authoritative node-type enumeration; template curation routes guest-facing actions through Tool/Agent nodes.
- **R4 — Roadmap leakage.** "Suggested Automations" is the most demo-tempting unbuilt feature in the product. Mitigation: §5.3's no-teaser rule and acceptance #5; the demo-narrative rule is board-confirmed and binding.
