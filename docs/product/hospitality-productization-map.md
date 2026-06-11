# Hospitality Productization Map

> **Owner:** Compass (productization + hospitality UX) · **Domain grounding:** Packs (482 STR scenarios + journey stages)
> **License sign-off column:** Forge (architecture + license guardrail — must sign off before any rename touches Dify chrome)
> **Cross-links:** [Dify Capability Register](./dify-capability-register.md) · [Moat Fit Map](./moat-fit-map.md)
> **Last updated:** 2026-06-11
> **Purpose:** The generic→hospitality transformation layer: terminology, UX surface decisions, and the license-safe scope column that prevents terminology work from crossing into license-violating rebranding.

---

## License Scope Key

| Label | Meaning | Examples |
|---|---|---|
| **(a) Our surface** | Cendra-owned UI, copy, templates, or workflow nodes — free to rename, reframe, and design without LangGenius permission | `web/**/brain/` components, Cendra console pages, operator-facing copy, workflow DSL templates |
| **(b) Dify chrome** | Dify-branded UI elements, logos, navigation, or components — **may NOT be renamed, hidden, or modified** without the LangGenius commercial agreement | Dify logo, Studio header, Dify-branded console nav, Dify copyright notices |
| **(c) Neutral** | Generic UI patterns (buttons, tables, forms) with no Dify branding — free to style and relabel | Data tables, chat bubbles, form fields, icons from open libraries |

> **Forge sign-off required** on any row marked **(b)** before implementation. Do not let terminology work quietly cross from (a)/(c) into (b).

---

## 1. Terminology Table

### Core Concepts

| Generic Dify term | STR / Hospitality term | Scope | Forge signed? | Notes |
|---|---|---|---|---|
| Workspace | Property Portfolio | (a) Our surface | Pending | Maps to a single operator's set of managed properties |
| App | Guest Experience Flow / Automation | (a) Our surface | Pending | "App" is too generic; operators think in guest journeys |
| Workflow | Guest Journey Automation | (a) Our surface | Pending | See crux test in Moat Fit Map — only MOAT-anchored workflows get this label |
| Workflow canvas | Guest Journey Builder | (a) Our surface | Pending | Surface-level rename; the canvas is Dify, the Brain-wired nodes inside are MOAT |
| Knowledge Base | Property Knowledge | (a) Our surface | Pending | House rules, amenity guides, local recs, pricing policy docs |
| Knowledge Base document | Property Document / House Rule | (a) Our surface | Pending | |
| Agent | Cendra Assistant | (a) Our surface | Pending | The operator-facing AI agent; defensible because Brain gate strategy runs inside |
| Agent strategy | Autonomy Policy | (a) Our surface | Pending | The gate chain config that governs how the assistant earns and expends trust |
| TrustMeter score | Confidence Level / Trust Score | (a) Our surface | Pending | Operator-visible number; "Your Assistant is 87% confident on pricing decisions" |
| HITL (Human Input node) | Escalation / Needs Your Attention | (a) Our surface | Pending | Never show "HITL" to operators; always "Needs Your Attention" with context |
| Gate chain output: ABSTAIN | "I'm not sure — this needs you" | (a) Our surface | Pending | Calibrated abstention expressed in operator language |
| Gate chain output: EXECUTE | "Acting now" | (a) Our surface | Pending | |
| Gate chain output: REVIEW_REQUIRED | "Waiting for your approval" | (a) Our surface | Pending | |
| Outcome ledger entry | Performance Record | (a) Our surface | Pending | "Cendra handled 47 check-in queries this month — here's the outcome record" |
| Criticality certificate | Action Receipt / Compliance Receipt | (a) Our surface | Pending | Shown when operator requests audit |
| LLMOps / observability | Activity Log | (a) Our surface | Pending | Internal ops only; not exposed to operators |
| Plugin | Integration / Add-on | (a) Our surface | Pending | "Plugin" is developer vocabulary; operators see "Integrations" |
| Model | AI Engine | (a) Our surface | Pending | Operators don't pick models; Cendra does. If surfaced, use "AI Engine" |
| Webhook trigger | Automated Start / Trigger | (a) Our surface | Pending | "Webhook" is developer vocabulary |
| Schedule trigger | Scheduled Automation | (a) Our surface | Pending | |
| DSL / workflow YAML | Automation Blueprint | (a) Our surface | Pending | Only surfaced in advanced / developer mode |
| Dify Studio | (not exposed to operators) | (b) Dify chrome | N/A | Keep internal; operators never see the raw Studio |
| Dify Explore | Automation Templates (curated) | (a) Our surface | Pending | Cendra wraps a curated subset; Dify Explore branding not shown to operators |
| Dify logo | (never shown to operators) | (b) Dify chrome | Must not modify | License requirement; only shown in internal/dev contexts |

### Guest Journey Stages → Operator Vocabulary
*(Grounded in the Packs 482-scenario journey map)*

| Journey Stage | Operator vocabulary in Cendra | Automations typically active |
|---|---|---|
| Pre-booking | Inquiry Handling, Availability Answers | Rate query response, instant booking acknowledgement |
| Booking confirmation | Booking Confirmation, Upsell | Welcome message, early check-in offer, damage deposit collection |
| Pre-arrival | Pre-arrival Sequence | House rule delivery, keycode dispatch, local guide send |
| Check-in | Check-in Support | Keycode help, early arrival routing, HITL for special requests |
| In-stay | In-stay Support | Maintenance escalation, amenity questions, noise complaint triage |
| Checkout | Checkout Sequence | Late checkout offer, checkout instructions, review request |
| Post-stay | Guest Recovery, Review Management | Review response, rebooking offer, complaint resolution |
| Operations (cross-stay) | Operations Automation | Cleaning scheduling, restocking alerts, revenue reporting |

---

## 2. UX Surface Decisions

### Expose to Operators

| Surface | What to show | Cendra label |
|---|---|---|
| Workflow run status | Status of active automations (Running / Waiting for you / Completed / Error) | Automation Activity |
| HITL queue | List of items needing operator decision, with context and recommended action | Needs Your Attention |
| Outcome ledger summary | Monthly performance snapshot: actions taken, outcomes, revenue impact | Assistant Performance |
| TrustMeter per automation | Confidence level for each active automation | Confidence Level |
| Knowledge base viewer | Browse and update property documents | Property Knowledge |
| Automation template library | Curated and promoted workflow templates | Automation Templates |
| Integration status | Connected PMSs, channel managers, and external services | Integrations |
| Compliance receipts | On-request audit log of autonomous actions | Action History / Receipts |

### Hide from Operators

| Surface | Reason |
|---|---|
| Dify Studio (workflow canvas) | Operator-facing workflows are pre-built or auto-promoted; raw canvas is a developer surface |
| Model / provider selection | Model choice is an ops concern, not an operator concern |
| Plugin marketplace | Operators don't install plugins; Cendra manages integrations |
| LLMOps / Langfuse traces | Internal observability only |
| Raw gate chain parameters | Exposed only as "Autonomy Settings" with guardrails |
| DSL / YAML | Exposed in advanced mode only for technical property managers |
| Dify API keys | Internal |

### Rename in Cendra Console (Our Surface Only)

| Raw element | Cendra label | Scope |
|---|---|---|
| Workflow node types (LLM, Code, HTTP, etc.) | Hidden behind named steps ("Send Message", "Check Availability", "Notify Cleaner") | (a) |
| Variable editor | Data Fields | (a) |
| Run logs | Automation Log | (a) |
| Annotation / feedback | Guest Feedback Training | (a) |

### Onboarding Framing

The operator's first session should establish:

1. **"Cendra learns your property"** — not "Cendra is a workflow tool." Frame setup as property configuration, not engineering.
2. **"Start with what matters most"** — onboarding wizard surfaces the top 3 automation templates by revenue impact (pre-arrival sequence, review request, inquiry response) rather than a blank canvas.
3. **"You're always in control"** — TrustMeter and "Needs Your Attention" queue introduced in onboarding week 1 before any autonomous action executes.
4. **"Your history stays yours"** — outcome ledger framing: "Every action Cendra takes is recorded. Over time, this record is what makes Cendra specific to your property."

---

## 3. License-Safe Scoping Column (Forge sign-off required)

> This section is the authoritative license boundary. No rename or hide listed as **(b) Dify chrome** may be implemented without Forge architecture review and Forge sign-off in this column.

| Action | Target | Scope | Forge sign-off | Status |
|---|---|---|---|---|
| Rename "Workspace" → "Property Portfolio" | Cendra console nav label | (a) Our surface | Not required | Pending impl |
| Rename "App" → "Automation" in operator UI | Cendra console | (a) Our surface | Not required | Pending impl |
| Rename workflow canvas → "Guest Journey Builder" | Cendra brain UI layer (`web/**/brain/`) | (a) Our surface | Not required | Pending impl |
| Hide Dify Studio nav from operator role | Cendra RBAC / role-gating | (a) Our surface | Not required | Pending impl |
| Rename Knowledge Base → "Property Knowledge" | Cendra console | (a) Our surface | Not required | Pending impl |
| Surface curated templates as "Automation Templates" | Cendra Explore wrapper | (a) Our surface | Not required | Pending impl |
| Hide raw plugin marketplace from operator role | Cendra RBAC | (a) Our surface | Not required | Pending impl |
| Add Cendra logo to operator console | Cendra console header | (a) Our surface | Not required | Pending impl |
| Remove or hide Dify logo from operator-facing console | Dify console chrome | **(b) Dify chrome** | **REQUIRED — do not implement without sign-off** | Blocked on LangGenius commercial agreement |
| Remove "Powered by Dify" notices | Dify console chrome | **(b) Dify chrome** | **REQUIRED** | Blocked on LangGenius commercial agreement |
| Rename Dify-branded console nav items (Studio, Explore, etc.) in Dify chrome | Dify console chrome | **(b) Dify chrome** | **REQUIRED** | Blocked on LangGenius commercial agreement |
| Custom domain (Cendra URL, no Dify in address) | Infrastructure | (c) Neutral | Not required | Pending DNS config |
| Custom color theme / CSS over Dify components | CSS overrides on our surface | (a) Our surface | Not required | Allowed; must not touch Dify-trademark elements |
| Rename Cendra-owned workflow node labels | `web/**/brain/` components | (a) Our surface | Not required | |
| Add "Needs Your Attention" banner (HITL surface) | Cendra console overlay | (a) Our surface | Not required | |

### Forge Sign-Off Path

For any **(b) Dify chrome** item:

1. Forge reviews the specific UI element against the Dify/LangGenius open-source license (Apache 2.0) and any commercial agreement in place.
2. Forge either: (a) confirms the commercial agreement covers the requested modification, or (b) flags the item as requiring commercial agreement negotiation before implementation.
3. Forge records the sign-off as a comment on this document's issue ([CEN-3](/CEN/issues/CEN-3)) and updates this table.

> **Current status:** No LangGenius commercial agreement is in place. All **(b) Dify chrome** actions are blocked until Forge confirms agreement status.

---

## 4. Cross-Links and Consistency Checks

- Every terminology entry in this map should have a corresponding row in the [Moat Fit Map](./moat-fit-map.md) verdict column (TABLE-STAKES, MOAT, or anchored PRODUCTIZATION).
- If a hospitality term appears in this map but has no Moat Fit Map row, flag it as potentially unanchored PRODUCTIZATION.
- The [Dify Capability Register](./dify-capability-register.md) §Console Surfaces table is the authoritative list of what Dify exposes; this map decides what Cendra shows, hides, or wraps around it.

---

*Update this document when: a new guest journey stage is added to the Packs scenario library; Forge signs off a (b) item; a new Brain mechanism surface is added; or the LangGenius license situation changes.*
