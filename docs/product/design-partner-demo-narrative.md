# Design-Partner Demo Narrative (Observe-Mode Story)

> **Owner:** Packs (hospitality solutions — scenario grounding) · **Reviewer:** Compass (productization)
> **Binding inputs:** [Moat Fit Map](./moat-fit-map.md) (board-confirmed 2026-06-11, CEN-4 — verdicts, Moat Maturity Ruling, and demo-narrative rule in force) · [Hospitality Productization Map](./hospitality-productization-map.md) (terminology FINAL, onboarding framing FINAL, CEN-7/CEN-8 grounding)
> **Scenario grounding:** the 473-scenario STR corpus (`reference/brain_engine/Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md`, on `cendra/main`). Per the Compass count ruling, all product claims cite **473**.
> **Last updated:** 2026-06-11
> **Purpose:** The scripted design-partner demo flow for the G2 program. Every beat is grounded in a real corpus scenario; every defensibility statement cites its Moat Fit Map row and respects the row's code-verified status. This document is the only approved demo script — ad-libbed defensibility claims are out of policy.

---

## The Story (one line, board-confirmed)

> **"Cendra is watching, scoring, and accruing your ledger today; autonomy is earned and switched on per workflow."**

This is an honest observe-mode story. We demo what is live at observe posture and we present everything else as the earned-autonomy roadmap, explicitly labeled as such.

### Forbidden claims (binding, from the Moat Maturity Ruling)

These may not be stated, implied, or visually staged anywhere in the demo until the corresponding Moat Fit Map row reads `implemented`:

| Forbidden claim | Why | Row |
|---|---|---|
| **Live signed receipts** ("every action already carries a tamper-proof certificate") | Certificate minting is unwired; Art. 12 records are not emitted | #3, #10 (`partial`) |
| **Live TrustMeter gating** ("the confidence score decides what Cendra can do") | TrustMeter is display-only; not consulted by the live gate chain | #2 (`partial`) |
| **Live self-improvement** ("Cendra learned from your edit / gets smarter every week") | Meso/macro learning loops are not live; macro is not on `cendra/main` | #7 (`partial → planned`) |
| Zero-core-edit integration | Framing retired by Atlas adjudication — the maintained fork is a governed cost of the moat | Part A note |
| Any differentiation claim on a clone-risk or no-claim surface | See "Zero-claim surfaces" section below | Part C / §4 |

### Demo-safe mechanisms (live today at observe posture)

| Mechanism | Status | What the demo may show |
|---|---|---|
| Gate chain skeleton (#1) | `implemented` (default OFF; demo tenant runs `observe`) | Every workflow dispatch passes through the gate pipeline and is recorded |
| Calibrated abstention (#4) | `implemented` (most-live gate) | "I'm not sure — this needs you" with a structured rationale |
| Outcome ledger capture (#9) | `implemented` when gates active | Decision Cards accruing per gated dispatch |
| Bi-temporal memory (#5) | `implemented` (T6 retrieval loopback) | As-of belief reconstruction ("what did we know last Tuesday") |
| Compliance monitor + Art. 50 disclosure (#10/#14, monitor half) | `partial` — monitor + Art. 50 live | Transparency disclosure on guest-facing output; compliance gate as first chain slot |

Everything else in the script is presented under the roadmap framing of Act 5 — never as shipping.

---

## Pre-Demo Checklist (run before every external presentation)

1. **Posture:** demo tenant runs `BRAIN_GATES_MODE=observe`. Confirm before the session; the ledger beats are only honest if gates are actually capturing.
2. **Traceability:** every defensibility line in this script cites a MOAT row in brackets. If you change a line, re-derive its citation from the [Moat Fit Map](./moat-fit-map.md) — a claim with no row, or whose row is not `implemented`, is cut, not softened.
3. **Review gate (acceptance condition):** this script must be re-reviewed against the demo-narrative rule (Moat Fit Map, Moat Maturity Ruling) after any Moat Fit Map update and before any external use.
4. **Vocabulary:** operator-facing terms only, per the [Hospitality Productization Map](./hospitality-productization-map.md) §1 — Decision Card, Needs Your Attention, Confidence Level, Property Knowledge, "Ready for you to send", Urgent — Safety Issue, Performance Record. Never HITL, DecisionCase, TrustMeter, gate chain, DSL.
5. **Persona:** the audience is a **PM (property manager)** — corpus persona ruling. Speak in PM vocabulary: automations, properties, guests, owners, cleaners, vendors.

---

## Demo Flow

Total runtime ~25 minutes: cold open (2) → Act 1 (4) → Act 2 (5) → Act 3 (4) → Act 4 (3) → safety beat (2) → Act 5 roadmap (4) → close (1).

### Cold Open — "Cendra learns your property" (2 min)

Frame from the productization map's onboarding framing (FINAL, §2):

> **Presenter:** "Cendra is not a workflow tool you configure — it's an assistant that learns your property. From day one it watches every guest conversation, scores every decision it would have made, and records the outcome. Nothing executes without you. That record — your record — is what makes Cendra specific to your property over time, and it's yours." *(Anchors: MOAT #9 compounding ledger; #2 + #4 "you're always in control". Observe-mode terms only: watching and scoring — no self-improvement claims.)*

Show the onboarding wizard's top-3 starter templates (pre-arrival sequence, review request, inquiry response). Required phrasing: **"starter templates"** — no intelligence claim (they are hand-crafted until MOAT #8 promotion powers the library).

---

### Act 1 — Watching: the Draft rung (4 min)

**Scenario (corpus #103, Pre-arrival, Low risk):** *Guest asks for Wi-Fi password before arrival.* Corpus default behavior: answer automatically when the underlying fact is verified and guest-facing; otherwise ask a narrow clarification or create a missing-info card.

**What the demo shows:** the guest message arrives; Cendra checks the reservation, access-code release policy, and housekeeping readiness; a reply appears in the queue as **"Ready for you to send."** The PM reviews and taps send.

> **Presenter:** "This is Draft — the entry rung. Cendra has done the work: verified the reservation, checked when the access code is allowed to go out, written the reply. You send it. Every automation starts here, on the bottom rung of a ladder — Draft, then semi-auto, then autopilot — and a workflow only climbs when its track record earns it." *[MOAT #1 — gate chain, `implemented` at observe; ladder = kernel `AutonomyState`, per the CEN-10 ruling]*

**Mandatory framing (CEN-10):** Draft is presented **only as the OBSERVE rung of the earned-autonomy ladder**. The standalone line "AI drafts your replies" is forbidden in all demo copy — it is a commodity claim on the clone-risk list.

**Second scenario for this beat (corpus #386, Post-stay, Low risk):** *PM wants to request review.* Same rung, PM-initiated direction — shows the assistant working both inbound and outbound. No additional claims.

---

### Act 2 — Scoring: calibrated abstention (5 min)

**Scenario (corpus #1, Pre-booking, High risk):** *Same-night inquiry after 22:00 from zero-review guest.* Corpus default behavior: do not resolve purely from language; build an evidence-backed Decision Card and route to PM approval when money, access, safety, or policy is involved.

**What the demo shows:** the inquiry arrives at 22:40. Cendra does **not** answer. It assembles the evidence — zero reviews, local guest, one-night weekend stay, same-night flag — and surfaces a card in **Needs Your Attention**: *"I'm not sure — this needs you,"* with the signals listed and a structured rationale for why it held back.

> **Presenter:** "This is the part most AI tools get wrong. When Cendra's confidence is below the threshold you set, it doesn't guess — it abstains, and it tells you exactly why. The threshold isn't a prompt; it's a calibrated statistical gate that learns its error rate from your property's own history. Refusing to act under uncertainty is a feature you can audit." *[MOAT #4 — calibrated abstention, `implemented`, the most-live gate in the chain]*

**Honesty constraint:** today, abstention **refuses the dispatch with a rationale**. The one-tap approve/expire queue (approval-gateway routing, #13) is **pending wiring** — the demo may show the rationale card, but must not stage a live "tap approve and Cendra executes" round-trip. If asked: "approval routing is on the G2 roadmap; today every held action is surfaced with its reasoning."

**Companion scenario (corpus #433, Internal operations, Medium risk):** *AI has missing property knowledge.* When Cendra can't answer because a property fact is missing (does the building allow late luggage drop?), it records the gap instead of improvising. **Zero-claim presentation** — see the clone-risk section below for the only permitted phrasing.

---

### Act 3 — Accruing: your ledger (4 min)

**Scenario (corpus #323, Upsell / revenue, Medium risk):** *Late checkout upsell proactive offer.* Corpus default behavior: supervised automation — check availability, cleaning schedule, and same-day arrivals first; draft the offer; auto-send only when policy, price, and availability are verified.

**What the demo shows:** Cendra proposes a paid late-checkout offer for a departing guest (no same-day arrival, cleaning schedule clear), the PM approves the draft, and a **Decision Card** is written: the situation, the signals checked, the action, the outcome. Then open **Assistant Performance**: the month's accrued cards.

> **Presenter:** "Every decision Cendra participates in — sent, held, or escalated — writes a record: what it saw, what it did, what happened. That's your ledger, and it's the asset. An operator in year three has a system that knows their property at a depth no competitor can replicate, because replicating it would mean replaying those three years. It starts accruing the day you turn observe mode on — which is why we turn it on in week one." *[MOAT #9 — compounding outcome ledger, `implemented` when gates are active]*

**Honesty constraints:** revenue-impact numbers shown must come from the demo tenant's real accrued cards (or be clearly labeled as illustrative mock data). Do not claim the ledger is already feeding learned behavior — cases are captured, not yet classified for learning (#9 status note), and self-improvement claims are forbidden (#7).

---

### Act 4 — Remembering: decision-time memory (3 min)

**Scenario (corpus #393, Post-stay, Low risk):** *Guest claims item was missing before arrival.* A guest disputes after checkout: "the coffee machine was already broken when we arrived."

**What the demo shows:** Cendra reconstructs what was known **at the time** — the pre-arrival inspection record, the cleaner's readiness confirmation, no in-stay reports — and drafts a grounded response for the PM.

> **Presenter:** "Most systems can tell you what they know now. Cendra can tell you what it believed last Tuesday, when the decision was made. Every fact carries two timestamps — when it was true and when we learned it — so a dispute isn't your memory against the guest's; it's a reconstruction of the record." *[MOAT #5 — bi-temporal belief memory, `implemented` via the retrieval loopback]*

---

### Safety Beat — "Urgent — Safety Issue" (2 min)

**Scenarios (corpus #209, Check-in day, Critical · #295, During stay, Critical):** *Guest reports gas smell* / *Guest reports carbon monoxide alarm.* Corpus behavior: immediate operational incident — acknowledge safely, collect minimal evidence, escalate to PM/on-call before any commitment. Never auto-reply; never learn a durable pattern from an emergency.

**What the demo shows:** the gas-smell message triggers the visually distinct **Urgent — Safety Issue** alert — instantly, bypassing the normal approval queue.

> **Presenter:** "Safety issues don't wait in a queue. A gas smell or a CO alarm pages you and your on-call team immediately. And note what Cendra does *not* do here: it doesn't improvise guidance, it doesn't make commitments to the guest, and it doesn't generalize from an emergency. The bypass skips the queue — it never skips the record: every critical escalation is still logged and still passes compliance checks."

**Binding presentation rules (CEN-10 ruling):** this is a **permitted no-claim surface**. It is presented as table-stakes operator responsibility — **never** as differentiation (nearest mechanism #14 is ruled *not a moat*). The governance line ("bypasses the queue, never the envelope" — ledger #9 + compliance monitor #10) is a factual engineering guard, not a sales claim.

---

### Act 5 — The Roadmap: autonomy is earned (4 min)

Open with the transparency disclosure already visible in Acts 1–4:

> **Presenter:** "You may have noticed the AI-transparency notice on every guest-facing message. That's live today — EU AI Act Article 50 disclosure is built in, and a compliance check is the first gate every action passes." *[#10/#14 monitor half — live. Present as posture; #14 is ruled not-a-moat, so no differentiation claim on the compliance stack itself.]*

Then the earned-autonomy roadmap — **explicitly labeled "this is where the product is going, not what ships today"**:

1. **Confidence Level (TrustMeter).** Show the per-automation score, display-only. Permitted: "every workflow has a confidence score that grows with verified outcomes and resets on mistakes — today you see it; promotion to higher autonomy rungs will be gated on it." *[MOAT #2 — `partial`, display-only; the "grows with history" claim holds only with gates ≥ observe, which the demo tenant runs]*
2. **Your rules, verified (owner policy).** Ground with corpus #445/#446 (Internal operations, Medium): *owner requests no discounts for a specific property* / *owner allows a special discount for repeat guests*. Permitted: "you write the rule once — 'no discounts on the Marina flat', 'repeat guests may get 10%' — and Cendra verifies it mathematically when you save it. Enforcement inside the action pipeline is on the roadmap." *[MOAT #6 — `partial`: authoring + save-time Z3 verification live; in-chain enforcement pending. Never stage a live "policy blocked this action" moment.]*
3. **Suggested Automations.** Permitted: "when the ledger shows you've made the same call enough times, Cendra will propose the automation and show you exactly what it would do — you approve the promotion." Spoken in future tense only. *[MOAT #8 — `partial`/roadmap: promotion path unbuilt; never demo as live]*
4. **Action Receipts.** If the receipts surface is shown at all, it appears with its **"pending"** label. Permitted: "every autonomous action will carry a signed receipt; the receipt format ships with the autonomy rungs that need it." *[MOAT #3 + #10 — `partial`: not minted today; demoing a signed receipt as live is forbidden]*

> **Presenter (close of act):** "So the honest sequence is: today Cendra watches, scores, and accrues your ledger. Each workflow earns its way up the ladder on its own record — and you flip the switch per workflow, never globally."

---

### Close (1 min)

> **Presenter:** "What you've seen live today: drafting on the bottom rung, calibrated abstention with reasons, the ledger accruing, decision-time memory, and compliance posture built in. What you saw as roadmap: confidence-gated promotion, mathematically verified rules, mined automations, signed receipts. The ask: run observe mode on your real inbox for thirty days. It costs you nothing in risk — nothing executes without you — and at the end you'll have a month of your own ledger and a report of what Cendra would have handled."

The 30-day observe-mode ask is the conversion mechanism and is deliberately aligned with the Moat Maturity Ruling: *ledger accrual is the schedule-critical moat — every month of OFF posture is a month the moat does not widen.*

---

## Zero-Claim Surfaces (clone-risk handling in the demo)

These surfaces may appear in the demo **with zero defensibility claims**. Scripted neutral lines are mandatory; anything stronger is out of policy.

| Surface | Status (Moat Fit Map) | Only permitted demo phrasing |
|---|---|---|
| **Property Knowledge** | Clone risk until bi-temporal anchoring (#5) lands (G2) | "Your house rules, amenity guides, and local recs live here and feed every answer." Nothing about it being unique or defensible. |
| **Knowledge Gap cards** (corpus #433–434) | Conditional anchor (#4 + #5); emission wiring is net-new G2 work — clone risk until it ships | "When Cendra doesn't know a property fact, it asks you once and files the answer." No provenance/defensibility claim until the abstention-emission wiring lands. |
| **Starter templates / Automation Templates** | Commodity until #8 promotion powers the library | "Starter templates to get you live in week one." Always "starter" — never "Cendra intelligence." |
| **Urgent — Safety Issue** | Permitted no-claim surface (differentiation barred outright) | Safety-responsibility framing only (see Safety Beat). |
| **Draft mode standalone** | Clone risk standalone; ladder framing mandatory | Only ever presented as the first rung of the earned-autonomy ladder (Act 1). |

---

## Traceability Index (acceptance: every defensibility statement → confirmed MOAT row)

| Demo beat | Corpus scenario(s) | Stage · Risk | Defensibility claim made | MOAT row · status | Live-claim allowed? |
|---|---|---|---|---|---|
| Cold open | — (onboarding framing §2) | — | History accrues per property; PM in control | #9 · `implemented` (gates active); #2/#4 framing | Yes, observe-mode terms |
| Act 1 — Draft rung | #103, #386 | Pre-arrival · Low; Post-stay · Low | Per-workflow earned-autonomy ladder | #1 · `implemented` (observe) | Yes (ladder framing only) |
| Act 2 — Abstention | #1 (+ #433 companion) | Pre-booking · High | Calibrated refusal with auditable rationale | #4 · `implemented` | Yes (no live approval-queue round-trip — #13 pending) |
| Act 3 — Ledger | #323 | Upsell/revenue · Medium | Compounding per-operator record | #9 · `implemented` when gates active | Yes (no learning claims — #7 not live) |
| Act 4 — Memory | #393 | Post-stay · Low | Decision-time belief reconstruction | #5 · `implemented` | Yes |
| Safety beat | #209, #295 | Check-in/During stay · Critical | **None (barred)** | #14 ruled not-a-moat; guard cites #9/#10 | No claims by ruling |
| Act 5.1 — Confidence Level | — | — | Score grows with verified outcomes | #2 · `partial` (display-only) | Display yes; gating claim no |
| Act 5.2 — Owner policy | #445, #446 | Internal ops · Medium | Save-time mathematical verification | #6 · `partial` (authoring live; in-chain pending) | Authoring yes; enforcement no |
| Act 5.3 — Suggested Automations | — | — | Mined-from-your-ledger templates | #8 · `partial`/roadmap | Future tense only |
| Act 5.4 — Action Receipts | — | — | Signed decision records | #3 + #10 · `partial` (not minted) | No — "pending" label mandatory |
| Act 5 open — Art. 50 | — | — | Transparency posture (no moat claim) | #10/#14 monitor half · live | Posture yes; differentiation no |

---

*Update this document when: the Moat Fit Map changes any row status (re-run the pre-demo checklist), a demo beat's scenario grounding changes, Compass revises terminology, or the board updates the demo-narrative rule. This script is invalid for external use until re-reviewed against the demo-narrative rule after any such change.*
