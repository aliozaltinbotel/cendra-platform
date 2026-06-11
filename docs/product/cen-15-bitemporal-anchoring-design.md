# CEN-15 — Bi-Temporal Anchoring Design

> **Owner:** Porter (Brain Kernel Engineer)
> **Status:** Design — for Atlas adjudication
> **Date:** 2026-06-11
> **Issue:** [CEN-15](/CEN/issues/CEN-15) · **Parent input:** [CEN-4](/CEN/issues/CEN-4) / [CEN-10](/CEN/issues/CEN-10)
> **Scope:** Clears the two conditional clone-risk rows in [`moat-fit-map.md`](./moat-fit-map.md) §"Clone-Risk Surfaces": (A) **Property Knowledge** → anchor to MOAT **#5** (bi-temporal belief memory); (B) **Knowledge Gap cards** → net-new emission from MOAT **#4** (calibrated abstention) into MOAT **#5** (epistemic store) with decision-time provenance.

## 0. Binding inputs & constraints

This design is bound by [`moat-fit-map.md`](./moat-fit-map.md) on `main` (board-confirmed 2026-06-11, CEN-4 interaction `52576e43`). Map rows this design builds on:

- **Part A row #5** — *Bi-temporal observation / belief memory*: Redis-backed bi-temporal KG (`memory/knowledge_graph.py`, `kg_as_of.py`, `brain:kg:` keyspace) + epistemic Postgres store (`brain_epistemic`), served to Dify via the **External Knowledge API** (touchpoint **T6** retrieval loopback). Status **implemented** (live via T6 retrieval loopback).
- **Part A row #4** — *Calibrated abstention*: `core/brain/abstention/` `AbstentionGate` in the `runtime_gateway` gate chain (`core/brain/gates.py`: Compliance → Certificate → Abstention → Risk); calibration persisted in `brain_calibration` (T5). In enforce mode it refuses the dispatch with a rationale. Status **implemented** (most-live gate).
- **Part C clone-risk rows** — *Property Knowledge* (unanchored → clone risk) and *Knowledge Gap cards* (CONDITIONAL — anchor assigned to #4+#5, emission wiring does not exist; code-checked 2026-06-11, no gap-registry in `api/core/brain` on `cendra/main`).

**Constraints applied throughout (per the G2-PRD rules in the map):**

1. **No claim of live capability until the mechanism row reads `implemented`.** Property Knowledge's *as-of* anchor and the Knowledge Gap registry are **net-new G2 work**; both ship behind a "no-defensibility-claim" rule until their rows flip. Restated per-row in §6.
2. **No zero-core-edit framing.** This is a maintained fork. Property Knowledge's as-of path rides the External Knowledge API (a Dify-native extension point), but the kernel it calls is the governed fork (T1/T3 edit upstream files). No PRD/marketing may claim zero-core-edit integration.
3. **License track is board-owned.** Nothing here touches Dify branding, copyright, or the multi-tenant clause; no LICENSE change is implied.
4. **Cite the map row(s) you build on** — done above and inline.

A note on geography: the brain kernel (`api/core/brain/**`, `reference/brain_engine/`, `FORK_LEDGER.md`) lives on the `cendra/*` fork branches, **not** on this `main` checkout. `main` carries upstream Dify + the product docs. File paths in the "Dify side" columns below were code-verified against this checkout; "Kernel side" paths are quoted from the map row catalog (verified at CEN-6) and land on `cendra/main`.

---

## Part A — Property Knowledge → MOAT #5 (bi-temporal belief memory)

### A.1 The problem the anchor solves

A plain Dify knowledge base answers "what does the corpus say **now**." Any STR-focused Dify fork can replicate that. The defensible question is **"what was true / what did the system believe at decision-time T"** — e.g. a guest disputes a cleaning fee charged on 2026-03-02; the operator needs the *house rules as they stood on 2026-03-02*, not today's. Reconstructing belief-as-of-T requires the operator's own valid-time history, which is exactly MOAT #5 and is not cloneable by a fork that only stores current docs.

### A.2 The bi-temporal model

Every Property Knowledge fact carries two independent time axes (standard bi-temporal):

| Axis | Meaning | Source |
|---|---|---|
| **valid-time** (`valid_from`, `valid_to`) | the real-world window the fact is *true about the property* (e.g. "quiet hours are 22:00–08:00, effective 2026-01-01 → open") | operator-asserted at document/chunk authoring; `valid_to = null` ⇒ still in force |
| **decision-time** (`tx_time`/`as_of`) | the instant a retrieval/decision *observed* the corpus | stamped by the kernel at the moment of the run that triggered retrieval |

"Time-aware retrieval" = given a decision-time `T`, return only facts whose valid-time window contains `T`, reconstructed as the corpus was known at `T`. `kg_as_of.py` already implements this reconstruction over the `brain:kg:` keyspace; the epistemic store (`brain_epistemic`) is the durable Postgres mirror.

### A.3 Two retrieval paths (ship both; only Path 2 is the anchor)

**Path 1 — Dify-native valid-time filter (table-stakes rung, config-only).**
Store `valid_from` / `valid_to` as **date-typed dataset metadata** (`DatasetMetadata` + `DatasetMetadataBinding`, persisted in the `doc_metadata` JSON column — `api/models/dataset.py`). Configure the knowledge-retrieval node in **`manual`** metadata-filtering mode (`metadata_filtering_mode = "manual"`, `api/core/workflow/nodes/knowledge_retrieval/knowledge_retrieval_node.py`) with conditions resolved by `_resolve_metadata_filtering_conditions`:

```
valid_from  ≤  <now>      AND  ( valid_to is empty  OR  valid_to ≥ <now> )
```

This surfaces only *currently-valid* docs. It is honest, useful, and **cloneable** — date metadata is not a moat. It is the fallback / first rung, **not** the differentiator.

**Path 2 — kernel-served as-of loopback (the MOAT #5 anchor).**
Bind each Property Knowledge dataset as an **External Knowledge** dataset (`ExternalKnowledgeApis` / `ExternalKnowledgeBindings`, `api/services/external_knowledge_service.py`) pointing at the kernel's External-Knowledge endpoint (touchpoint **T6**). On retrieval the kernel:

1. reads the **decision-time `T`** carried on the request (the run's inbound-event timestamp, threaded from the agent run — see Flow scope §A.5);
2. calls `kg_as_of(T)` to reconstruct the corpus as believed at `T` (valid-time window contains `T`, no facts asserted after `T`);
3. returns chunks **plus provenance**: each result carries `{valid_from, valid_to, as_of: T, source_doc_id, asserted_at}`.

Path 2 is defensible because the as-of reconstruction depends on the operator's accumulated valid-time history — a fork with today's corpus cannot answer "as of 2026-03-02." This is the row that must read `implemented` before any Property-Knowledge differentiation claim.

### A.4 Provenance & storage

- **Storage of belief:** valid-time lives in two mirrored places — Dify `doc_metadata` (for Path-1 filtering and operator-visible editing) and the kernel epistemic store `brain_epistemic` / `brain:kg:` keyspace (authoritative for as-of reconstruction). The kernel is source-of-truth for as-of; Dify metadata is a denormalized projection for native filtering. **Sync direction:** operator edits land in Dify metadata and are ingested into the epistemic store by the kernel's document-ingest hook (same path that already feeds the KG at index time).
- **Provenance on every Path-2 result:** `as_of`, `valid_from/valid_to`, `source_doc_id`, `asserted_at`. This is what makes the outcome ledger (#9) auditable at decision-time and is the provenance the demo narrative leans on ("here is the rule as it stood when we acted").

### A.5 Change ledger — Property Knowledge

| Layer | Change | Where | New? |
|---|---|---|---|
| Kernel | Stamp decision-time `T` on each run and expose it to the External-Knowledge retrieve call | `core/brain/**` run context; existing T1/T3 hooks | extend existing |
| Kernel | As-of retrieve handler returning chunks + bi-temporal provenance | External-Knowledge endpoint over `kg_as_of.py` / `brain_epistemic` | extend existing (T6 already serves; add `as_of` arg + provenance fields) |
| Kernel | Ingest `valid_from/valid_to` from Dify `doc_metadata` into epistemic store at index time | document-ingest hook | extend existing |
| **Dify (Flow)** | `valid_from` / `valid_to` date metadata convention on Property Knowledge datasets | `DatasetMetadata`, `doc_metadata` (`api/models/dataset.py`) | config + small svc |
| **Dify (Flow)** | Bind Property Knowledge datasets to kernel External-Knowledge endpoint | `external_knowledge_service.py` | config |
| **Dify (Flow)** | Thread decision-time `T` into the External-Knowledge retrieve request from the run | `knowledge_retrieval_node.py` retrieve call / agent run context | **net-new wiring** |
| **Dify (Flow)** | Path-1 manual-mode date filter as table-stakes fallback | `knowledge_retrieval_node.py` node config | config |

The decision-time threading (row 6) is the **substantial** Dify item → delegated to Flow as a child issue (§C).

---

## Part B — Knowledge Gap cards → #4 emission into #5 (net-new)

### B.1 The anchor

A "missing-info list" is cloneable. The defensible version: a gap card is **emitted by the calibrated-abstention gate (#4)** at the moment it refuses, recording *what the system did not know when it abstained*, and is **persisted in the epistemic store (#5)** with **decision-time provenance**. That record cannot be reproduced without the operator's own abstention history. Code check (CEN-10, 2026-06-11, `cendra/main`) confirms **no gap-registry exists today** — this is net-new G2 kernel work.

### B.2 Emission

Today `AbstentionGate` (`core/brain/abstention/` in the `gates.py` chain) refuses a low-confidence dispatch with a rationale and records the outcome to the calibration window (`brain_calibration`). Net-new: at the abstention decision point, also emit a **gap record**:

```
GapRecord {
  gap_id, property_id, run_id,
  query / intent the system could not satisfy,
  missing_predicate     # what it did not know (structured where available, else the rationale)
  confidence, threshold,  # why #4 abstained
  as_of                 # decision-time = the run's inbound-event timestamp
  kg_snapshot_ref       # pointer to what #5 *did* know at as_of (links gap ↔ belief)
  status                # open | answered | dismissed
}
```

Emission is a hook inside the abstention gate — no new gate slot, no change to the chain order (Compliance → Certificate → Abstention → Risk). It fires only when #4 abstains, so the registry is literally "the operator's history of what Cendra didn't know."

### B.3 Storage

Persist `GapRecord` in the epistemic store (`brain_epistemic`) as a new gap entity / keyspace (`brain:gap:` + Postgres table `brain_gap`). It sits in #5 by construction, so each gap is co-located with the bi-temporal belief snapshot (`kg_snapshot_ref`) and inherits decision-time provenance. `status` transitions to `answered` when a later Property-Knowledge doc covers the missing predicate (closes the loop: a gap that the operator fills becomes belief).

### B.4 Retrieval / surfacing

- **Read API (kernel):** `GET /v1/brain/knowledge-gaps/<property_id>` (service_api/brain), mirroring the existing TrustMeter read API pattern — returns open gaps with provenance.
- **Surface (Dify/console):** the "Knowledge Gap cards" UI reads that API. Minimal Dify wiring — a console/service-API surface, no core-edit, no new workflow node. Each card shows *what was asked, what was missing, when (as_of), and the abstention rationale* — decision-time provenance is the product.
- **Loop-back (optional, later):** answered gaps can seed new Property-Knowledge docs, tightening #5 over time.

### B.5 Change ledger — Knowledge Gap cards

| Layer | Change | Where | New? |
|---|---|---|---|
| Kernel | `GapRecord` entity + emission hook on abstention decision | `core/brain/abstention/` | **net-new** |
| Kernel | Gap persistence in epistemic store (`brain_gap` table / `brain:gap:` keyspace) | `brain_epistemic` / `core/brain/db` | **net-new** |
| Kernel | `status` lifecycle + link to `kg_snapshot_ref` | epistemic store | **net-new** |
| Kernel | Read API `GET /v1/brain/knowledge-gaps/<property_id>` | `service_api/brain` | **net-new** |
| **Dify (Flow)** | Knowledge Gap cards surface consuming the read API | console / service-API client | net-new surface, no core-edit |

The bulk here is **kernel-side and is mine.** Dify side is a thin read surface — small enough that it can fold into the same Flow child issue as Part A, or stay a console task.

---

## C. Coordination — Flow child issue

The Dify-side wiring for **Part A Path 2** is substantial (decision-time threading through the retrieve request, External-Knowledge binding, metadata convention) → filed as a child issue assigned to Flow. The **Part B** Dify surface is a thin read-API client and is bundled in for cohesion. Kernel work (both parts) stays with me on `cendra/*`.

Child-issue scope (Flow):
1. `valid_from`/`valid_to` date-metadata convention on Property Knowledge datasets (`DatasetMetadata`/`doc_metadata`).
2. Bind Property Knowledge datasets to the kernel External-Knowledge endpoint (`external_knowledge_service.py`).
3. **Thread decision-time `T`** into the External-Knowledge retrieve request from the run (`knowledge_retrieval_node.py`) — the net-new item.
4. Path-1 manual-mode date filter as the table-stakes fallback.
5. Knowledge Gap cards read surface against `GET /v1/brain/knowledge-gaps/<property_id>`.

Interface contract kernel↔Dify: kernel publishes (a) the External-Knowledge retrieve request schema including `as_of`, and (b) the gap read-API schema. Flow consumes both; neither requires Flow to touch the kernel.

---

## D. No-claim-until-shipped (restated per clone-risk row)

| Clone-risk row | Becomes defensible when… | Until then |
|---|---|---|
| **Property Knowledge** | the as-of retrieve path (Part A Path 2) is live end-to-end and map row #5's Property-Knowledge anchoring reads `implemented` | ship Property Knowledge; **no differentiation claim**. Path-1 valid-time filtering may be described only as table-stakes "always-current docs," never as a moat. |
| **Knowledge Gap cards** | gap cards are emitted by #4, persisted in #5 with decision-time provenance, and the registry row reads `implemented` | ship the cards surface with **zero defensibility claim** (per CEN-10). A gap list with no abstention-emission provenance is a commodity and must not be demoed as differentiation. |

Demo-narrative rule (map synthesis) remains in force: the honest observe-mode story only. Neither anchor may be demoed as live until its row flips.

## E. Open questions for Atlas

1. **Decision-time source of truth.** Proposed `as_of` = the run's inbound-event timestamp (when the guest message arrived), not wall-clock at retrieval, so a delayed/queued run reconstructs belief as of the event it is answering. Confirm this is the intended semantics for the ledger.
2. **Gap granularity.** One `GapRecord` per abstention event (proposed) vs. deduplicated per missing-predicate. Per-event preserves the honest history; dedup makes a cleaner operator surface. Recommend per-event storage with dedup at the read API.
3. **Path-1 exposure.** OK to ship Path-1 valid-time filtering in G2 ahead of Path-2, labeled table-stakes, or hold both until Path-2 lands to avoid a half-anchored surface?
