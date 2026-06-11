# PRD — Outcome Dashboard & Confidence Score (Trust Surfaces)

> **Issue:** CEN-19 · **Parent:** CEN-11 G2 umbrella (Wave 1)
> **Owner:** Compass (Product Lead) · **Status:** v1, 2026-06-11
> **Binding inputs:** [`docs/product/moat-fit-map.md`](../moat-fit-map.md) on `main` (board-confirmed 2026-06-11, CEN-4) — verdicts, Moat Maturity Ruling, demo-narrative rule · [`docs/product/hospitality-productization-map.md`](../hospitality-productization-map.md) (surface labels, copy) · CEN-12 observe-mode PRD (metric definitions — same wave)
> **Builders:** Pixel (operator console) + Vista (product surfaces). Consumes `service_api/brain` reads only; **no backend additions outside Forge's lane** — every gap is a Platform ask (§8), converted to interface issues by Atlas.

---

## 1. Map rows this PRD builds on (citation, per the G2 constraint)

| Map row | Mechanism / surface | Status (code-verified, map on `main`) | What this PRD uses it for |
|---|---|---|---|
| **Part A #9** | Compounding outcome ledger — `brain_decision` DecisionCases, captured by T7 from the T1 stream wrapper (idempotent, append-only) | **implemented** (when gates active); cases stored as `scenario="general"` (unclassified — not yet learnable) until Batch 5/6 classification | The data source for the entire Outcome Dashboard; the moat the dashboard is the face of |
| **Part A #2** | TrustMeter — `core/brain/autonomy/trust_meter.py` + `brain_autonomy`; read via `GET /v1/brain/trust-meter/<property_id>` | **partial** — substrate + read API present; **not consulted by the live gate chain** | The Confidence Score surface, **display-only**. In-chain gating is CEN-13's scope (blocked on the CEN-26 kernel feasibility review), not this PRD's |
| **Part C "Outcome Dashboard"** | Operator ROI view ← LLMOps monitoring equivalent | **PRODUCTIZATION anchored to MOAT (#9)** — ruling: *Ship. The ledger is the moat; the dashboard is the face of it.* | The surface this PRD specifies (§4) |
| **Part C "Confidence Score"** | TrustMeter display in operator UI (no Dify equivalent) | **PRODUCTIZATION anchored to MOAT (#2)** — ruling: *Ship as display-only now (read API is live); TrustMeter does not yet gate anything in-chain. The history claim holds only once tenants run gates ≥ observe.* | The surface this PRD specifies (§5) |
| Part A #1 (supporting) | Gate chain (`BRAIN_GATES_MODE` off / observe / enforce) | implemented — default OFF; one workspace in `observe` | Precondition: the ledger only accrues at posture ≥ observe (CEN-12/CEN-17 activation program) |
| Part A #4 (supporting) | Calibrated abstention | implemented (most-live gate) | The abstain/act verdict mix shown on Decision Cards |

**Row #9 hospitality expression (verbatim, the dashboard's product thesis):** *"Your history with Cendra compounds — an operator on year three has a system that knows their property at a depth no competitor can replicate without those three years of data."*

**Row #2 hospitality expression (verbatim, the score's product thesis):** *"Your Assistant's confidence grows with every successful booking or resolved issue — and resets if it makes a mistake, so you stay in control."*

**Retired framings honored:** no zero-core-edit claim anywhere (the maintained fork — T1/T3 core edits — is a governed cost of the moat); no claim of live capability for any `partial`/`planned` row; license track is board-owned and Dify branding is never modified by us.

> Status note: PR #8 (Batch 6) merged to `cendra/main` on 2026-06-11, after the map's Porter verification pass. The map on `main` remains the canonical status source for this PRD; no row status is upgraded here. If Porter's re-verification upgrades #2 or #9, the maturity section (§6) inherits the new status without changing this PRD's display-only scope.

## 2. Users and jobs-to-be-done

**Primary user: the design-partner PM (property manager).**
JTBD: *"When I review what Cendra has been doing for my property, I need to see every action, its outcome, and what it cost or earned me — so I can decide whether it deserves more autonomy."*
The Outcome Dashboard is the ROI ledger view; the Confidence Score is the per-workflow trust readout that tells the PM *where* Cendra has earned the next rung.

**Secondary user: Cendra activation operator (us, G3 program).**
JTBD: *"When I assemble a design partner's evidence pack (cases, calibration, overrides, promotions), I need the same numbers the PM sees, exportable and consistent with the CEN-12 accrual KPIs."*
One metric vocabulary serves both: the dashboard is the PM-facing face of the G3 evidence pack.

## 3. Scope

**In scope:**
- **Outcome Dashboard** ("Assistant Performance" in operator copy): per-property view over the `brain_decision` DecisionCase ledger — actions, outcomes, overrides, and revenue-impact framing per row #9 (§4).
- **Confidence Score** ("Confidence Level" in operator copy): display-only TrustMeter surface fed by `GET /v1/brain/trust-meter/<property_id>` (§5).
- Honest empty/cold-start states for tenants at `off` posture (§4.4).

**Out of scope — hard exclusions:**
- **No TrustMeter gating.** The score displays; it gates nothing. In-chain TrustMeter is CEN-13 (blocked on CEN-26). This PRD must not create UI that implies the score blocks or permits actions.
- **No signed receipts.** Certificate minting (#3) and Art. 12 emission (#10) are unwired (CEN-14's scope). Any receipt affordance on a Decision Card is labeled "pending."
- **No autonomy claims beyond the promotion gate.** The ladder display (§5) shows the *current* rung and progress; it never advertises a rung that hasn't cleared its `PromotionGate`.
- **No self-improvement claims** (#7 meso/macro not live on the canonical map).

## 4. Outcome Dashboard ("Assistant Performance")

### 4.1 Core view — the ledger, per property

A per-property, reverse-chronological view over DecisionCases with a summary header. Unit of display: the **Decision Card** (productization-map label for a DecisionCase).

**Summary header (per property, per period — week/month/all-time):**

| Tile | Definition (aligned with CEN-12 §8 KPIs) | Source |
|---|---|---|
| **Interactions watched** | Count of DecisionCases in period (= CEN-12 "ledger depth," weekly new cases) | Ledger |
| **Scored decisions** | Same count, framed as scoring: every case carries a gate verdict | Ledger + shadow verdicts (CEN-25 ask) |
| **Decision mix** | Act-worthy vs. abstain distribution (= CEN-12 "scored-decision mix") | Shadow verdicts (CEN-25 ask) |
| **Your overrides** | Cases with `human_overrode = true` (= CEN-12 "override capture") | Outcome fields (platform ask §8) |
| **Outcomes verified** | Cases with a resolved outcome (`successful` not null) vs. pending — outcomes fill asynchronously | Outcome fields (platform ask §8) |
| **Revenue impact** | Sum of `revenue_impact` over **verified** outcomes only, in property currency | Outcome fields (platform ask §8) |

**Decision Card (row) fields:** journey stage, scenario, what Cendra saw (message context), what Cendra did or would have done (decision + at observe: "would have"), confidence, abstain/act verdict, outcome status (pending / successful / unsuccessful / overridden), revenue impact (when verified), timestamp (`decision_at`, not capture time).

### 4.2 Revenue-impact framing (row #9, gated copy)

The kernel's `Outcome.revenue_impact` is *estimated revenue impact in property currency* and is `None` until outcome verification fills it. Binding copy rule (inherited from CEN-12 §6): **revenue-impact and ROI claims appear only over verified outcomes.** Until a tenant has verified outcomes, the dashboard leads with volume + scoring ("Cendra watched N guest interactions and scored every one. Your ledger: M decision records and counting.") and shows the revenue tile in a "building your baseline" state. Never an extrapolated or projected ROI number.

### 4.3 Compounding framing (the moat sentence)

The dashboard's persistent footer/empty-period framing is the #9 expression: the ledger is the operator's own history, it compounds, and it's theirs. Tie-in to onboarding framing #4 ("Your history stays yours") per the productization map.

### 4.4 Cold start and posture honesty

- Tenant at `off` posture: no accrual. Empty state says so plainly — "Cendra isn't watching yet. Activate observe mode to start your ledger." (links the CEN-12/CEN-17 activation path; never fakes sample data).
- Tenant at `observe`: cards are framed as **"what Cendra saw / would have done," never "what Cendra did."**
- `scenario` shows "General" (unclassified) until Batch 5/6 classification lands — the UI must tolerate a single-bucket scenario distribution without pretending taxonomy exists (CEN-12 "classification readiness" KPI: reported, no target).

## 5. Confidence Score ("Confidence Level") — display-only TrustMeter

### 5.1 Data contract (live today)

`GET /v1/brain/trust-meter/<property_id>` returns per-workflow **bands**: `workflow`, `state` (kernel `AutonomyState`), `sample_size`, `success_rate`, `override_rate`, `incidents`, and `progress {target_state, satisfied, total}` (promotion-gate criteria met out of total). This shape is sufficient for the display surface; no new read API is required for v1.

### 5.2 Display rules

- **Per-workflow, not a single global number.** The autonomy ladder is per workflow (kernel `AutonomyState`: OBSERVE → SEMI_AUTO → AUTOPILOT). Show each workflow's band: product-label rung (OBSERVE renders as **"Draft — Ready for you to send"** per the CEN-10 mapping), success rate, override rate, sample size, incidents.
- **Promotion progress, not promises:** "3 of 5 reliability checks met toward semi-auto" from `progress.satisfied/total`. The next rung is shown as *progress toward*, never as available or imminent. **Never advertise an autonomy level that hasn't cleared its promotion gate** (binding, productization map §3).
- **Sample-size honesty:** below a minimum `sample_size`, render "still learning your property — N decisions observed" instead of a percentage. A confident-looking 100% on 3 samples is a trust bug.
- **Display-only, enforced in UX:** no toggle, no threshold slider, no "raise autonomy" action on this surface. Promotion actions belong to the future CEN-13 surface once TrustMeter is in-chain.
- **History claim is per-tenant conditional:** the row #2 "grows with history" copy is permitted only for tenants running gates ≥ observe (their meter actually moves). For `off`-posture tenants the surface shows the cold-start state, no growth claim.

## 6. Maturity — live vs. roadmap (binding on all copy and demos)

Per the Moat Maturity Ruling, what this product ships against today vs. what is roadmap with explicit Batch dependencies:

| Capability | Today (map on `main`) | Roadmap dependency | What the surface may claim meanwhile |
|---|---|---|---|
| Ledger capture (#9) | **implemented** when gates active; accrues only at posture ≥ observe | CEN-12/CEN-17 activation program turns it on per design partner | "Watching, scoring, accruing" — only for activated tenants |
| Case classification | All cases `scenario="general"` | Batch 5/6 classification | Show "General"; no taxonomy claims |
| Outcome verification & revenue impact | `Outcome` fields exist in kernel; fill is asynchronous; not exposed via `GET /v1/brain/cases` today | Platform asks §8 (API exposure); outcome-verification wiring | Volume + scoring copy; revenue tile in baseline state |
| TrustMeter display (#2) | Read API **live** (`partial` = not in-chain) | — (display is shippable now) | Per-workflow confidence + promotion progress, display-only |
| TrustMeter in-chain (#2 → gating) | Not in the live gate chain | CEN-13 PRD (blocked on CEN-26 feasibility) | Forbidden: any claim the score gates actions |
| Signed receipts on cards (#3/#10) | Issuance unwired; Art. 12 unemitted | CEN-14 | "Receipt pending" label only |
| Demo narrative | — | — | Observe-mode story only: *"watching, scoring, accruing your ledger today; autonomy is earned per workflow"* |

## 7. Hospitality copy rules

1. Operator-facing labels come from the productization map: **Assistant Performance** (dashboard), **Confidence Level** (score), **Decision Card** (case), journey-stage vocabulary for `stage`. Never "DecisionCase," "TrustMeter," "gate chain," or "HITL" in operator UI.
2. Verbatim theses: row #9 expression on the dashboard, row #2 expression on the score surface (§1) — these are the only two differentiation sentences; everything else is descriptive.
3. Observe posture: "watched & scored," "what Cendra would have done." Action language ("Cendra did X") only for executed actions (`executed_actions` non-empty) on workflows whose autonomy rung permits execution.
4. Override language is respectful of the operator: "You adjusted this" — overrides are the operator's control working, and they feed the meter ("your edits teach the score what to trust"). Never frame an override as an error.
5. Forbidden phrases (maturity ruling + CEN-10): "tamper-proof receipt" (until #3 mints), "Cendra learned from your edit" (until #7 loops live), "AI drafts your replies" standalone (ladder-rung framing only), any zero-core-edit claim, any ROI projection.
6. Numbers degrade honestly: small samples → qualitative copy; missing outcomes → "pending"; `off` posture → activation prompt, never sample data.

## 8. Platform asks (for Atlas → Forge; we do not create these issues ourselves)

Already filed (CEN-25, from CEN-12 — this PRD consumes the same asks; flagging shared dependency, not duplicating):
- **A1. Accrual-metrics aggregation endpoint** — per-tenant aggregates (cases by day/workflow/verdict, capture-integrity inputs). The dashboard summary header (§4.1) is the second consumer; raw `GET /v1/brain/cases` lists don't scale to period rollups.
- **A2. Observe-mode shadow verdicts in the DecisionCase** — act/abstain/refuse + confidence recorded per case, so the decision-mix tile and per-card verdict render from the ledger alone.

New asks from this PRD:
- **B1. Enrich the case read model.** `GET /v1/brain/cases` today returns only `case_id, stage, scenario, property_id, decision, successful, conversation_id, created_at`. The Decision Card and summary tiles additionally need: `human_overrode`, `resolution_type`, `revenue_impact`, `approval_required/approved`, `decision_at` (decision time, not capture time), `response_text`/message context (PII-safe form per the T4 redaction module), and confidence/verdict (with A2). Kernel `Outcome` already carries these fields; this is exposure, not new capture.
- **B2. Case filtering & pagination for the dashboard.** Filters on `stage`, `scenario`, verdict, outcome status, and a `decision_at` date range; stable cursor pagination. Current API supports only `property_id` + `limit` (≤200) + `offset`.
- **B3. Console-auth access to brain reads.** `service_api/brain` endpoints authenticate via app token (`validate_app_token`). The operator console (Pixel's lane) needs a tenant-scoped console-session path to trust-meter and cases reads — embedding service-API app tokens in the console is not acceptable.
- **B4. Revenue-impact semantics confirmation.** Confirm `Outcome.revenue_impact` currency handling (per-property currency, no conversion?) and when/what fills it at observe posture, before the revenue tile ships beyond its baseline state.
- **B5. Trust-meter workflow labels.** `bands[].workflow` returns workflow-kind identifiers; the console needs a supported mapping to operator-facing workflow names (productization-map journey vocabulary) rather than string-munging kind IDs.

Dependency note: B1/B2/A1/A2 gate the full §4.1 header; a v0 dashboard (interactions watched + decision list from today's thin payload, plus the §5 score surface, which needs only B3/B5) can ship first. Recommended sequencing: B3 → B5 → B1 → A2/A1 → B2 → B4.

## 9. Acceptance criteria (this PRD)

- [x] Map-row citation table with Status column, including #9 `implemented` (when gates active) and #2 `partial` — display-only, not in-chain (§1).
- [x] Live-vs-roadmap maturity section with explicit Batch dependencies (Batch 5/6 classification; CEN-13/CEN-26, CEN-14, CEN-12/CEN-17 cross-wave dependencies) (§6).
- [x] Metric definitions aligned with the observe-mode PRD (CEN-12 §8 KPI vocabulary reused for the summary header) (§4.1).
- [x] Hospitality copy rules, including the two verbatim differentiation theses and the forbidden-phrase list (§7).
- [x] "Platform asks" section listing every platform/Dify capability needed, marked shared (CEN-25) vs. new, for Atlas to convert into interface issues (§8).
- [x] No claim of live capability beyond `implemented` rows; no zero-core-edit claims; no Dify branding changes; observe-mode demo narrative only.
