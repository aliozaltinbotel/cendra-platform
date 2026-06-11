# Demo Scenario Content Pack (Acts 1–4)

> **Owner:** Packs (Hospitality Solutions) · **Issue:** CEN-34 (child of CEN-20) · **Reviewer:** Compass
> **Companion of:** [demo-narrative.md](./demo-narrative.md) (CEN-20, PR #15) — that file fixes the *beats* (journey stage, mechanism, permitted claim per act); this file supplies the Packs-owned *content*: concrete corpus scenario IDs, verbatim guest-message threads, and property fixture data.
> **Binding constraint:** every line below is checked against the Claims Register in `demo-narrative.md`. Nothing in this pack demonstrates or implies a Part 2 (FORBIDDEN) capability as live. Per register rule R6, all scenario-volume claims cite **473 scenarios across 9 journey stages**.
> **Corpus source:** scenario IDs reference the catalog in `reference/brain_engine/Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md` §9 (on `cendra/main`).
> **Posture:** one demo workspace, `BRAIN_GATES_MODE=observe`. Every Cendra reply below is a **draft** waiting in "Ready for you to send"; nothing auto-sends.

---

## Shared property fixture

The whole demo runs on one property and one booking so the ledger (Act 3) and the as-of retrieval (Act 4) visibly reference the dispatches created in Acts 1–2. The cast uses the corpus persona vocabulary: **guest / PM / owner / cleaner / vendor**.

```yaml
property:
  id: LHL-204
  name: "Lighthouse Loft"
  type: 2-bedroom apartment, Lisbon (rooftop shared plunge pool, seasonal)
cast:
  pm:      "Dana Ferreira — property manager, 38 listings"   # corpus persona: PM
  owner:   "Marco V. — unit owner, approval persona"          # corpus persona: owner
  cleaner: "Rui — turnover crew"                              # corpus persona: cleaner
  vendor:  "AquaSol Pools — pool service contractor"          # corpus persona: vendor
  guest:   "Maya R. — Airbnb guest, 2 adults"                 # corpus persona: guest
booking:
  ref: LHL-204-7741
  channel: Airbnb
  stay: 2026-06-18 → 2026-06-22 (4 nights)
  deposit: €300 hold
house_policy:                       # PM-authored property policy (not Cendra intelligence)
  check_in: "16:00; door code released 15:00 on arrival day, after ID verification"
  early_check_in: "€25, only if cleaning complete and no same-day turnover"
  late_checkout: "€40 until 14:00, only if no same-day arrival"
property_knowledge_seeded:          # facts Cendra HAS at demo start
  - wifi name/password, parking instructions, AC controls, quiet hours
  - "rooftop plunge pool: shared, seasonal"   # note: NO heating temperature, extension policy, or fee on file — deliberate (Act 2)
```

> **Demo-prep note:** the pool-heating specifics are deliberately absent from Property Knowledge. Do not "fix" this fixture before the demo — Act 2's abstention depends on it being honestly unanswerable.

---

## Act 1 — Watching: early check-in + door code, drafted not sent

*Stage: Pre-arrival → Check-in day.*

**Corpus scenarios cited:**

| ID | Catalog title | Stage | Risk | Auto-reply? |
|---|---|---|---|---|
| **102** | Guest asks for smart lock code | Pre-arrival | High | No — escalate to PM |
| **176** | Guest wants early check-in but property is not ready | Check-in day | High | No — escalate to PM |
| **205** | Guest arrives before access code release time | Check-in day | High | No — escalate to PM |

**Thread (Airbnb messages):**

> **Guest (Maya R.) — Jun 17, 19:42**
> Hi! Our flight lands at 9:30am tomorrow so we'd be at the apartment around 11. Any chance of an early check-in? Also, could you just send us the door code now so we don't have to bother you in the morning? Thanks!

> **Cendra draft — Jun 17, 19:43 · status: “Ready for you to send”**
> Hi Maya! Thanks for letting us know your arrival time. Two things:
>
> **Early check-in:** standard check-in at Lighthouse Loft is 16:00. Early check-in (€25) is possible when cleaning is finished and there's no same-day turnover — I'll confirm by 9:00 tomorrow whether 11:00 works, once the cleaning schedule for your arrival day is final.
>
> **Door code:** for security, the door code is sent at 15:00 on arrival day, once ID verification is complete — I can't share it earlier, but it will be with you well before you reach the apartment. If early check-in is confirmed, the code comes with the confirmation instead.
>
> Anything else before your trip? Safe travels!
> — *Drafted with AI assistance; reviewed and sent by your host.*

**Why the draft looks like this (corpus grounding):** scenario 102's rule is *never reveal access credentials before verification and release policy* — so the draft contains **no code**, only the release policy. Scenario 176/205 handling routes the early-check-in decision to the PM because money and access are involved (`Should AI Auto-Reply: No`).

**What the demo shows:** the draft sits in "Ready for you to send." Dana (PM) checks the turnover feed — Rui (cleaner) has the unit marked *done by 13:00, no same-day turnover* — edits the draft to offer 13:30 for €25, and sends. The edit is recorded: the Assistant Performance row for this dispatch shows *drafted → PM-edited → sent*.

**Claims check (Act 1):** exercises C4 (gate chain, observe posture named), C2 (the dispatch and the PM's override accrue to the ledger), C6 (Draft presented only as the OBSERVE rung — the "Say" block in `demo-narrative.md` Act 1 carries the ladder framing). Standing rule R1 respected: no standalone "AI drafts your replies" line anywhere in this act.

---

## Act 2 — Scoring: pool-heating question Cendra cannot answer

*Stage: During stay.*

**Corpus scenarios cited:**

| ID | Catalog title | Stage | Role in this act |
|---|---|---|---|
| **268** | Guest asks for pool heating extension | During stay | The guest question |
| **237** | Guest says amenity is missing | During stay | Adjacent family (missing-info registry routing) |
| **433** | AI has missing property knowledge | Internal operations | The Knowledge Gap card |
| **434** | AI finds repeated missing info | Internal operations | Gap card aggregation across repeats |

**Thread (WhatsApp):**

> **Guest (Maya R.) — Jun 19, 16:05**
> Hey, quick question — the rooftop pool is pretty cold. Can the heating be turned up or extended for tomorrow? And what temperature does it actually get to?

**Cendra abstains. The structured rationale (shown on screen, verbatim fixture):**

```text
ABSTAINED — no draft produced
Asked:        pool-heating extension availability + max temperature (LHL-204)
Consulted:    Property Knowledge → amenity record "rooftop plunge pool: shared, seasonal"
Missing:      heating temperature spec · extension policy · extension fee
Confidence:   0.31 — below the 0.85 threshold for guest-impacting amenity answers
Action:       declined to draft a guess; question surfaced as a Knowledge Gap card
```

**Knowledge Gap card (fixture):**

```text
KNOWLEDGE GAP · LHL-204 · open
"Pool heating — temperature and extension policy"
First asked: Jun 19, 16:05 (WhatsApp, booking LHL-204-7741)
```

**What the demo shows:** Dana forwards the question to AquaSol Pools (vendor); when the vendor answers, Dana saves the fact into Property Knowledge and resolves the card. The resolution is a **PM action** — the demo presenter performs it by hand.

**Honesty notes (binding, restated from `demo-narrative.md`):**
- The abstention **refuses with a rationale**. Do **not** demo or describe one-tap approval routing into an operator queue — that is designed, not live (F6).
- The gap card is presented as a **workflow convenience only** — zero defensibility claims (R3; no gap-registry mechanism exists in the kernel today). The card's value framing stays at "you can see what guests asked that you haven't documented," nothing more.

**Claims check (Act 2):** exercises C1 (refusal + rationale). F6 avoided (no routing flow), R3 applied (no defensibility language on the card), F3 avoided (no "Cendra will learn this" phrasing — the PM saves the fact manually).

---

## Act 3 — Accruing: late-checkout fee offer, PM-approved, outcome recorded

*Stage: Upsell / revenue.*

**Corpus scenarios cited:**

| ID | Catalog title | Stage | Role in this act |
|---|---|---|---|
| **300** | Late checkout request with no same-day arrival | Upsell / revenue | The request + offer |
| **302** | Late checkout request on checkout morning | Upsell / revenue | Adjacent family (timing variant) |

**Thread (Airbnb messages):**

> **Guest (Maya R.) — Jun 21, 10:12**
> Our flight back isn't until the evening tomorrow — could we get a late checkout?

> **Cendra draft — Jun 21, 10:13 · status: “Ready for you to send”**
> Hi Maya! Yes — late checkout until 14:00 is available on Jun 22 for €40. Cleaning for the next arrival isn't until later that afternoon, so 14:00 works on our side. Want me to set it up? You can accept the charge right in this thread.
> — *Drafted with AI assistance; reviewed and sent by your host.*

**Checks the draft ran before proposing (shown in the dispatch detail):** next booking at LHL-204 starts Jun 24 → *no same-day arrival* (scenario 300's gating fact); Rui's cleaning slot Jun 22 15:00 → 14:00 checkout compatible; €40 fee from PM-authored house policy.

**What the demo shows:** Dana sends the draft unchanged. Maya accepts and pays through the channel. Then open **Assistant Performance**: the Acts 1–2 dispatches are already there, and this one lands with *action: late-checkout offer · approval: PM-sent · outcome: accepted, €40* — an append-only DecisionCase per gated dispatch.

**Honesty guards (binding):**
- "PM-approved" here means **Dana manually sends the draft** — the observe rung. It is not an approval queue or one-tap flow (F6).
- The €40 offer comes from **house policy the PM authored**. Do not describe the offer as mined or suggested from history (F4) — no "Cendra noticed you usually charge €40" phrasing.
- The ledger claim stays conditional on gates ≥ observe, per C2's required phrasing.

**Claims check (Act 3):** exercises C2 (ledger), C4 (gated dispatch), C6 (ladder framing carried by the narrative's Act 3 "Say" block). F4 and F6 explicitly avoided.

---

## Act 4 — Memory: deposit dispute vs. what Cendra believed at decision-time

*Stage: Post-stay.*

**Corpus scenarios cited:**

| ID | Catalog title | Stage | Role in this act |
|---|---|---|---|
| **384** | Guest disputes damage claim | Post-stay | The dispute |
| **350** | Cleaner reports damage | Check-out | The originating report (fixture timeline) |
| **392** | Guest asks for deposit return status | Post-stay | Adjacent family (deposit-state questions) |

**Fixture timeline (before the dispute):**

```text
Jun 22 11:30  Rui (cleaner) logs turnover report: ceramic table lamp broken, 2 photos attached
Jun 22 18:00  Marco V. (owner) approves a €120 damage charge   # 384: damage claims need PM/owner approval
Jun 22 18:04  Cendra drafts the damage-charge message + charge against the €300 deposit hold
Jun 22 18:20  Dana (PM) reviews and sends; charge submitted    # DecisionCase recorded
```

**Thread (email, five days later):**

> **Guest (Maya R.) — Jun 27, 09:48**
> I just saw a €120 charge from my deposit for a "broken lamp". That lamp was already cracked when we arrived — we never touched it. I'd like the €120 back, or I'll take this up with Airbnb.

**The as-of retrieval (the demo's money shot).** Dana asks: *"Show me what the booking situation looked like when the damage charge was drafted."* The panel reconstructs decision-time state (Jun 22, 18:04):

```text
AS-OF Jun 22 2026, 18:04 — booking LHL-204-7741
Deposit:        €300 hold, active
Damage report:  cleaner turnover report Jun 22 11:30, 2 photos (timestamps verified)
Guest thread:   no damage mention by guest at any point during stay (Jun 18–22)
Approval:       owner Marco V., Jun 22 18:00
Charge:         €120 drafted 18:04, PM-sent 18:20
```

Two clocks, as scripted in `demo-narrative.md` Act 4: *when a fact became true* vs. *when Cendra learned it*. The dispute answer isn't the system's current opinion — it's a reconstruction of exactly what was believed when the decision was drafted. Cendra then drafts the dispute response for Dana (evidence summary + channel-appropriate language), and it waits in "Ready for you to send" — money-impacting decisions never move without the configured approval path (scenario 384).

**Honesty guards (binding):**
- This is a **bi-temporal reconstruction** (C3, map row #5 — implemented, T6 retrieval loopback live). It is **not** a "signed receipt" or "tamper-proof record" — that phrasing is F1-forbidden in any tense that implies it ships today.
- No refund or charge reversal happens autonomously; corpus 384 requires evidence + PM/owner approval before any guest-facing fee language.

**Claims check (Act 4):** exercises C3 (as-of reconstruction), C2 (the original charge decision is on the ledger), C4. F1 explicitly avoided ("reconstruction," never "receipt"); F7 avoided (no "Cendra blocked the charge until ID verified" framing).

---

## Claims-register compliance summary

Line-check of this pack against the register in `demo-narrative.md`:

| Act | Corpus IDs cited | Permitted claims exercised | Forbidden rows checked clean |
|---|---|---|---|
| 1 | 102, 176, 205 | C2, C4, C6 | F2 (no TrustMeter gating shown), F7 (code withheld by *policy in the draft text*, not a live precondition blocker) |
| 2 | 268, 237, 433, 434 | C1 | F6 (no routing queue), F3 (no self-learning phrasing), R3 (gap card claims nothing) |
| 3 | 300, 302 | C2, C4, C6 | F4 (offer is house policy, not mined), F6 (PM sends manually) |
| 4 | 384, 350, 392 | C2, C3, C4 | F1 (reconstruction ≠ signed receipt), F7 |

Global rules applied throughout: **R1** (Draft only as the OBSERVE ladder rung), **R6** (473 scenarios / 9 journey stages wherever volume is mentioned), **R7** (no Dify branding touched anywhere in demo materials). The disclosure footer on guest-facing drafts reflects C5 (AI disclosure on guest-facing output) — framed as responsibility, never sold standalone (per C5's phrasing discipline).

## Change control

Bound to `demo-narrative.md` change control: if Atlas flips a Moat Fit Map row and Compass promotes an F-row to a C-row, the corresponding honesty guard here may be relaxed **by PR against this file**, Compass-reviewed. Scenario IDs are stable references into the corpus catalog §9; if the catalog is renumbered, this file must be updated in the same PR.
