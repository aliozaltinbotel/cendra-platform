# PRD: Smart Escalation — Confidence-Routed Approvals

> **ROADMAP PRD.** The routing mechanism this PRD specifies is **designed, not live**. The
> confidence thresholds that drive it are implemented and demo-safe (#4); the routing of an
> abstention into an approval queue is **not wired** (#13 `partial`). **Never demo Smart
> Escalation as shipping.** Per the demo-narrative rule, the observe-mode story is the only
> story: "Cendra is watching, scoring, and accruing your ledger today; autonomy is earned and
> switched on per workflow."

- **Issue:** [CEN-22](/CEN/issues/CEN-22) (Wave 2 of the [CEN-11](/CEN/issues/CEN-11) G2 slate)
- **Depends on:** PRD *Cendra Assistant & the Earned-Autonomy Ladder*
  (`docs/product/prd/cendra-assistant-earned-autonomy-ladder.md`) — escalation is the routing
  surface for that PRD's abstentions and the execution substrate for its SEMI_AUTO hold window.
- **Canonical input:** `docs/product/moat-fit-map.md` on `main` (board-confirmed, CEN-4).

## 1. Summary

When the Assistant's confidence in a proposed action falls below the calibrated threshold, it
abstains — it refuses to act and produces a rationale (#4, implemented today). Smart Escalation
is the product surface that turns that refusal into a *routed decision*: the action waits
`PENDING` in a one-tap approval queue, the property manager approves or denies it from the
console (or the bound Human-Input step), and unanswered requests expire safely onto a
conservative fallback — never into silent execution.

The differentiation claim is narrow and specific: **the escalation threshold and reasoning are
the moat; the queue and the HITL node are table-stakes.** Any inbox tool can build an approval
queue. What it cannot replicate is *when* Cendra escalates — thresholds calibrated per
workflow from the operator's own outcome history, with the rationale and evidence attached.

## 2. User & job-to-be-done

- **User:** the property manager (PM) operating one or more short-term-rental properties
  through the Cendra console — the same user as the ladder PRD, in the moments where the
  Assistant is *not* sure.
- **Job-to-be-done:** *"When the system isn't confident enough to act, get the decision to me
  fast, with the evidence I need to decide in one glance and one tap — and if I can't respond
  in time, fail safe, not silent."*
- Secondary job (trust accrual): every resolution the PM makes is evidence — it feeds the
  decision ledger (#9) and the calibration window (#4), so today's escalations are the data
  that sharpens tomorrow's thresholds.

## 3. Map-row citations (canonical input: `docs/product/moat-fit-map.md` on `main`)

| Map row | Mechanism / surface | Status (code-verified) | Verdict | What this PRD does with it |
|---|---|---|---|---|
| Part A #4 | Calibrated abstention (`AbstentionGate`, `brain_calibration`) | **implemented** (most-live gate) | MOAT | The trigger. In enforce mode it **refuses the dispatch with a rationale** — it does **not** currently route to a Dify HITL node. This PRD specifies the surface that routing feeds. |
| Part A #13 | Confidence-routed approval gateway (`core/brain/autonomy/approval.py` + `brain_autonomy`) | **partial** — kernel ported (Batch 2); HITL-node wiring + timeout sweep pending | MOAT (supporting #4) | The routing substrate. Its wiring is the prerequisite for this entire surface. Approval routing alone is a commodity queue; the calibrated thresholds that drive it are the moat. |
| Part C | **Smart Escalation** (HITL triggered by calibrated abstention) | Anchored (#4) | PRODUCTIZATION anchored to MOAT | **Ship — when #13 wiring lands.** Ruling verbatim: "The escalation threshold and reasoning are MOAT; the HITL node is TABLE-STAKES. Today abstention refuses with a rationale; routing into the HITL node lands with the #13 approval-gateway wiring." |
| Part B | Human Input (HITL) node | Dify table-stakes | TABLE-STAKES | The completion surface we bind to. "HITL is TABLE-STAKES; the gate chain *triggering* HITL at the right threshold is MOAT." We integrate it; we do not claim it and we do not rebuild it. |

Supporting rows consumed but not claimed: #9 outcome ledger (every escalation and its
resolution is a DecisionCase), #1 gate chain (the dispatch path the abstention verdict comes
from), #2 TrustMeter (escalation volume per workflow is ladder-progress context — display
only; **no "score gates actions" claims** while #2 is `partial`).

**Retired framings honored:** no zero-core-edit claim anywhere — the maintained fork (T1/T3
core edits) is a governed cost of the moat; the HITL binding itself lands at the
`callback_handler` seam (T7 area) plus a T5 beat entry, both registered fork touchpoints.

## 4. Product definition

### 4.1 Today's truth (binding baseline — what exists before any of this ships)

This section is the honest substrate every claim below is measured against:

- **Abstention refuses; it does not route.** `AbstentionGate` (in the live T1/T3 chain) blocks
  a low-confidence dispatch and records a rationale. The refusal is operator-visible as a
  rationale card (ladder PRD §4.1). Nothing waits anywhere; nothing is queued; no HITL node is
  entered. The action simply does not happen.
- **The approval gateway kernel exists but is unwired.** `ApprovalGateway.request_approval`
  is non-blocking: it returns either a decided response (auto-approved / blocker-denied) or a
  `PENDING` one parked in the gateway. `submit_response` resolves; `expire_overdue` sweeps
  timed-out requests onto their fallback. Decision order (kernel, fixed): hard blockers →
  conditional approve → auto-approve set → preference rules → confidence routing → mandatory
  approval. **Nothing calls any of this in the live dispatch path today.**
- **Three-tier confidence routing (kernel, Batch 2):** HIGH (≥ 0.85) → auto-approve; MEDIUM
  (0.50–0.85) → notify the PM with an EvidencePack; LOW (< 0.50) → escalate with urgency+1.
  Tier boundaries are kernel defaults; the action-policy sets (auto-approve / conditional /
  always-require) are pack data (`packs/hospitality/approval.yaml`), never kernel code.

### 4.2 The one-tap approval queue (console surface — Pixel's lane)

A single console queue, per property, of actions waiting on the PM:

- **Queue card anatomy:** what the Assistant wants to do (operator vocabulary, one line) ·
  why it's asking (the abstention rationale + `evidence_summary` from the EvidencePack) ·
  confidence shown as a tier (high / medium / low), **never a raw score gating claim** ·
  urgency (1–5) · a visible countdown to expiry · guest/booking context links.
- **Resolve actions (one tap each):**
  - **Approve** → the held action executes; resolution recorded (actor, timestamp, channel).
  - **Deny** → the action is dropped; the PM can attach a reason (feeds override evidence, #9).
  - **Approve + "always allow this"** → kernel `apply_rule` + `rule_scope`
    (`this_time` / `always` / `this_property` / `all_properties`): the PM's decision becomes a
    preference rule the gateway consults *before* confidence routing on future requests. This
    is the queue's compounding hook — resolutions teach the router.
- **Expire safely (the safety invariant, binding):** on timeout (`URGENCY_TIMEOUTS`: urgency 1
  → 60 min … urgency 5 → 2 min; default fallback `notify_manager`), the request resolves to
  `TIMEOUT` and the fallback runs. **A timeout never executes the held action.** Expiry is
  always conservative: notify, drop, or re-escalate — never act.
- **Resolution from anywhere:** the queue card and the bound Human-Input step are two faces of
  one approval record — resolving in either place resolves both (single source of truth:
  `request_id`).

### 4.3 Relationship to the earned-autonomy ladder

Smart Escalation is not a fourth rung; it is the routing surface that serves the ladder at two
points:

- **In Draft (OBSERVE):** every action already waits for the PM — the queue *is* the Draft
  workflow's confirm surface once wired, replacing ad-hoc confirmation with routed, expiring,
  evidence-carrying requests.
- **In Hold-to-send (SEMI_AUTO):** the hold window (`hold_seconds`, default 60s) is an
  approval request with auto-approve-on-timeout semantics *inverted by promotion*: because the
  workflow has **earned** SEMI_AUTO through the `PromotionGate`, silence means consent within
  the window. This inversion is permitted **only** above the promotion gate — it is the single
  exception to the expire-safely invariant, and it is earned, never configured. (Same gateway,
  same ledger, same audit trail — ladder PRD Platform ask 3.)
- **Critical escalations bypass the queue, not the envelope** (CEN-10 engineering guard,
  binding): an "Urgent — Safety Issue" escalation skips approval routing but still writes a
  DecisionCase to the ledger (#9) and passes the compliance monitor (#10). The bypass is
  auditable; it is never marketed (permitted no-claim surface).

## 5. Maturity: live vs roadmap (binding on demo and copy)

Per the map's Moat Maturity Ruling: no claim of live capability until the mechanism row's
Status reads `implemented`.

**Live today (demo-safe at observe posture):**

- Calibrated abstention (#4) — `implemented`; refuses with a rationale. The *threshold logic*
  of Smart Escalation is real and demonstrable as the abstention rationale card.
- Decision ledger capture (#9) — `implemented` when gates run ≥ observe; abstention verdicts
  appear in the ledger today.
- Approval gateway kernel (#13) — ported (Batch 2), unit-level live; **nothing routes into it
  at runtime.**

**Designed, not live (roadmap with explicit Batch dependencies — never demo as shipping):**

| Capability this PRD specifies | Gap | Batch dependency |
|---|---|---|
| Abstention → approval-request routing | abstention refuses inline; no code path creates an `ApprovalRequest` from an abstain verdict | Batch 5+ (Platform ask 1) |
| HITL-node completion ↔ approval `resolve` | net-new wiring at the `callback_handler` seam | Batch 5+ (Platform ask 2) |
| Timeout-sweep beat job (`expire_overdue`) | no T5 beat entry exists | Batch 5+ (Platform ask 3) |
| Durable approval queue + console read API | requests park in-process in the gateway; no persistence or queue endpoint | Batch 5+ (Platform asks 4–5) |
| SEMI_AUTO hold-window execution | same gateway wiring, inverted timeout above the promotion gate | Batch 5+ (ladder PRD ask 3 — one wiring effort, two surfaces) |

**Consequence:** the G2-demoable slice of Smart Escalation is the **abstention rationale card
plus the roadmap frame** — "today Cendra tells you when it doesn't know; next it brings that
decision to you as a one-tap approval." The queue ships only when the #13 wiring lands and the
map row flips to `implemented`.

## 6. Hospitality copy rules (binding)

1. **Permitted (the row's hospitality expression, verbatim):** "Low-confidence actions wait
   for your one-tap approval and expire safely if you don't respond." — usable **only** once
   #13 reads `implemented`; until then all queue copy is future-framed ("will wait").
2. **Permitted today (#4 is implemented):** "Cendra tells you when it doesn't know — it never
   guesses on a guest-impacting decision without surfacing it to you first."
3. **Forbidden:** marketing the approval queue, inbox, or HITL node as differentiation —
   "approval routing is a commodity queue; the calibrated confidence thresholds that drive it
   are the moat" (#13 ruling). The claim is always about *when* Cendra escalates, never *that*
   it has a queue.
4. **Forbidden:** any "score gates actions" claim while #2 is `partial`; confidence appears as
   tiers and history, never as a live gating number.
5. **Forbidden:** marketing the critical-escalation bypass ("Urgent — Safety Issue") as
   differentiation — permitted no-claim surface, table-stakes operator responsibility.
6. **No autonomy level advertised above its promotion gate:** hold-window (silence-means-
   consent) copy may only describe SEMI_AUTO as an *earned* state, shown locked with unlock
   criteria until a real workflow earns it.
7. **No zero-core-edit claims** (Atlas adjudication; framing retired).
8. Operator vocabulary only (approve / deny / always allow / expires in…); kernel and Dify
   vocabulary (`ApprovalStatus`, `PENDING`, HITL, gateway) never appear in operator surfaces.
   Dify branding is never modified (license; board-owned track).

## 7. Brain / service_api capabilities consumed

| Capability | Surface | Status |
|---|---|---|
| `AbstentionGate` + `brain_calibration` (#4) | the escalation trigger + rationale content | implemented |
| `ApprovalGateway` (`request_approval` / `submit_response` / `expire_overdue`), three-tier confidence router, `EvidencePack` (#13) | queue semantics, card content, expiry | partial — kernel only, unwired |
| Preference rules (`apply_rule`, `rule_scope`) | "always allow this" one-tap rule creation | partial — kernel only |
| Decision ledger `brain_decision` + T7 capture (#9) | every escalation + resolution as DecisionCase evidence | implemented when gates ≥ observe |
| Pack policy sets (`packs/hospitality/approval.yaml`) | auto-approve / conditional / always-require action sets | pack data; injected, never kernel code |
| Dify Human-Input (HITL) node (Part B) | workflow-side completion surface | table-stakes; binding is the net-new work |

## 8. Platform asks (for Atlas — to be converted into interface issues for Forge's org; not created from this issue)

These refine the ladder PRD's ask 3 ("hold-window execution") into its constituent wiring;
they are one engineering effort serving two surfaces (Draft-queue routing and the SEMI_AUTO
hold window). Sequencing recommendation to Atlas: 4 → 1 → 2 → 3 → 5 (persistence first — the
in-process gateway cannot back a console surface).

1. **Abstain → route:** on an abstain verdict in the live gate path (T1/T3), create an
   `ApprovalRequest` via `ApprovalGateway.request_approval` (carrying the abstention rationale
   and EvidencePack summary) instead of terminating at inline refusal. Gated by posture: only
   where the workspace runs ≥ observe and the workflow's autonomy state permits.
2. **HITL binding:** bind Dify Human-Input node completion ↔ approval `resolve`
   (`submit_response`) at the `callback_handler` seam, so a workflow-side response and a
   console-side response resolve the same `request_id`.
3. **Timeout sweep:** Celery beat entry (T5) running `expire_overdue` on a short cadence;
   expired requests resolve to `TIMEOUT` and run their `fallback_action` — **fallbacks must
   never execute the held action** (safety invariant, §4.2).
4. **Durable approval store:** persist approval requests (tenant-scoped `brain_*` table,
   additive migration) so the queue survives process restarts and is queryable; the in-process
   parking in `ApprovalGateway` is test-grade only.
5. **Queue service API:** console read endpoint (pending approvals per property, with
   rationale/evidence/expiry) and resolve write endpoint (authz: operator role), consistent
   with the ladder service API (ladder PRD ask 4).

## 9. Acceptance criteria

**For this PRD (Atlas merge = acceptance):** map-row citation table with Status column present
(§3); live-vs-roadmap maturity section with explicit Batch dependencies present (§5);
hospitality copy rules present (§6); today's-truth baseline recorded (§4.1); platform asks
enumerated, none self-created (§8).

**For the shipped surface (implementation issues created per accepted PRD, by Atlas/Forge —
all gated on #13 flipping to `implemented`):**

1. A low-confidence action on a gated workspace produces a `PENDING` approval request visible
   in the console queue within seconds, carrying rationale, evidence summary, confidence tier,
   urgency, and expiry countdown.
2. One-tap approve executes the held action exactly once; one-tap deny drops it; both write
   the resolution (actor, channel, timestamp) to the decision ledger (#9).
3. "Always allow this" creates a preference rule at the chosen scope, and an identical
   subsequent request is auto-approved by the rules step — visibly attributed to the rule.
4. An unanswered request expires to `TIMEOUT` at its urgency deadline and runs its fallback;
   **no expired request ever executes the held action** (tested invariant).
5. Resolving via the bound Human-Input step and via the console are equivalent — one record,
   no double-execution, no orphaned HITL waits.
6. A critical escalation bypasses the queue but still appears in the ledger and passes the
   compliance monitor (CEN-10 engineering guard — tested).
7. No operator-facing string violates §6 (copy review against the clone-risk table is part of
   UX acceptance).

## 10. Out of scope

- The earned-autonomy ladder itself (rungs, promotion, demotion) — ladder PRD.
- Signed receipts for escalation resolutions (#3/#10) — CEN-14 track.
- Guest-journey workflow templates that *use* Human-Input nodes — Packs' lane, separate PRDs.
- Any change to abstention thresholds or calibration math (#4 is consumed as-is).
- Notification channels (push/SMS/email digests) beyond the console queue — follow-up PRD once
  the queue exists.
