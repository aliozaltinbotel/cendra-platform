# PRD: Cendra Assistant & the Earned-Autonomy Ladder

> **Issue:** CEN-18 (Wave 1 of the CEN-11 G2 slate)
> **Owner:** Compass (Product Lead) · **Reviewer:** Atlas (accepts by merging)
> **Status:** Draft for review · **Date:** 2026-06-11
> **Canonical inputs:** [Moat Fit Map](../moat-fit-map.md) (board-confirmed 2026-06-11, CEN-4) · CEN-10 clone-risk adjudication (recorded in the map synthesis) · [Hospitality Productization Map](../hospitality-productization-map.md) · [Design-Partner Demo Narrative](../design-partner-demo-narrative.md)
> **Kernel facts** verified against `origin/cendra/main` on 2026-06-11 (`api/core/brain/autonomy/`, `api/core/workflow/nodes/agent_v2/cendra_brain_layer.py`).

---

## 1. Summary

Cendra Assistant is the operator-facing agent: a Dify `agent_v2` agent whose every run passes through the Brain gate chain (compliance → certificate → abstention → risk) with calibrated abstention inside the loop. It is defensible **because the gates are inside it** — the same agent without the gate chain is a commodity Dify agent (Crux Test, map Part C).

The earned-autonomy ladder is the operator's control surface over what the Assistant may do: every repeatable workflow ("send access code", "reply check-in ETA", "charge security deposit") has its own per-workflow `AutonomyState` — **OBSERVE → SEMI_AUTO → AUTOPILOT** — promoted only when the kernel `PromotionGate`'s five reliability metrics all pass, and demoted on any single breach.

**Draft mode is not a feature; it is the OBSERVE rung.** Per the CEN-10 ruling, the standalone claim "AI drafts your replies" is a forbidden commodity claim. This PRD records the binding product-Draft ↔ kernel-OBSERVE mapping (§5) and the copy rules that follow (§7).

## 2. User & job-to-be-done

- **User:** the STR property manager / operator ("PM") running guest communication and operations for one or more properties. Not a developer; never sees Dify vocabulary.
- **Job-to-be-done:** *"Hand routine guest-facing work to an assistant I can actually trust — let me see everything it would do before it does anything, give it independence one workflow at a time only when it has proven itself, and take that independence back instantly when it slips."*
- **Anti-job:** another AI inbox that drafts replies. The market is full of those; the operator's unmet need is **governed delegation with receipts**, not text generation.

## 3. Map-row citations (canonical input: `docs/product/moat-fit-map.md` on `main`)

| Map row | Mechanism / surface | Status (code-verified) | Verdict | What this PRD does with it |
|---|---|---|---|---|
| Part A #1 | Gate chain (T1 dispatch wrapper + T3 `agent_v2` `gate_agent_run`) | **implemented** (Batch 4; default `BRAIN_GATES_MODE=off`; certify step not minted) | MOAT | The Assistant's substrate. Every Assistant run and tool dispatch is gated. |
| Part A #4 | Calibrated abstention (`AbstentionGate`, `brain_calibration`) | **implemented** (most-live gate) | MOAT | Inside the Assistant loop: refuses low-confidence actions with a rationale instead of guessing. |
| Part A #2 | TrustMeter (`trust_meter.py`, `brain_autonomy`, `GET /v1/brain/trust-meter/<property_id>`) | **partial** — read API live; **NOT consulted by the live gate chain** | MOAT | Display-only ladder/score projection in the console. **No "score gates actions" claims** anywhere in product or copy. |
| Part C | **Cendra Assistant** (operator-facing agent on `agent_v2` + Brain gate strategy) | Anchored (#1, #4) | PRODUCTIZATION anchored to MOAT | **Ship.** This PRD. |
| Part C | **Draft mode** ("Ready for you to send") | Anchored **only as ladder rung** (#1/#2 via kernel `AutonomyState`) | Anchored as rung; standalone framing is clone-risk | Presented exclusively as the OBSERVE rung (§5). Standalone framing forbidden (§7). |

Supporting rows consumed but not claimed: #9 outcome ledger (`implemented` when gates active — feeds the ladder's metrics), #13 confidence-routed approval gateway (`partial` — prerequisite for the SEMI_AUTO hold-window UX, see §6 and §9).

## 4. Product definition

### 4.1 Cendra Assistant

- **Substrate:** Dify `agent_v2` agent node with the Brain gate strategy; the agent loop is gated via `cendra_brain_layer.gate_agent_run` (fork touchpoint T3) and each tool dispatch via the T1 wrapper. The kernel chain is `core/brain/gates.py::DecisionPipelineAdapter` composed in `core/brain/runtime_gateway.py`: **Compliance → Certificate → Abstention → Risk**.
- **Behavioral contract (operator-visible):**
  1. The Assistant never acts above the workflow's current autonomy rung.
  2. When uncertainty exceeds the calibrated threshold, it abstains with a rationale — it surfaces, it does not guess (#4).
  3. Every gated run is appended to the operator's decision ledger (#9) via `record_agent_run` / T7 capture, accruing the evidence that later earns promotion.
- **Honest reuse:** the agent loop, model runtime, RAG retrieval, and chat delivery are Dify table-stakes (map Part B). We integrate them; we do not claim them. The maintained fork's T1/T3 core edits are a governed cost of the moat — **this PRD makes no zero-core-edit claim.**

### 4.2 The earned-autonomy ladder

Kernel: `AutonomyState` + `WorkflowMetrics` + `WorkflowAutonomy` (`api/core/brain/autonomy/models.py`), `PromotionGate` (`gate.py`), `AutonomyEngine` (`engine.py`) — all on `cendra/main` (Batch 2).

Per-workflow, per-property state machine (there is no property-wide autonomy slider):

| Rung | Kernel state | Meaning for the operator |
|---|---|---|
| **Draft** | `OBSERVE` | Brain drafts; the PM always confirms. Entry state for every workflow; no promotion threshold to enter it. |
| **Hold-to-send** | `SEMI_AUTO` | Brain executes after a hold window (default 60s, `hold_seconds`) during which the PM can cancel. |
| **Autopilot** | `AUTOPILOT` | Brain executes immediately; the PM receives a digest. |

**Promotion is earned, never assumed.** `PromotionGate.required_metrics` — the five reliability metrics, all of which must pass (conservative promotion); any single breach demotes one rung (aggressive demotion):

| Metric | → SEMI_AUTO (kernel default) | → AUTOPILOT (kernel default) |
|---|---|---|
| `sample_size` (observed executions) | ≥ 20 | ≥ 50 |
| `success_rate` (Wilson-adjusted) | ≥ 0.80 | ≥ 0.92 |
| `override_rate` (PM cancels/corrections) | ≤ 0.15 | ≤ 0.05 |
| `incidents` (post-action complaints) | ≤ 1 | 0 |
| `mean_latency_seconds` | ≤ 60 | ≤ 45 |

Thresholds are kernel defaults (`PromotionThresholds`); operator-facing copy says "proven reliability," never raw numbers, and the console shows per-criterion progress (`CriteriaProgress` via `TrustMeterService`).

Every transition writes an audit trail (`changed_at` / `changed_by` / `reason` on `WorkflowAutonomy`) so the console can always answer **"why is this on autopilot?"**

### 4.3 Ladder UX requirements (operator console — Pixel's lane)

1. **Ladder view per workflow:** current rung, per-criterion promotion progress, and the transition audit trail. Read from the TrustMeter projection (`TrustMeterView` / `GET /v1/brain/trust-meter/<property_id>`) — display-only today (map #2).
2. **Promotion is operator-confirmed:** when the `PromotionGate` says a workflow qualifies, the console proposes promotion; the PM accepts. Auto-promotion without a confirmation tap is out of scope for G2.
3. **One-tap demotion ("take the keys back"):** the PM can drop any workflow to a lower rung instantly, no friction, from anywhere the rung is shown. Demotion never asks "are you sure."
4. **SEMI_AUTO hold-window inbox:** pending actions with a visible countdown and a cancel control. Each cancel is recorded as an override (feeds `override_rate`).
5. **Abstention surfacing:** when the Assistant abstains, the console shows the structured rationale ("here is what I didn't know") — never a silent failure.
6. **Vocabulary:** operator-facing names are Draft / Hold-to-send / Autopilot (hospitality register); kernel names appear nowhere in UI.

## 5. The Draft ↔ OBSERVE mapping (recorded per CEN-10)

This section is the formal record the CEN-10 ruling requires:

- **Product "Draft mode" ("Ready for you to send") maps to kernel `AutonomyState.OBSERVE`** (`core/brain/autonomy/models.py`). It is an autonomy *state*, not a gate-chain output — gate verdicts (permit/refuse/abstain) are a different axis. No kernel vocabulary change is needed; the suspected gap in the productization map §1 is resolved by this mapping.
- Draft is therefore **the entry rung of the earned-autonomy ladder**, not a standalone feature. A workflow in Draft is a workflow whose autonomy has not yet been earned — and whose every confirmed draft is accruing the sample that may earn it.
- Mapping table (binding for all product surfaces and copy): product **Draft** = kernel `OBSERVE` · product **Hold-to-send** = kernel `SEMI_AUTO` · product **Autopilot** = kernel `AUTOPILOT`.

## 6. Maturity: live vs roadmap (binding on demo and copy)

Per the map's Moat Maturity Ruling: no claim of live capability until the mechanism row's Status reads `implemented`.

**Live today (demo-safe at observe posture):**

- Gate chain skeleton (#1) — `implemented`, default OFF; current posture one workspace in `observe`, no tenant enforcing.
- Calibrated abstention (#4) — `implemented`; Wilson success-rate path active at generic dispatch; conformal path sharpens once agent-loop confidence flows.
- Ledger capture (#9) — `implemented` when gates run ≥ observe.
- Ladder kernel (`AutonomyState`, `PromotionGate`, `AutonomyEngine`, `TrustMeterService`) — ported (Batch 2) and unit-level live; **what feeds it at runtime is not wired** (see Platform asks).
- TrustMeter read API (#2) — live, display-only.

**Designed, not live (roadmap with explicit Batch dependencies — never demo as shipping):**

| Capability this PRD depends on | Gap | Batch dependency |
|---|---|---|
| TrustMeter consulted in the live gate chain (#2) | `gates.py` has no TrustMeter gate | Batch 5+ chain wiring |
| Runtime enforcement of `AutonomyState` at dispatch | `AutonomyEngine` exists; dispatcher does not yet consult it in the live T1/T3 path | Batch 5+ (Platform ask 1) |
| `MetricsCollector` fed from the live ledger | metrics-folding exists; no live interaction stream wired | Batch 5/6 (Platform ask 2) |
| SEMI_AUTO hold-window execution (#13) | approval gateway ported; HITL-node binding + timeout sweep pending | Batch 5+ (Platform ask 3) |
| Signed receipts per Assistant action (#3/#10) | certs not minted; Art. 12 records not emitted | Batch 5+ seam (CEN-14 track, not this PRD) |

**Consequence:** the G2-shippable slice is **Draft (OBSERVE) end-to-end plus display-only ladder telemetry**. SEMI_AUTO and AUTOPILOT ship as visible-but-locked rungs ("not yet earned") until the platform asks land. This is the honest product: the locked rungs *are* the demo story.

## 7. Hospitality copy rules (binding)

1. **Forbidden:** "AI drafts your replies" or any standalone Draft framing (CEN-10 clone-risk table). Every Draft mention ties to the ladder.
2. **Permitted Draft framing:** "Draft is where Cendra starts — watching, drafting, learning your property. Every draft you approve is evidence toward earned independence, one workflow at a time."
3. **No "score gates actions" claims** while #2 is `partial`: the confidence score is shown as history and progress, never described as controlling execution.
4. **No autonomy level is advertised above its promotion gate.** A rung that hasn't been earned by a real workflow is shown locked, with the criteria to unlock — never marketed as available-now capability.
5. **No zero-core-edit claims** (Atlas adjudication; framing retired).
6. **Demo-narrative rule (verbatim posture):** "Cendra is watching, scoring, and accruing your ledger today; autonomy is earned and switched on per workflow." No live signed receipts, no live TrustMeter gating, no live self-improvement claims.
7. Operator vocabulary only (Draft / Hold-to-send / Autopilot); kernel and Dify vocabulary never appear in operator surfaces. Dify branding is never modified (license; board-owned track).

## 8. Brain / service_api capabilities consumed

| Capability | Surface | Status |
|---|---|---|
| Gate chain (`DecisionPipelineAdapter` via T1 + T3 `gate_agent_run`) | every Assistant run | implemented (off-by-default) |
| `AbstentionGate` + `brain_calibration` | in-loop abstention, rationale surfacing | implemented |
| `AutonomyEngine` + `PromotionGate` + `AutonomyState` (Batch 2) | ladder state machine | ported; runtime wiring pending |
| `TrustMeterService` / `GET /v1/brain/trust-meter/<property_id>` | ladder + score display | live, read-only |
| Decision ledger `brain_decision` + `record_agent_run` (T7) | evidence accrual for the five metrics | implemented when gates ≥ observe |
| Approval gateway `autonomy/approval.py` (#13) | SEMI_AUTO hold window (roadmap) | partial |

## 9. Platform asks (for Atlas — to be converted into interface issues for Forge's org; not created from this issue)

1. **Autonomy enforcement at dispatch:** consult `AutonomyEngine.state_for(property_id, workflow)` in the live gate path (T1/T3) so `OBSERVE` forces draft-only and `SEMI_AUTO`/`AUTOPILOT` semantics are enforced by the platform, not by UI convention. Includes the workflow-kind resolution (`workflow_kinds.py` registry) at dispatch time.
2. **Metrics pipeline:** wire `MetricsCollector` to fold live outcomes from the decision ledger (#9) — sends, operator confirms/cancels (overrides), incidents, latency — into `WorkflowMetrics`, and run `AutonomyEngine.update_metrics` on a beat so the `PromotionGate` evaluates real evidence.
3. **Hold-window execution (#13):** bind the approval gateway to the Dify Human-Input node (resolve/expire + timeout-sweep beat job) so SEMI_AUTO's cancel window is real execution semantics.
4. **Ladder service API:** per-workflow read endpoint (rung + `CriteriaProgress` + transition audit trail) and a write endpoint for operator-confirmed promotion and one-tap demotion (authz: operator role; demotion never gated).
5. **Console events:** transition events (`autonomy.transition` log line today) exposed as a consumable stream/webhook for the console's audit view and digest notifications.

Ask 4 and 5 unblock the G2-shippable slice; asks 1–3 unblock SEMI_AUTO. Sequencing recommendation to Atlas: 4 → 5 → 2 → 1 → 3.

## 10. Acceptance criteria

**For this PRD (Atlas merge = acceptance):** map-row citation table with Status column present (§3); maturity section with Batch dependencies present (§6); copy rules present (§7); Draft↔OBSERVE mapping recorded (§5); platform asks enumerated, none self-created (§9).

**For the shipped G2 slice (implementation issues created per accepted PRD, by Atlas/Forge):**

1. An operator can run Cendra Assistant on a gated workspace (≥ observe) and every run is gated (compliance → cert-slot → abstention → risk) and ledger-captured.
2. Every guest-facing workflow starts in Draft; nothing executes without operator confirmation while in Draft.
3. An abstention produces an operator-visible rationale card, not a silent failure or a guess.
4. The console shows, per workflow: current rung, five-criterion promotion progress, and the transition audit trail; locked rungs display their unlock criteria.
5. One-tap demotion takes effect immediately and is recorded with actor + reason.
6. Promotion to SEMI_AUTO is impossible — in UI and API — unless the `PromotionGate` evaluation passes all five metrics **and** the operator confirms.
7. No operator-facing string violates §7 (copy review against the clone-risk table is part of UX acceptance).

## 11. Out of scope

- TrustMeter as an in-chain gate (#2 roadmap), certificate minting / Art. 12 emission (CEN-14 track), Z3 policy in-chain (#6), pattern-mining promotion (#8), Knowledge Gap cards (separate G2 PRD), any marketing of "Urgent — Safety Issue" escalation (permitted no-claim surface).
- Auto-promotion without operator confirmation.
- Any modification of Dify branding (license; board-owned).
