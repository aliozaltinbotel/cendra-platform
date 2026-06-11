# Hospitality Productization Map

> **Owner:** Compass (productization + hospitality UX) · **Domain grounding:** Packs (473-scenario STR corpus + journey stages)
> **Count note (Compass ruling, 2026-06-11):** the corpus catalog holds **473 scenario records across 9 journey stages** (469 carry the full classification template). The earlier "482" figure also counted the corpus doc's 9 numbered preamble sections; all product claims, demos, and G3 evidence packs cite **473**.
> **License sign-off column:** Forge (architecture + license guardrail — must sign off before any rename touches Dify chrome). Forge's sign-off issue: [CEN-9](/CEN/issues/CEN-9).
> **Cross-links:** [Dify Capability Register](./dify-capability-register.md) · [Moat Fit Map](./moat-fit-map.md)
> **Last updated:** 2026-06-11
> **Purpose:** The generic→hospitality transformation layer: terminology, UX surface decisions, and the license-safe scope column that prevents terminology work from crossing into license-violating rebranding.
>
> **Finalization status (Compass, CEN-8, 2026-06-11):** Terminology table and UX surface decisions are **FINAL** on the product side. Every row carries a license scope label; rows whose scope is ambiguous are marked **Ruling Q*n*** and enumerated in §3 for Forge ([CEN-9](/CEN/issues/CEN-9)). Every framing sold as differentiation cites its [Moat Fit Map](./moat-fit-map.md) anchor (column "Differentiation"); framings with no anchor are flagged in §4 for Atlas's clone-risk list. Implementation of any **(b)** or **Ruling** row remains blocked on Forge.

---

## License Scope Key

| Label | Meaning | Examples |
|---|---|---|
| **(a) Our surface** | Cendra-owned UI, copy, templates, or workflow nodes — free to rename, reframe, and design without LangGenius permission | `web/**/brain/` components, Cendra console pages, operator-facing copy, workflow DSL templates |
| **(b) Dify chrome** | Dify-branded UI elements, logos, navigation, or components — **may NOT be renamed, hidden, or modified** without the LangGenius commercial agreement | Dify logo, Studio header, Dify-branded console nav, Dify copyright notices |
| **(c) Neutral** | Generic UI patterns (buttons, tables, forms) with no Dify branding — free to style and relabel | Data tables, chat bubbles, form fields, icons from open libraries |
| **Ruling Q*n*** | Scope is ambiguous between (a)/(c) and (b) — explicitly parked for Forge; see §3 "Open License Questions" | Role-gating Dify-branded pages, CSS theming boundaries, Explore wrapping |

> **Forge sign-off required** on any row marked **(b)** or **Ruling Q*n*** before implementation. Do not let terminology work quietly cross from (a)/(c) into (b).

---

## 1. Terminology Table

### Core Concepts (FINAL)

> **Persona note (corpus grounding):** in the 473-scenario corpus the operator is the **PM (property manager)** — that is the term professional STR operators use for themselves. "Operator" in this doc = PM. The owner is a **separate persona** with their own request/approval/reporting flows (Stage 9: block dates, revenue questions, expense approvals), not a synonym for operator. The full cast is guest / PM / owner / cleaner / vendor.
>
> **Reading the "Differentiation" column:** "—" means the rename is a **usability rename only** — it is never sold as differentiation, so the moat-not-clone lens does not apply. "MOAT #*n*" cites the [Moat Fit Map](./moat-fit-map.md) Part A mechanism (and Part C surface where one exists) that anchors the framing. **NONE** means the framing has no anchor and **must not be marketed as defensible** — these are flagged to Atlas in §4. Maturity caveats follow the Moat Fit Map "Moat maturity ruling": never demo a `partial`/`planned` mechanism as live.

| Generic Dify term | STR / Hospitality term | Scope | Forge | Differentiation (MOAT anchor) | Notes |
|---|---|---|---|---|---|
| Workspace | Property Portfolio | (a) Our surface | Not required | — usability rename | Maps to a single operator's set of managed properties |
| App | Automation | (a) Our surface | Not required | — usability rename | "App" is too generic. Corpus-grounded: PMs say "automations"; they think in situations and decisions, not "experience flows" — dropped "Guest Experience Flow" as non-operator vocabulary |
| Workflow | Guest Journey Automation | (a) Our surface | Not required | MOAT #1 + #9 — label reserved for Brain-gated workflows (crux test) | Per the Moat Fit Map crux test: only workflows whose nodes invoke the gate chain and feed the outcome ledger earn this label; ungated flows are plain "Automations" |
| Workflow canvas | Guest Journey Builder | (a) Our surface | Not required | MOAT #1 + #9 (Part C: Guest Journey Builder) | The canvas is Dify; the defensibility is the Brain-wired nodes inside. Without those nodes wired, this name is a clone risk — do not market it ahead of the wiring |
| Knowledge Base | Property Knowledge | (a) Our surface | Not required | **NONE — clone risk** (Part C: anchor to MOAT #5 in G2) | House rules, amenity guides, local recs, pricing policy docs. **Resolved (Compass, 2026-06-11): "Property Knowledge" is the single label** across console, onboarding, and all three product docs; the corpus's "Property Brain" is retired as a surface name to avoid collision with the Brain Engine decision kernel. Already on Atlas's clone-risk list — ship the name, **never market it as a differentiator** until bi-temporal anchoring lands |
| Knowledge Base document | Property Document / House Rule | (a) Our surface | Not required | — usability rename | |
| Agent | Cendra Assistant | (a) Our surface | Not required | MOAT #1 + #4 (Part C: Cendra Assistant) | Defensible because the gate chain and calibrated abstention run inside it |
| Agent strategy | Autonomy Policy | (a) Our surface | Not required | MOAT #1 | The gate chain config that governs how the assistant earns and expends trust |
| TrustMeter score | Confidence Level / Trust Score | (a) Our surface | Not required | MOAT #2 — **display-only today** (not in-chain) | Operator-visible number; "Your Assistant is 87% confident on pricing decisions." The "grows with history" claim holds only once tenants run gates ≥ observe |
| HITL (Human Input node) | Escalation / Needs Your Attention | (a) Our surface | Not required | MOAT #4 (routing via #13 pending) — Part C: Smart Escalation | Never show "HITL" to operators; always "Needs Your Attention" with context. The HITL node is TABLE-STAKES; the calibrated threshold that triggers it is the moat |
| Gate chain output: ABSTAIN | "I'm not sure — this needs you" | (a) Our surface | Not required | MOAT #4 | Calibrated abstention expressed in operator language |
| Gate chain output: EXECUTE | "Acting now" | (a) Our surface | Not required | MOAT #1 | |
| Gate chain output: REVIEW_REQUIRED | "Waiting for your approval" | (a) Our surface | Not required | MOAT #4 + #13 | #13 approval-gateway wiring is pending — queue semantics ship with it |
| Execution mode: DRAFT | "Ready for you to send" | (a) Our surface | Not required | MOAT #1/#2 **only as a rung of the earned-autonomy ladder** — standalone framing flagged (§4) | Corpus default execution mode "Draft": Cendra prepares the reply, PM sends. The highest-volume supervised mode and the trust-building on-ramp between Conditional and Approval Required. "AI drafts your replies" alone is a commodity claim — sell only as the ladder rung. If the gate chain has no DRAFT output, that's a kernel vocabulary gap — file with Porter |
| Critical-risk escalation | "Urgent — Safety Issue" | (a) Our surface | Not required | **NONE — do not sell as differentiation** (nearest mechanism #14 was ruled *not a moat*; flagged §4) | Corpus risk tier Critical (15 scenarios: gas smell, CO alarm, lockout, no power/water, property occupied, injury) demands **immediate escalation that bypasses the approval queue** — must be visually and verbally distinct from "Needs Your Attention". Ship for safety; market as table-stakes responsibility, not moat |
| DecisionCase | Decision Card | (a) Our surface | Not required | MOAT #9 | Corpus product-surface name for a classified situation awaiting or recording a decision; the unit listed in "Needs Your Attention" and the unit of the compounding ledger |
| Missing-info registry entry | Knowledge Gap | (a) Our surface | Not required | **NONE standalone — flagged §4**; candidate anchor MOAT #5 (epistemic store) pending Atlas ruling | Corpus surfaces unanswered property facts as "Knowledge Gap cards" (scenarios 433–434); resolving one feeds Property Knowledge. A gap-card list is cloneable; the defensible version is gaps emitted by calibrated abstention from the epistemic store |
| PatternRule candidate | Suggested Automation | (a) Our surface | Not required | MOAT #8 — **roadmap maturity**: promotion path unbuilt; never demo as live | Mined PM behavior awaiting approval before promotion; corpus surface name: "Learning Center" |
| Outcome ledger entry | Performance Record | (a) Our surface | Not required | MOAT #9 | "Cendra handled 47 check-in queries this month — here's the outcome record" |
| Criticality certificate | Action Receipt / Compliance Receipt | (a) Our surface | Not required | MOAT #3 + #10 — **receipts not minted today**; label "pending" in UI, never demo as live | Shown when operator requests audit |
| LLMOps / observability | Activity Log | (a) Our surface | Not required | — internal ops only | Not exposed to operators |
| Plugin | Integration / Add-on | (a) Our surface | Not required | — usability rename | "Plugin" is developer vocabulary; operators see "Integrations" |
| Model | AI Engine | (a) Our surface | Not required | — usability rename | Operators don't pick models; Cendra does. If surfaced, use "AI Engine" |
| Webhook trigger | Automated Start / Trigger | (a) Our surface | Not required | — usability rename | "Webhook" is developer vocabulary |
| Schedule trigger | Scheduled Automation | (a) Our surface | Not required | — usability rename | |
| DSL / workflow YAML | Automation Blueprint | (a) Our surface | Not required | — usability rename | Only surfaced in advanced / developer mode |
| Dify Studio | (not exposed to operators) | **(b) Dify chrome** | N/A — hiding is role-gating, see **Ruling Q1** | — | Keep internal; operators never see the raw Studio |
| Dify Explore | Automation Templates (curated) | (a) wrapper — **Ruling Q4** on attribution inside the wrapper | Ruling Q4 | PARTIAL MOAT #8 (Part C: Automation Hub) — hand-crafted templates are commodity; label them "starter templates" until pattern-mined promotion powers the library | Cendra wraps a curated subset; Dify Explore branding not shown to operators |
| Dify logo | (never shown to operators) | **(b) Dify chrome** | Must not modify | — | License requirement; only shown in internal/dev contexts |

### Guest Journey Stages → Operator Vocabulary (FINAL)
*(Grounded in the Packs 473-scenario journey map. The corpus defines **9 stages**, not 8 — Upsell / Revenue is a stage of its own, with 41 scenarios. Stage labels below are the corpus's own labels; per-stage scenario counts in parentheses. License scope: stage vocabulary, template names, and operator copy are Cendra-owned workflow/template content.)*

| Journey Stage (corpus label) | Operator vocabulary in Cendra | Scope | Automations typically active |
|---|---|---|---|
| Pre-booking (50) | Inquiry Handling, Availability Answers | (a) Our surface | Rate & discount queries, amenity/area answers (parking, pets, pool, distances), inquiry risk screening (same-night, zero-review, off-platform payment asks) |
| Booking confirmation (40) | Booking Confirmation | (a) Our surface | Welcome / "what happens next" message, guest profile completion (missing email, phone, arrival time), invoice requests, booking-change requests (guest count, pet, cot) |
| Pre-arrival (61) | Pre-arrival Sequence | (a) Our surface | Check-in instruction timing, arrival-time collection & changes, keycode / Wi-Fi dispatch, security deposit chase, local guide send |
| Check-in day (61) | Check-in Support | (a) Our surface | Keycode & lockbox help, property-not-ready routing, missing amenity at check-in, critical escalation (no power/water, gas smell, property occupied) |
| During stay (84) | In-stay Support | (a) Our surface | Maintenance escalation, amenity questions, noise complaint triage, damage reports, lockout & safety escalation, refund-request routing to PM |
| Upsell / revenue (41) | Revenue Opportunities | (a) Our surface | Early check-in & late checkout fees, stay extension, extra guest & pet fees, mid-stay cleaning, airport transfer, pool heating fee, damage deposit collection |
| Check-out (40) | Checkout Sequence | (a) Our surface | Checkout instructions, key return, deposit return questions, damage dispute routing, cleaner damage / missing-item reports |
| Post-stay (40) | Guest Recovery, Review Management | (a) Our surface | Review request & response, bad-review recovery, refund/compensation routing, rebooking & direct-booking offers, deposit return status |
| Internal operations (52, cross-stay) | Operations Automation | (a) Our surface | Cleaner turnover & readiness tracking, vendor dispatch & SLA chase, owner requests (block dates, revenue questions), PMS/channel sync-conflict detection, knowledge-gap capture |

> **Grounding corrections applied:** (1) Upsell / Revenue promoted to its own stage — the prior table folded "early check-in offer, damage deposit collection" into Booking confirmation and "late checkout offer" into Checkout; in the corpus all of these are Stage 6 (Upsell / Revenue) scenarios. (2) "Check-in" → "Check-in day" and "In-stay" → "During stay" to match corpus stage labels. (3) "Review request" moved from Checkout to Post-stay (corpus scenario 386). (4) Internal operations is not just cleaning/restocking/reporting — the corpus stage is dominated by vendor SLA workflows, owner requests, cleaner access/readiness, and PMS↔channel data conflicts.

---

## 2. UX Surface Decisions (FINAL)

### Expose to Operators

| Surface | What to show | Cendra label | Scope |
|---|---|---|---|
| Workflow run status | Status of active automations (Running / Waiting for you / Completed / Error) | Automation Activity | (a) Our surface |
| HITL queue | List of items needing operator decision, with context and recommended action | Needs Your Attention | (a) Our surface |
| Outcome ledger summary | Monthly performance snapshot: actions taken, outcomes, revenue impact | Assistant Performance | (a) Our surface |
| TrustMeter per automation | Confidence level for each active automation | Confidence Level | (a) Our surface |
| Knowledge base viewer | Browse and update property documents | Property Knowledge | (a) Our surface — Cendra-built page. If implementation instead reuses the Dify KB console page, **Ruling Q3** applies before ship |
| Automation template library | Curated and promoted workflow templates | Automation Templates | (a) wrapper — **Ruling Q4** on attribution inside the wrapper |
| Integration status | Connected PMSs, channel managers, and external services | Integrations | (a) Our surface |
| Compliance receipts | On-request audit log of autonomous actions | Action History / Receipts | (a) Our surface |

### Hide from Operators

> Hiding is implemented as **role-gating** (the surface still exists for internal/dev roles, unmodified). Whether role-gating a Dify-branded surface counts as "removing" it under the LangGenius additional license conditions is **Ruling Q1** — the single ruling covers every row marked Q1 below.

| Surface | Reason | Scope |
|---|---|---|
| Dify Studio (workflow canvas) | Operator-facing workflows are pre-built or auto-promoted; raw canvas is a developer surface | **(b) Dify chrome** — hidden via role-gating, **Ruling Q1** |
| Model / provider selection | Model choice is an ops concern, not an operator concern | **(b) Dify console surface** — hidden via role-gating, **Ruling Q1** |
| Plugin marketplace | Operators don't install plugins; Cendra manages integrations | **(b) Dify console surface** — hidden via role-gating, **Ruling Q1** |
| LLMOps / Langfuse traces | Internal observability only | (c) Neutral — third-party tools, never operator-facing |
| Raw gate chain parameters | Exposed only as "Autonomy Settings" with guardrails | (a) Our surface |
| DSL / YAML | Exposed in advanced mode only for technical property managers | (a) Our surface (the exposure UI is ours; the DSL format is Dify's, unmodified) |
| Dify API keys | Internal | **(b) Dify console surface** — hidden via role-gating, **Ruling Q1** |

### Rename in Cendra Console (Our Surface Only)

| Raw element | Cendra label | Scope |
|---|---|---|
| Workflow node types (LLM, Code, HTTP, etc.) | Hidden behind named steps ("Send Message", "Check Availability", "Notify Cleaner") | (a) Our surface |
| Variable editor | Data Fields | (a) Our surface |
| Run logs | Automation Log | (a) Our surface |
| Annotation / feedback | Teach Cendra | (a) Our surface — corpus surface name; the strong correction signal is the PM's edit of a draft, not guest feedback, so "Guest Feedback Training" mislabels the mechanism. Anchored to MOAT #7 (learning loops) — **meso/macro loops are not live**; never demo "Cendra learned from your edit" until #7 reads implemented |

### Onboarding Framing (FINAL)

The operator's first session should establish:

1. **"Cendra learns your property"** — not "Cendra is a workflow tool." Frame setup as property configuration, not engineering. *(Anchor: MOAT #9 compounding ledger + #7 learning loops; per the maturity ruling, week-1 copy says "records and learns from outcomes" only in observe-mode terms — watching and scoring — not self-improvement claims.)*
2. **"Start with what matters most"** — onboarding wizard surfaces the top 3 automation templates by revenue impact (pre-arrival sequence, review request, inquiry response) rather than a blank canvas. *(No differentiation claim — these are starter templates until MOAT #8 promotion powers the library.)*
3. **"You're always in control"** — TrustMeter and "Needs Your Attention" queue introduced in onboarding week 1 before any autonomous action executes. *(Anchor: MOAT #2 + #4 — the "earned autonomy with receipts" narrative.)*
4. **"Your history stays yours"** — outcome ledger framing: "Every action Cendra takes is recorded. Over time, this record is what makes Cendra specific to your property." *(Anchor: MOAT #9 — the schedule-critical moat; accrual requires gates ≥ observe, which is why observe-mode-on is a G2 priority for design partners.)*

---

## 3. License-Safe Scoping Column (Forge sign-off required — [CEN-9](/CEN/issues/CEN-9))

> This section is the authoritative license boundary. No rename or hide listed as **(b) Dify chrome** may be implemented without Forge architecture review and Forge sign-off in this column. Rows marked **Ruling Q*n*** are explicitly ambiguous and need a Forge ruling (questions enumerated below) before they can be treated as (a)/(c).

| Action | Target | Scope | Forge sign-off | Status |
|---|---|---|---|---|
| Rename "Workspace" → "Property Portfolio" | Cendra console nav label | (a) Our surface | Not required | Pending impl |
| Rename "App" → "Automation" in operator UI | Cendra console | (a) Our surface | Not required | Pending impl |
| Rename workflow canvas → "Guest Journey Builder" | Cendra brain UI layer (`web/**/brain/`) | (a) Our surface | Not required | Pending impl |
| Hide Dify Studio nav from operator role | Cendra RBAC / role-gating | **Ambiguous — Ruling Q1** | **Ruling required before impl** | Parked for Forge |
| Hide model/provider selection, plugin marketplace, Dify API keys from operator role | Cendra RBAC / role-gating | **Ambiguous — Ruling Q1** | **Ruling required before impl** | Parked for Forge |
| Rename Knowledge Base → "Property Knowledge" | Cendra console | (a) Our surface | Not required | Pending impl |
| Surface curated templates as "Automation Templates" | Cendra Explore wrapper | (a) wrapper — **Ruling Q4** on Dify attribution inside | **Ruling required before impl** | Parked for Forge |
| Add Cendra logo to operator console | Cendra console header | (a) Our surface | Not required | Pending impl |
| Remove or hide Dify logo from operator-facing console | Dify console chrome | **(b) Dify chrome** | **REQUIRED — do not implement without sign-off** | Blocked on LangGenius commercial agreement |
| Remove "Powered by Dify" notices | Dify console chrome | **(b) Dify chrome** | **REQUIRED** | Blocked on LangGenius commercial agreement |
| Rename Dify-branded console nav items (Studio, Explore, etc.) in Dify chrome | Dify console chrome | **(b) Dify chrome** | **REQUIRED** | Blocked on LangGenius commercial agreement |
| Custom domain (Cendra URL, no Dify in address) | Infrastructure | (c) Neutral — **Ruling Q6** to confirm | Confirm via Q6 | Pending DNS config |
| Custom color theme / CSS over Dify components | CSS overrides on our surface | (a) Our surface — boundary defined by **Ruling Q3** | **Ruling required for boundary** | Allowed in principle; must not touch Dify-trademark elements |
| Rename Cendra-owned workflow node labels | `web/**/brain/` components | (a) Our surface | Not required | |
| Add "Needs Your Attention" banner (HITL surface) | Cendra console overlay | (a) Our surface | Not required | |
| Serve multiple PM tenants from one Cendra deployment | Deployment architecture | **Ambiguous — Ruling Q2 (multi-tenant clause)** | **REQUIRED — blocks GA posture** | Parked for Forge; may require LangGenius commercial license regardless of branding |

### Open License Questions for Forge ([CEN-9](/CEN/issues/CEN-9))

These are the enumerated open questions Compass needs ruled before any **Ruling**-marked row is implemented. Each ruling should be recorded in this section and in the table above.

- **Q1 — Role-gating Dify-branded surfaces.** Dify's license (Apache 2.0 with LangGenius additional conditions) restricts removing/modifying console branding. Operators never see Studio, Explore, model/provider settings, the plugin marketplace, or Dify API-key pages — but the surfaces are *hidden via RBAC*, not removed or modified, and remain intact for internal roles. Does role-gating constitute "removal" under the additional conditions? *Depends on it: the entire "Hide from Operators" table; the operator console posture for G3 design partners.*
- **Q2 — Multi-tenant clause.** Cendra serves multiple independent PMs (tenants) from a shared deployment. Does this fall under the LangGenius additional condition restricting multi-tenant operation without a commercial license — even with all Dify branding intact? *Depends on it: GA deployment architecture; whether the LangGenius commercial agreement is a launch blocker rather than a branding nicety. This is the highest-stakes question in the list.*
- **Q3 — Styling vs. modification boundary.** CSS theming over Dify components is scoped (a). Where exactly is the line — is recoloring/reskinning a Dify-branded header "styling" or "modifying branding"? Is reusing an unbranded Dify console page (e.g., the KB viewer) inside the Cendra console (a)/(c) or (b)? *Depends on it: custom theme work; the Property Knowledge viewer implementation choice.*
- **Q4 — Wrapping Dify Explore.** The "Automation Templates" gallery is a Cendra-built wrapper over a curated subset of Dify Explore content. Must the wrapper retain visible Dify attribution for the wrapped content? *Depends on it: Automation Templates surface; onboarding wizard's top-3 templates.*
- **Q5 — Non-console attribution obligations.** Do guest-facing messages, emails, embedded webapp chrome, or API responses generated through Dify carry any Dify attribution obligations? *(Note: EU AI Act Art. 50 transparency disclosure is a separate, Cendra-owned obligation — MOAT #10 — and is not affected by this ruling.)* *Depends on it: all guest-journey message templates; the embedded webapp decision.*
- **Q6 — Custom domain.** Serving the Dify-based console under a Cendra domain with no Dify in the address — any license constraint? Provisionally scoped (c); confirm. *Depends on it: DNS/branding rollout.*

### Forge Sign-Off Path

For any **(b) Dify chrome** or **Ruling Q*n*** item:

1. Forge reviews the specific UI element against the Dify/LangGenius open-source license (Apache 2.0 + additional conditions) and any commercial agreement in place.
2. Forge either: (a) confirms the action is license-safe as scoped, or (b) flags the item as requiring commercial agreement negotiation before implementation.
3. Forge records the sign-off in [CEN-9](/CEN/issues/CEN-9) and updates §3 of this document (scope column + question list).

> **Current status:** No LangGenius commercial agreement is in place. All **(b) Dify chrome** actions are blocked, and all **Ruling Q*n*** rows are parked, until Forge rules in [CEN-9](/CEN/issues/CEN-9).

---

## 4. Moat-not-Clone Audit (Compass, CEN-8, 2026-06-11)

Result of applying the [Moat Fit Map](./moat-fit-map.md) anchor test to every rename and framing in §1–§2:

- **Anchored differentiation (cite their MOAT row in the Differentiation column):** Guest Journey Automation/Builder (#1+#9), Cendra Assistant (#1+#4), Autonomy Policy (#1), Confidence Level (#2), Needs Your Attention / Smart Escalation (#4, #13), gate-output copy (#1/#4/#13), Decision Card (#9), Performance Record (#9), Action Receipt (#3+#10), Suggested Automation (#8), Teach Cendra (#7). Each carries the maturity caveat from the Moat Fit Map ruling — **never demo a `partial`/`planned` mechanism as live, and never advertise an autonomy level that hasn't cleared its promotion gate.**
- **Usability renames (no differentiation claim, lens not applicable):** Property Portfolio, Automation, Property Document, Integration, AI Engine, Trigger, Scheduled Automation, Automation Blueprint, Activity Log, Data Fields, Automation Log, all journey-stage vocabulary.
- **Flagged — no MOAT anchor, goes to Atlas's clone-risk list** (additions to [Moat Fit Map](./moat-fit-map.md) Part C / Atlas synthesis; Atlas adjudicates):
  1. **Knowledge Gap cards** — a list of unanswered property facts is cloneable by any fork. Candidate anchor: MOAT #5 (epistemic store) + #4 (abstention emits the gap). Until Atlas rules and the wiring exists, ship the surface but make no defensibility claim.
  2. **"Urgent — Safety Issue" critical escalation** — nearest mechanism (#14 compliance stack) was explicitly ruled *not a moat*. Ship it as table-stakes operator responsibility; never market it as differentiation.
  3. **DRAFT-mode standalone framing** — "AI drafts your replies" is a commodity claim every inbox tool makes. Only the earned-autonomy ladder framing (Draft → Conditional → Autonomous, governed by #1/#2) is defensible. Marketing copy must always present Draft as a ladder rung.
- **Reaffirmed, already on Atlas's clone-risk list:** Property Knowledge (unanchored until MOAT #5 bi-temporal tagging, G2) and hand-crafted starter templates (commodity until MOAT #8 promotion powers the library — label "starter templates, not Cendra intelligence").

---

## 5. Cross-Links and Consistency Checks

- Every terminology entry in this map that is sold as differentiation cites a [Moat Fit Map](./moat-fit-map.md) Part A mechanism (and Part C surface where one exists) in its Differentiation column. Entries marked **NONE** are on, or flagged for, Atlas's clone-risk list (§4).
- If a hospitality term is added to this map without a Differentiation entry, it defaults to "usability rename — no defensibility claim allowed" until anchored.
- The [Dify Capability Register](./dify-capability-register.md) §Console Surfaces table is the authoritative list of what Dify exposes; this map decides what Cendra shows, hides, or wraps around it. The Register's TABLE-STAKES rows must never be claimed as differentiation (Moat Fit Map Part B).
- License rulings live in §3 and [CEN-9](/CEN/issues/CEN-9); product claims and demo-narrative limits live in the Moat Fit Map "Moat maturity ruling."

---

*Update this document when: a new guest journey stage is added to the Packs scenario library; Forge rules a Q*n* or signs off a (b) item ([CEN-9](/CEN/issues/CEN-9)); Atlas adjudicates a §4 clone-risk flag; a new Brain mechanism surface is added; or the LangGenius license situation changes.*
