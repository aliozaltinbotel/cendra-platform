# PRD — Property Knowledge bi-temporal anchoring + Knowledge Gap cards (clone-risk remediation)

> **Owner:** Compass (Product Lead)
> **Status:** In review — Atlas accepts by merging
> **Date:** 2026-06-11
> **Issue:** [CEN-21](/CEN/issues/CEN-21) · **Program:** [CEN-11](/CEN/issues/CEN-11) G2 slate, Wave 2
> **Canonical inputs:** [`docs/product/moat-fit-map.md`](../moat-fit-map.md) on `main` (board-confirmed 2026-06-11, CEN-4 interaction `52576e43`); [CEN-10](/CEN/issues/CEN-10) clone-risk adjudication; CEN-15 engineering design (PR #11, pending Atlas adjudication — this PRD is consistent with it but stands alone).

This PRD remediates the two clone-risk rows that the moat-fit map scopes as one family: **Property Knowledge** (unanchored productization) and **Knowledge Gap cards** (conditionally anchored, emission wiring missing). Both become defensible only when wired to Brain mechanisms #5 and #4+#5 respectively. Until each wiring ships, the surfaces ship with **no differentiation claim**.

---

## 1. Map-row citations

Per the binding G2-PRD constraint, every row this PRD builds on, with its current Status as recorded in the map on `main`:

| Map row | Mechanism / surface | Status on map | Role in this PRD |
|---|---|---|---|
| Part A **#5** | Bi-temporal observation / belief memory — Redis bi-temporal KG (`memory/knowledge_graph.py`, `kg_as_of.py`) + epistemic store (`brain_epistemic`), served via External Knowledge API loopback `POST /v1/brain/retrieval` (**T6**) | **implemented** (Batch 2/3; retrieval endpoint Batch 5) | The anchor for Property Knowledge; the store Knowledge Gap cards persist into |
| Part A **#4** | Calibrated abstention — `core/brain/abstention/` `AbstentionGate` in the `runtime_gateway` chain; calibration in `brain_calibration` (T5) | **implemented** (most-live gate) | The emitter of Knowledge Gap cards |
| Part C | **Property Knowledge** (knowledge base for property docs) | **Unanchored PRODUCTIZATION → clone risk** | Surface remediated by §4 |
| Part C | **Knowledge Gap cards** (missing-info registry; corpus scenarios 433–434, adjudicated CEN-10) | **PRODUCTIZATION conditionally anchored — clone risk until wired** | Surface remediated by §5 |
| Clone-Risk Surfaces table | Both rows above: Property Knowledge "Anchor with bi-temporal metadata on all property docs (Brain moat #5). Timeline: G2." · Knowledge Gap cards "No kernel gap-registry exists today — wiring is net-new G2 work. No defensibility claim until it ships." | — | The remediation contract this PRD fulfills |

**Status nuance that governs every claim in this document:** mechanism row #5 is `implemented` — as-of reconstruction is live via the T6 retrieval loopback. What is **not** implemented is the *productization wiring* of these surfaces onto that mechanism: valid-time metadata on property docs, decision-time threading into retrieval, and the entire gap registry (code-checked 2026-06-11: no gap-registry concept exists in `api/core/brain` on `cendra/main`). A live mechanism does not license a live-surface claim.

---

## 2. Users and jobs-to-be-done

**Primary user — the STR/boutique-hotel operator** (owner-operator or property manager, 1–50 units, the design-partner profile):

- **JTBD 1 (Property Knowledge):** *"When a guest or my team asks about house rules, fees, amenities, or policies, answer from the docs that were in force at the moment that matters — not from whatever the corpus says today."* Canonical scenario: a guest disputes a cleaning fee charged 2026-03-02; the operator needs the house rules *as they stood on 2026-03-02*.
- **JTBD 2 (Knowledge Gap cards):** *"Show me what my Assistant could not answer — what it didn't know when it held back — so I can fill exactly those holes instead of guessing what documentation to write."*

**Secondary user — the operator's staff** (front office / guest comms): consumes time-aware answers inside guest-journey workflows; triages gap cards as a daily worklist.

Both jobs consume trust: the operator must be able to see *why* an answer or an abstention happened. Provenance is the product, not metadata.

---

## 3. Why now

- Both surfaces are listed in the map's Clone-Risk Surfaces table with timeline **G2**. Every demo that shows an unanchored knowledge base is a demo any Dify fork can copy.
- The ledger-accrual ruling (map, Moat Maturity Ruling) makes observe-mode posture for design partners a G2 priority; gap cards are the observe-mode surface that makes abstention *visible value* ("here is what I didn't know") rather than silent refusal.
- The evidence-pack format for design partners (G3 prep) needs decision-time provenance to exist before it can be packaged.

---

## 4. Product definition — Property Knowledge (time-aware)

### 4.1 Operator experience

1. **Authoring:** every Property Knowledge document (house rules, fee schedules, amenity descriptions, local guides) carries operator-visible **valid-time** fields: *effective from* / *effective until* (open-ended allowed, meaning "still in force"). Editing is part of the normal doc-upload flow — no separate "temporal admin" surface.
2. **Answering (default):** guest-journey workflows retrieve only documents currently in force. Expired house rules never leak into guest replies.
3. **Answering (as-of):** when a workflow runs on behalf of a past event — a dispute, a review response, an audit view — retrieval reconstructs the corpus **as it was believed at the decision-time of that event** and each retrieved chunk displays its provenance: *effective window, as-of timestamp, source document*.

### 4.2 Two rungs, only one is the moat

| Rung | What it is | Claim discipline |
|---|---|---|
| **Rung 1 — always-current docs** (valid-time filter on native retrieval) | Date metadata + filtered retrieval so answers use only in-force docs | **Table-stakes.** May be described as "your Assistant never quotes an expired policy." Never as a differentiator — any fork can build date filters. |
| **Rung 2 — as-of reconstruction** (kernel-served retrieval through the T6 loopback, `POST /v1/brain/retrieval`, with decision-time `as_of`) | "What did we believe at moment T" — depends on the operator's accumulated valid-time history, which a fork with today's corpus cannot replay | **The MOAT #5 anchor.** Differentiation claims permitted **only after** the Property-Knowledge anchoring reads `implemented` on the map. |

### 4.3 Acceptance criteria — Property Knowledge

- [ ] Every Property Knowledge doc type in the hospitality pack carries `valid_from`/`valid_to`; ingestion rejects docs without `valid_from`.
- [ ] A guest-journey workflow answering "what are the quiet hours?" uses only docs whose valid window contains now (Rung 1).
- [ ] Given a dispute about an event at time T, retrieval through the T6 loopback with `as_of = T` returns the docs in force at T, each carrying `{valid_from, valid_to, as_of, source_doc_id, asserted_at}` provenance (Rung 2).
- [ ] A doc updated after T does **not** appear in an `as_of = T` reconstruction.
- [ ] Operator UI displays the effective window on every retrieved chunk in audit/dispute views.
- [ ] The map row for Property Knowledge anchoring is flipped to `implemented` only when all of the above pass end-to-end on a design-partner-shaped dataset.

---

## 5. Product definition — Knowledge Gap cards

### 5.1 What a card is (and what makes it defensible)

A Knowledge Gap card is **not** an unanswered-questions list. Each card is the durable record of a moment the calibrated-abstention gate (#4) refused to act because it did not know enough, persisted in the epistemic store (#5) with decision-time provenance. Card anatomy, as surfaced to the operator:

| Field | Operator-facing meaning |
|---|---|
| What was asked | The guest query / intent the Assistant could not satisfy |
| What was missing | The missing fact or predicate (structured where available, else the abstention rationale) |
| When | Decision-time (`as_of`) — when the Assistant held back |
| Why it held back | Confidence vs. the operator-set threshold |
| What it *did* know | Link to the belief snapshot at `as_of` (`kg_snapshot_ref`) |
| Status | `open` → `answered` (a later doc covers the gap) or `dismissed` |

### 5.2 Operator experience

1. **Worklist:** cards appear in the console as a triage list, newest first, filterable by journey stage and status. Each card is one abstention event (dedup presentation at the read layer is acceptable; storage stays per-event).
2. **Close the loop:** answering a card (uploading/extending a Property Knowledge doc) transitions it to `answered` — a filled gap becomes belief. This is the visible flywheel between the two surfaces in this PRD.
3. **Observe-mode value:** in observe posture the Assistant isn't acting autonomously, but it *is* abstaining and recording. Gap cards are the first artifact that makes observe mode worth watching — "your ledger of what Cendra didn't know is accruing today."

### 5.3 Acceptance criteria — Knowledge Gap cards

- [ ] A gap card is created **only** by the abstention gate's emission hook — there is no manual "add gap" path (manual notes are a different surface; mixing them would destroy the provenance claim).
- [ ] Each card persists in the epistemic store with `as_of`, confidence, threshold, and `kg_snapshot_ref` populated.
- [ ] Cards are readable per property via the kernel read API (`GET /v1/brain/knowledge-gaps/<property_id>`) and render in the console worklist.
- [ ] Filling the missing doc transitions the card to `answered` and the answering doc is linked.
- [ ] Zero defensibility claims anywhere in product copy until the emission wiring row reads `implemented` (CEN-10 ruling restated).

---

## 6. Capabilities consumed (brain / service_api)

| Capability | Source | Exists today? |
|---|---|---|
| As-of retrieval over bi-temporal KG | `POST /v1/brain/retrieval` (T6 loopback) over `kg_as_of.py` / `brain_epistemic` | **Yes** (map #5 `implemented`) — needs `as_of` request arg + provenance fields in the response (Platform ask P1) |
| Calibrated abstention events | `AbstentionGate` in the `runtime_gateway` chain; calibration in `brain_calibration` (T5) | **Yes** (map #4 `implemented`) — needs the gap-emission hook (Platform ask P4) |
| Gap registry storage | epistemic store (`brain_epistemic`; proposed `brain_gap` / `brain:gap:`) | **No — net-new** (Platform asks P4–P6) |
| Gap read API | `GET /v1/brain/knowledge-gaps/<property_id>` (service_api/brain, mirroring the TrustMeter read-API pattern) | **No — net-new** (Platform ask P6) |
| Decision-time stamped on runs and threaded to retrieval | kernel run context + Dify retrieve call | **No — net-new wiring** (Platform asks P2–P3) |

---

## 7. Maturity — live vs. roadmap (binding on demos and copy)

Per the map's Moat Maturity Ruling (binding on G2 PRDs):

**Live today (demo-safe at observe posture):**
- As-of belief reconstruction via the T6 retrieval loopback (mechanism #5, `implemented` Batch 2/3 + Batch 5 endpoint).
- Calibrated abstention (#4, `implemented`, most-live gate; Wilson path active at generic dispatch).

**Designed / net-new — roadmap only, never demo as shipping:**

| Item | Depends on | Batch/wave dependency |
|---|---|---|
| Valid-time metadata convention on property docs (Rung 1) | Dify dataset metadata + retrieval-node manual filtering (existing Dify capability, config + convention) | G2 implementation wave; no kernel batch dependency |
| Decision-time threading into retrieval (Rung 2 end-to-end) | net-new Dify-side wiring into the External-Knowledge retrieve call; `as_of` arg on T6 | G2 implementation wave; builds on Batch 4 (T6 touchpoint) + Batch 5 (retrieval endpoint) — both landed |
| Valid-time ingest into the epistemic store | kernel document-ingest hook extension | G2 implementation wave; builds on Batch 2/3 stores — landed |
| Gap registry (emission, storage, lifecycle, read API) | **net-new kernel work**; no gap-registry exists on `cendra/main` (code-checked 2026-06-11) | G2 implementation wave; builds on Batch 4 gate chain + Batch 5 service_api — landed. Sharper gap rationales improve when agent-loop confidence flows (the #4 conformal path note) |
| Gap cards console surface | gap read API above | After read API; console work, no core-edit |

**Claim gate:** no differentiation claim for either surface until its wiring row on the map reads `implemented`. The abstention mechanism being live does not make gap cards "live" — the cards do not exist until emission wiring ships.

---

## 8. Hospitality copy rules

Binding on console copy, marketing, demo scripts, and design-partner materials:

1. **Observe-mode story only** (map demo-narrative rule): *"Cendra is watching, scoring, and accruing your ledger today; autonomy is earned and switched on per workflow."* Neither surface may be demoed as live autonomous capability.
2. **Permitted now (table-stakes framing only):**
   - "Your Assistant never quotes an expired policy." (Rung 1, once shipped)
   - "Cendra keeps a record of what it didn't know, so you can fill exactly those gaps." (descriptive, no defensibility framing, only once emission wiring ships)
3. **Permitted after the respective row flips to `implemented`:**
   - "Cendra remembers what the booking situation looked like last Tuesday when it made that pricing decision — not just what it knows now." (map row #5 operator translation)
   - "Here is what the system did not know when it abstained" — the gap-card provenance claim.
4. **Forbidden always:**
   - Any "zero core edits" claim — the maintained fork (T1/T3 core edits) is a governed cost of the moat.
   - Marketing a plain missing-info list or date-filtered KB as differentiation (CEN-10 / Clone-Risk table).
   - Autonomy-level claims that haven't cleared their promotion gate.
5. **Vocabulary:** operator-facing copy says *effective from / effective until* and *as it stood on \<date\>* — never "valid-time," "bi-temporal," "epistemic," or "abstention" (internal terms). The abstention event surfaces to operators as *"your Assistant held back."*
6. **License track is board-owned** — no Dify branding, copyright, or LICENSE changes from product work, ever.

---

## 9. Platform asks

Capabilities this PRD needs that do not exist (or need extension). **For Atlas to convert into interface issues toward Forge's org — Packs/Vista/Pixel must not implement these, and this issue does not create them.** Consistent with the CEN-15 design change ledgers (PR #11).

| # | Ask | Layer | New or extend |
|---|---|---|---|
| P1 | `as_of` argument on the T6 External-Knowledge retrieve path (`POST /v1/brain/retrieval`) + bi-temporal provenance fields (`valid_from`, `valid_to`, `as_of`, `source_doc_id`, `asserted_at`) on every returned chunk | kernel (service_api/brain) | extend existing |
| P2 | Decision-time `T` stamped on each run (inbound-event timestamp semantics — pending Atlas ruling on CEN-15 open question 1) and exposed to the retrieve call | kernel run context (T1/T3 hooks) | extend existing |
| P3 | Decision-time threading from the Dify run into the External-Knowledge retrieve request (`knowledge_retrieval_node.py`) + External-Knowledge binding & `valid_from`/`valid_to` date-metadata convention on Property Knowledge datasets | Dify side (Flow's lane per CEN-15 §C) | net-new wiring + config |
| P4 | `GapRecord` emission hook at the abstention decision point in `core/brain/abstention/` (fires only on abstain; no new gate slot, chain order unchanged) | kernel | **net-new** |
| P5 | Gap persistence in the epistemic store (`brain_gap` table / `brain:gap:` keyspace) with `status` lifecycle (`open`/`answered`/`dismissed`) and `kg_snapshot_ref` linkage | kernel | **net-new** |
| P6 | Read API `GET /v1/brain/knowledge-gaps/<property_id>` with per-event storage, dedup-at-read presentation | kernel (service_api/brain) | **net-new** |
| P7 | Valid-time ingest from Dify doc metadata into the epistemic store at index time | kernel document-ingest hook | extend existing |
| P8 | Gap-card console surface consuming P6 (worklist, status transitions, link-to-answering-doc) | console (Pixel's lane, once P6 contract is published) | net-new surface, no core-edit |

Interface contract (per CEN-15): kernel publishes the retrieve-request schema including `as_of` and the gap read-API schema; product surfaces consume both without touching the kernel.

---

## 10. Out of scope

- Auto-drafting answers to gap cards (would re-open the Draft-mode standalone framing risk — CEN-10 ruling; only the earned-autonomy-ladder framing is permitted).
- Gap-to-doc auto-generation loop (CEN-15 §B.4 "loop-back" — explicitly later).
- Any HITL routing of abstentions to approval queues (map row #13, separate track).
- LICENSE / branding — board-owned.

## 11. Open product decisions

1. **Rung-1-first shipping** (CEN-15 open question 3): recommend shipping Rung 1 ahead of Rung 2, labeled strictly table-stakes ("always-current docs"), because expired-policy leakage is a real operator pain today and the copy rules above prevent over-claiming. Atlas to confirm with the CEN-15 adjudication.
2. **Gap-card volume control:** per-event storage with dedup-at-read (recommended in CEN-15 open question 2) — product accepts; the worklist must group repeat gaps or operators with chatty properties will face card floods.
3. **`valid_from` required at ingest** (acceptance criterion §4.3): strict-require vs. default-to-upload-date for migrated corpora. Recommendation: default existing docs to upload date with an "unverified window" badge; require explicit windows for new docs.
