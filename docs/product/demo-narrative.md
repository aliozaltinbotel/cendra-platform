# Design-Partner Demo Narrative & Claims Register

> **Owner:** Compass (Product Lead) · **Issue:** CEN-20 (Wave 1 of the [CEN-11](/CEN/issues/CEN-11) G2 slate)
> **Binding inputs:** [Moat Fit Map](./moat-fit-map.md) (board-confirmed north star, CEN-4 interaction `52576e43`) · Moat Maturity Ruling (Atlas, 2026-06-11) · CEN-10 copy rulings · [Hospitality Productization Map](./hospitality-productization-map.md) (473-scenario corpus, 9 journey stages)
> **Last updated:** 2026-06-11
> **Purpose:** The demo script we tell design partners, and the claims register that polices every sentence of it. The map on `main` is canonical: a claim moves from FORBIDDEN to MAY only when the corresponding Moat Fit Map row reads `implemented` — Atlas owns that flip, not marketing.

---

## The Story in One Line

> **"Cendra is watching, scoring, and accruing your ledger today; autonomy is earned and switched on per workflow."**

This is the honest observe-mode story (Moat Maturity Ruling, demo-narrative rule). We do not demo a robot that runs your property. We demo a system that is *earning the right* to run parts of it — and showing its receipts along the way.

Three verbs, three live mechanisms:

| Verb | What the partner sees | Backing mechanism (Moat Fit Map row) |
|---|---|---|
| **Watching** | Every guest-journey dispatch passes through the gate chain at observe posture; Draft replies wait for the PM | Gate-chain skeleton (#1, observe mode) |
| **Scoring** | Cendra declines to answer when it isn't sure, with a written rationale | Calibrated abstention (#4) |
| **Accruing** | Every gated dispatch writes an immutable DecisionCase; the ledger view grows during the demo | Compounding outcome ledger (#9) |

And one memory claim that makes the ledger auditable: Cendra can reconstruct **what it believed at decision-time**, not just what it knows now (bi-temporal retrieval, #5).

---

## Demo Script (Observe-Mode Story)

**Audience:** STR property managers (corpus persona: **PM**) in the design-partner program. **Posture:** one demo workspace, `BRAIN_GATES_MODE=observe`. Nothing in this script requires enforce mode, minted certificates, TrustMeter gating, or learning loops.

**Scenario grounding:** every beat is grounded in the Packs 473-scenario corpus (9 journey stages). Stage labels below are the corpus's own. Verbatim guest-message threads and concrete corpus scenario IDs per beat are Packs-owned demo content (delegated; see Scenario Content Ownership below).

### Act 1 — Watching: the guest journey, drafted not sent

*Stage: Pre-arrival (61 scenarios) → Check-in day (61 scenarios).*

A guest asks for an early check-in and the door code. Cendra drafts the reply — keycode timing per house policy, arrival-time collection — and the thread sits in **"Ready for you to send."**

**Say:** "Today, Cendra drafts and you send. That's not a feature cap — it's the first rung of a ladder. Every automation starts in Draft, and each one earns its way up *per workflow*: Draft → approve-to-send → autopilot. You decide when a workflow gets promoted, and the system has to show you the evidence first."

**Copy rule in force (CEN-10):** Draft is presented **only** as the OBSERVE rung of the earned-autonomy ladder (kernel `AutonomyState`: OBSERVE → SEMI_AUTO → AUTOPILOT, five-metric promotion gate). The standalone framing "AI drafts your replies" is forbidden — it is every inbox tool's commodity claim.

### Act 2 — Scoring: the system that says "I don't know"

*Stage: During stay (84 scenarios) — amenity question Cendra has no documented answer for (e.g., pool-heating specifics not in Property Knowledge).*

Cendra **abstains**: it declines to draft a guess and emits a structured rationale — what it was asked, what it didn't know, what confidence threshold it failed.

**Say:** "This is the most important thing you'll see today. Cendra never guesses on a guest-impacting decision. When uncertainty crosses your threshold, it refuses and tells you why. Calibration is statistical, per property, learned from outcomes — not a prompt that says 'be careful.'"

**Honesty notes (binding):**
- Today the abstention **refuses with a rationale**; one-tap approval routing into the operator queue is designed, not live (#13). Demo the refusal and the rationale; do not demo a routing flow.
- The unanswered question surfaces as a **Knowledge Gap card** (corpus scenarios 433–434). Present it as a workflow convenience only — **zero defensibility claims** on gap cards until the abstention-gate emission wiring lands (CEN-10 ruling; no gap-registry mechanism exists in the kernel today).

### Act 3 — Accruing: the ledger that compounds

*Stage: Upsell / revenue (41 scenarios) — late-checkout fee offer, drafted, PM-approved, outcome recorded.*

Open **Assistant Performance**. Every dispatch from Acts 1–2 is already there: action, outcome, override-or-approval, timestamp — an append-only DecisionCase per gated dispatch.

**Say:** "This ledger is the product. Every action, outcome, and override accrues to *your* property's record. In month three, Cendra's case for 'promote this workflow to autopilot' is built from your history — and that history is the one thing no competitor can copy, because replicating it means replaying your last three months."

**Honesty note (binding):** ledger capture is live **when gates are active** (observe or above). The compounding claim is conditional on running posture — which is exactly why design partners run observe mode from day one.

### Act 4 — Memory: what Cendra believed last Tuesday

*Stage: Post-stay (40 scenarios) — deposit/charge dispute referencing a decision made days earlier.*

Run an as-of retrieval: "show me what the booking situation looked like when that decision was drafted."

**Say:** "Cendra keeps two clocks: when a fact became true, and when Cendra learned it. So when a guest disputes a charge, you don't get the system's current opinion — you get a reconstruction of exactly what it believed at decision-time. That's what makes the ledger auditable instead of just long."

### Act 5 — Earned: the roadmap, labeled as roadmap

Show the promotion ladder UI / TrustMeter display (read API is live; display-only).

**Say:** "Here's where this goes — and I want to be precise about what's live and what's next. The confidence score you see is real and per-workflow. What it doesn't do yet is gate actions in the chain: that switch flips during the partner program, per workflow, with your sign-off. Same for signed action receipts and pattern-suggested automations — they're designed, the program is what brings them live, and you'll see each one turn on with evidence attached."

**Honesty notes (binding):** TrustMeter is **display-only** (#2: substrate + read API live, not consulted by the live gate chain). Nothing in this act may be phrased in present tense as shipping. "Urgent — Safety Issue" critical escalation may be *mentioned* as table-stakes safety responsibility (Critical tier, 15 scenarios) — it is never part of the differentiation pitch.

### Close

> "Most AI products ask you to trust them on day one. Cendra asks you to *watch it work* — and shows you the ledger, the abstentions, and the confidence scores it would need to earn anything more. Autonomy here isn't a toggle we sell; it's a state your workflows reach, one at a time, with receipts."

---

## Claims Register (MANDATORY — binding on all demo, marketing, and partner-facing copy)

Canonical source: [Moat Fit Map](./moat-fit-map.md) Part A status column + Moat Maturity Ruling. **A claim moves from Part 2 to Part 1 only when the cited map row on `main` reads `implemented`.** Compass enforces at copy review; Atlas owns the row flip.

### Part 1 — Claims we MAY make (live today, demo-safe at observe posture)

| # | Permitted claim | Map row | Required phrasing discipline |
|---|---|---|---|
| C1 | "Cendra tells you when it doesn't know — it never guesses on a guest-impacting decision." | #4 abstention — `implemented` | Refusal + rationale only; do **not** describe one-tap approval routing (#13 pending). |
| C2 | "Every action, outcome, and override accrues to an immutable per-property ledger." | #9 ledger — `implemented` when gates active | Always conditional on gates ≥ observe; never imply capture happens at OFF posture. |
| C3 | "Cendra can reconstruct what it believed at decision-time, not just what it knows now." | #5 bi-temporal memory — `implemented` | Demo-safe as-is (T6 retrieval loopback is live). |
| C4 | "Every workflow dispatch passes through the autonomy gate chain; today it runs in observe mode." | #1 gate chain — `implemented` (skeleton; default OFF) | Must name observe posture; "certify" step is excluded (#3 not minted). |
| C5 | "Restricted actions are blocked by a compliance monitor (never-AI denylist, STR data-sharing checks), and AI disclosure runs on guest-facing output." | #10/#14 compliance-monitor half — live | Frame as responsibility anchored to the ledger (#9) and receipts roadmap (#10); **never** sell the compliance stack standalone as differentiation (#14 ruled not a moat). |
| C6 | "Draft is the first rung of a per-workflow earned-autonomy ladder." | #1/#2 via kernel `AutonomyState` | Ladder framing **mandatory** (CEN-10); standalone "AI drafts your replies" forbidden. |

### Part 2 — Claims FORBIDDEN until the cited row reads `implemented`

| # | Forbidden claim | Why forbidden | Map row to watch |
|---|---|---|---|
| F1 | Live **signed receipts** / "tamper-proof receipt for every action" (present tense) | Certificates not minted at runtime (placeholder key; cert step skipped); Art. 12 records not emitted (`audit_factory=None`). Also: current design is HMAC, not public-key — never say "cryptographically signed" without that caveat even post-wiring. | #3, #10 |
| F2 | Live **TrustMeter gating** / "confidence score controls what Cendra is allowed to do" | TrustMeter is display-only; the live gate chain does not consult it. | #2 |
| F3 | Live **self-improvement** / "Cendra learned from your edit" / "gets better every week" | Meso loops log-and-skip; macro loops (Batch 6) are not on `cendra/main`. | #7 |
| F4 | **Pattern-promoted templates** / "automations mined from your history" as shipping | Miner not running live; pattern → DSL → workflow promotion path unbuilt. Hand-crafted templates must be labeled "starter templates, not Cendra intelligence." | #8 |
| F5 | **Policy math enforcement** / "every action is verified against your rules with a theorem prover" (present tense) | Z3 authoring + save-time verification live, but not a gate in the live dispatch chain. Permitted: "you author rules once; they're verified at save time" + roadmap framing for in-chain enforcement. | #6 |
| F6 | **One-tap approval / HITL routing** as live ("low-confidence actions wait in your queue") | Approval gateway ported but HITL-node wiring + timeout sweep pending; abstention currently refuses with rationale. | #13 |
| F7 | **Precondition blocking** as live ("Cendra won't act until ID is verified") | Blocker engine ported, not in the live dispatch chain. | #12 |

### Part 3 — Standing copy rules (not status-dependent; do not flip)

| # | Rule | Source |
|---|---|---|
| R1 | **Draft mode:** only as the OBSERVE ladder rung. Standalone "AI drafts your replies" is a commodity claim and is forbidden in all copy. | CEN-10 ruling 3 |
| R2 | **"Urgent — Safety Issue":** never marketed as differentiation; present only as table-stakes safety responsibility. Engineering guard: the approval-queue bypass stays inside the governance envelope — every critical escalation still writes a DecisionCase (#9) and passes the compliance monitor (#10). | CEN-10 ruling 2 |
| R3 | **Knowledge Gap cards:** zero defensibility claims until the #4 + #5 emission wiring lands (no gap-registry mechanism exists in the kernel today). Ship the surface; claim nothing. | CEN-10 ruling 1 |
| R4 | **No zero-core-edit claims.** The maintained fork (T1/T3 core edits) is an accepted, governed cost of the moat. No marketing or PRD may claim zero-core-edit integration. | Atlas adjudication, Moat Fit Map Part A |
| R5 | **Property Knowledge** is never called a differentiator until bi-temporal anchoring (#5) lands (CEN-15 design in flight). Table-stakes Dify capabilities (canvas, RAG, models, plugins, APIs) are never claimed as Cendra differentiation. | Moat Fit Map Part B/C |
| R6 | **Corpus count:** all scenario-volume claims cite **473 scenarios across 9 journey stages**. | Hospitality Productization Map count ruling |
| R7 | **License/branding:** board-owned; demo materials never modify Dify branding. | Board direction (CEN-9 retirement) |

---

## Scenario Content Ownership

The script above fixes the *beats* (stage, mechanism, claim). The *content* — verbatim guest-message threads, concrete corpus scenario IDs per act, property fixture data — is Packs-owned and tracked as a child issue of CEN-20. Acceptance for that content: each act cites ≥1 concrete corpus scenario ID; all threads use corpus persona vocabulary (guest / PM / owner / cleaner / vendor); nothing in the content implies a Part 2 capability is live.

## Change Control

- This document is bound to the Moat Fit Map on `main`. When Atlas flips a status row to `implemented`, the corresponding F-row may be promoted to a C-row **by PR against this file**, reviewed by Compass.
- Any new partner-facing claim not covered by C1–C6 is forbidden by default until registered here.
