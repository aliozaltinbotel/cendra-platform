# PRD: Observe-Mode Activation Program — Ledger Accrual for Design Partners

> **Owner:** Compass (Product Lead)
> **Issue:** [CEN-17](/CEN/issues/CEN-17) — Wave 1 of the [CEN-11](/CEN/issues/CEN-11) G2 slate
> **Status:** Draft for Atlas review (accepted by merge)
> **Date:** 2026-06-11
> **Canonical inputs:** [Moat Fit Map](../moat-fit-map.md) (board-confirmed north star, CEN-4) · [Hospitality Productization Map](../hospitality-productization-map.md) (terminology FINAL)

---

## 1. Problem & Why Now

The Moat Maturity Ruling (Moat Fit Map, Atlas Synthesis) is explicit:

> **"Ledger accrual is the schedule-critical moat."** The compounding ledger (#9) only compounds while gates run ≥ `observe` for real tenants. Every month of OFF posture is a month the moat does not widen — turning observe-mode on for design partners is therefore a G2 priority, not a nice-to-have.

Today the gate chain defaults to `BRAIN_GATES_MODE=off` and exactly **one workspace runs in `observe`**. No design partner is accruing a ledger. The entire compounding thesis — "an operator on year three has a system that knows their property at a depth no competitor can replicate" — has a clock, and the clock only runs while gates observe real tenant traffic.

This PRD defines the **product program** for moving design-partner tenants from `off` to `observe`: who gets switched on, how they consent, what they see, what accrues, how we measure it, and how we roll back. It does **not** define enforcement, autonomy promotion, or any operator-facing control of autonomy — those are later rungs of the ladder and separate PRDs.

## 2. User & Job-to-be-Done

**Primary user:** the **design-partner PM** (property manager — the corpus persona for "operator"). They run real guest traffic through Cendra automations today with gates OFF.

**Job-to-be-done:** *"Let Cendra watch my automations and build up a track record of what it would have done — without changing anything about how my property runs — so that when I later switch on autonomy, it has earned the right with my data, and I can see the receipts."*

**Secondary user:** the **Cendra program team** (us). JTBD: accumulate per-tenant decision history (DecisionCases + calibration samples) so that abstention calibration sharpens, promotion gates have evidence to score against, and the G3 evidence pack ("earned autonomy with receipts") has real content.

**Explicit non-user:** the guest. Observe mode is invisible to guests by design — dispatch behavior is unchanged.

## 3. Map-Row Citations (canonical input: `docs/product/moat-fit-map.md` on `main`)

| Map row | Mechanism | Status (code-verified, 2026-06-11) | Role in this PRD |
|---|---|---|---|
| **#1** | Gate chain (T1 tool-dispatch wrapper + T3 agent-loop gate; `BRAIN_GATES_MODE` = off / observe / enforce; optional per-tenant allowlist) | **implemented** (Batch 4; default OFF; one workspace in observe today; "certify" step not minted) | The switch this program flips. Observe = the chain evaluates every gated dispatch and records, refusing nothing. |
| **#4** | Calibrated abstention (`AbstentionGate` in the runtime_gateway chain; persistent calibration in `brain_calibration`, T5) | **implemented** (most-live gate; at generic dispatch confidence=1.0 → Wilson success-rate path active; conformal path sharpens once agent-loop confidence flows) | The mechanism whose calibration window observe-mode traffic fills. |
| **#9** | Compounding outcome ledger (`brain_decision` table, tenant-scoped; captured by T7 `cendra_decision_capture` from the T1 stream wrapper, idempotent) | **implemented** (when gates active); cases persist as `scenario="general"` (unclassified — not yet learnable) until Batch 5/6 classification | What accrues. One DecisionCase per gated dispatch. |
| **Synthesis** | Moat Maturity Ruling — §"Ledger accrual is the schedule-critical moat" + demo-narrative rule | binding | The schedule rationale and the copy boundary for everything below. |

Integration reality (binding on this PRD's claims): the gate chain attaches via **fork touchpoints T1/T3 that edit Dify core** — this is a maintained fork, a governed cost of the moat. **No claim of zero-core-edit integration appears in this PRD or in any partner-facing material derived from it.**

## 4. Product Definition

### 4.1 Tenant selection

Design partners are enrolled deliberately, not by default flip. Selection criteria:

1. **Real traffic:** the tenant runs at least one live automation with guest-facing volume (any journey stage). A tenant with zero dispatches accrues zero ledger — enrolling them is posture theater.
2. **Workspace = tenant:** one design partner per workspace, consistent with the existing license posture (multi-tenancy is a board-owned LICENSE §1a track; this program does not change deployment topology).
3. **Signed design-partner agreement** including the observation/data terms in §4.2.
4. **Named PM contact** who completes the consent step — not a silent backend flip.

Initial wave target: **all active design partners** (G3 onboarding checklist makes observe-mode consent a standard step for every new partner thereafter). Tenants are enrolled via the per-tenant allowlist (#1) so posture is per-tenant, not deployment-global.

### 4.2 Operator consent & transparency

Consent is a product step, not a legal afterthought. The PM sees and acknowledges, during onboarding (or a dedicated in-console moment for existing partners):

**What changes:**
> "Cendra will start **watching** your automations. For every action an automation takes, Cendra records what it observed, how confident it was, and what it would have decided. Nothing about how your automations run changes — Cendra does not act on its own, block anything, or message anyone."

**What we collect:** a decision record per automation action (the inputs the automation saw, the gate evaluations, confidence, and outcome), stored tenant-scoped in their workspace's database.

**What it's for:** "This is your property's track record. It's what lets Cendra later *earn* autonomy on your workflows — and it's yours: you'll be able to see every record."

**What we don't claim:** no learning/self-improvement promise at enrollment (see §6, honest-data caveat), no autonomy claim, no "signed receipt" claim (certificates are not minted today — map row #3 is `partial`).

Consent is recorded (timestamp, PM identity, tenant) and the posture change itself is audited (§7, platform ask P3). PII handling note: DecisionCase payloads can contain guest-message content; the kernel's redaction stack exists but HASH redaction awaits a per-tenant secret provider (map row #14, `partial`) — see platform ask P5 and risk R3.

### 4.3 Rollout / rollback posture

- **Rollout unit:** per-tenant, via the gate chain's per-tenant allowlist. Deployment default stays `off`.
- **Sequence:** one tenant first (validate accrual metrics end-to-end on a second real tenant beyond the existing observe workspace), then the remaining partners in batches. No fixed calendar gates — the sequence gate is "accrual metrics visible and sane for the previous batch."
- **Rollback:** removing a tenant from the allowlist returns them to `off`. Rollback is **non-destructive**: accrued DecisionCases and calibration samples are retained (they are the tenant's history; retention policy follows the design-partner agreement). Rollback triggers: partner request, observed dispatch-latency regression attributable to the gate chain, or capture-error rate above threshold (§5 guardrail metric).
- **Re-enrollment** after rollback resumes accrual on the same ledger — idempotent capture (T7, ON-CONFLICT-DO-NOTHING) makes this safe.

### 4.4 What the PM sees during observe

Minimum lovable surface — observe mode must be *visible* to the PM or the consent story ("it's yours, you can see it") is hollow. In scope:

1. **Observe-mode status indicator** in the console: per-property "Cendra is watching" state with plain-language explanation (copy rules §8). No toggle in v1 — posture changes go through the program team (the toggle is an `enforce`-era product decision).
2. **Decision Card list (read-only):** the accruing ledger rendered as Decision Cards (terminology table: DecisionCase → "Decision Card"), newest first, per property. Each card: when, which automation, what Cendra observed, gate outcome in operator language, confidence where meaningful (see copy rule C4 — generic-dispatch confidence of 1.0 is *not* shown as "100% confident").
3. **Accrual counter:** "Cendra has recorded **N** decisions about your property since {enrollment date}" — the visible compounding number, and the seed of the G3 evidence pack.

Explicitly **out of scope** for this PRD's surface: Confidence Level / TrustMeter display (separate surface, display-only ruling, map #2), Needs Your Attention queue (no approval routing exists in observe — #13 is `partial`), Performance/ROI dashboards, any autonomy controls.

Surface ownership: Pixel (console) builds against existing read APIs; see §9 for what exists and §10 for what's missing.

### 4.5 What accrues (the point of the program)

Per gated dispatch, while a tenant is in observe:

- **One DecisionCase** in the tenant-scoped `brain_decision` ledger (#9) — idempotent capture from the dispatch stream.
- **Calibration samples** in the abstention gate's persistent calibration window (#4) — Wilson success-rate path active today; the conformal path sharpens once agent-loop confidence flows (Batch dependency, §7).
- **Nothing else.** No certificates (not minted), no Art. 12 records (not emitted), no TrustMeter movement claims, no pattern mining (beat jobs log-and-skip), no learning-loop consumption (Batch 6).

## 5. Success Metrics

| Metric | Definition | Target posture |
|---|---|---|
| **Per-tenant ledger accrual rate** (primary) | DecisionCases written per tenant per week | Nonzero and roughly tracking the tenant's automation dispatch volume — the metric is "is the moat widening for this tenant," not a vanity total |
| **Calibration-window fill** (primary) | Per-tenant sample count in the abstention calibration window vs. the window size needed for calibrated thresholds | Monotonic fill; flag tenants whose fill stalls (their automations aren't dispatching through gated paths) |
| **Enrollment coverage** | Active design partners in observe / total active design partners | 100% of partners meeting §4.1 criteria |
| **Capture-error guardrail** | Failed/errored capture per dispatch; dispatch latency delta attributable to T1/T3 wrappers | Below agreed threshold; breach triggers rollback review (§4.3) |

Honest-measurement rule: accrual rate counts **rows in the tenant's ledger**, not enrollments. A tenant flipped to observe whose ledger stays empty is a program failure for that tenant, not a success statistic.

## 6. Honest-Data Caveat (encoded, board-visible)

Every DecisionCase accrued under this program persists as **`scenario="general"`** — unclassified, **not yet learnable**. Classification arrives with Batch 5/6 work. Therefore:

- **The program promises accrual, not learning.** Day-one data builds the tenant's immutable history and fills the calibration window; it does not yet feed pattern mining, meso/macro learning loops, or promotion evidence in classified form.
- Partner-facing copy may say Cendra is "recording" and "building your track record." It may **not** say Cendra is "learning your property" from this data until classification ships and the relevant learning-loop rows read `implemented`.
- Internal planning may not schedule any G3 evidence-pack item that requires *classified* cases against observe-mode data alone.

This caveat is the difference between an honest compounding story and an over-claim. It is binding on demo scripts, onboarding decks, and console copy derived from this PRD.

## 7. Maturity: Live vs. Roadmap (with Batch dependencies)

**Live today (this PRD builds only on these):**

- Gate chain in observe posture (#1 — `implemented`, Batch 4; default OFF; per-tenant allowlist)
- Calibrated abstention recording to the calibration window (#4 — `implemented`; Wilson path active at generic dispatch)
- DecisionCase capture to the tenant-scoped ledger (#9 — `implemented` when gates active; T7, idempotent)

**Roadmap (named here so nobody reads them into scope; never demoed as live):**

| Dependency | Map row | Status | What it unblocks (not in this PRD) |
|---|---|---|---|
| Case classification (replaces `scenario="general"`) | #9 caveat | Batch 5/6 | Learnable cases; "Cendra learns your property" copy |
| Agent-loop confidence flowing to abstention | #4 note | Batch dependency on agent-loop wiring | Conformal calibration path sharpening; meaningful per-case confidence display |
| Certificate minting | #3 | `partial` — issuance unwired (Batch 5+ seam) | Action Receipts; "tamper-proof receipt" copy |
| Art. 12 record emission | #10 | `partial` — `audit_factory=None` | Compliance-receipt surface beyond Art. 50 |
| TrustMeter in-chain | #2 | `partial` — not consulted by live chain | Confidence Level "grows with history" claims |
| HITL approval routing | #13 | `partial` | Needs Your Attention queue semantics |
| Meso/macro learning loops | #7 | `partial → planned` (macro is Batch 6, not on `cendra/main`) | Any self-improvement narrative |

## 8. Hospitality Copy Rules (observe-mode)

Vocabulary is the FINAL terminology table (productization map §1); all copy below is **(a) Our surface**. Dify branding is never touched (board-owned license track).

- **C1 — The narrative is the demo-narrative rule, verbatim in spirit:** "Cendra is **watching, scoring, and accruing your ledger** today; autonomy is **earned and switched on per workflow**." All observe-mode copy is a variation of this sentence. Forbidden: any present-tense autonomy claim ("Cendra handles," "Cendra acts," "Cendra replies for you"), any signed-receipt claim, any TrustMeter-gating claim, any self-improvement claim.
- **C2 — Surface names:** DecisionCase → **Decision Card**; the ledger view is the operator's **track record** / "decisions recorded," never "training data," "ledger," or "DecisionCase." Gate-output copy follows the table (ABSTAIN → "I'm not sure — this needs you") **except** that in observe mode nothing "needs you" — observe-mode card copy uses past-conditional framing: *"Cendra **would have** asked you about this one."*
- **C3 — No enforcement implications:** observe-mode status copy must say explicitly that nothing changes: "Cendra doesn't act, block, or send anything on its own in this mode."
- **C4 — Confidence honesty:** while generic dispatch reports confidence=1.0 (Wilson path placeholder), per-card confidence is **not** rendered as a percentage. Show gate outcome language only, until agent-loop confidence flows (§7). A fabricated "100% confident" badge is an over-claim with extra steps.
- **C5 — Accrual, not learning** (§6): "recording," "building your track record," "watching and scoring" — yes. "Learning your property," "getting smarter every day" — not until classification ships.
- **C6 — Draft-mode adjacency:** if observe-mode copy references the ladder, Draft is presented only as the OBSERVE rung of the earned-autonomy ladder (CEN-10 ruling); "AI drafts your replies" standalone remains forbidden.
- **C7 — Persona:** copy addresses the **PM** ("you"); guests are never addressed by observe-mode surfaces.

## 9. Brain / service_api Capabilities Consumed

| Capability | Mechanism | Status |
|---|---|---|
| `BRAIN_GATES_MODE` posture + per-tenant allowlist | #1 gate chain config (T1/T3) | live |
| DecisionCase capture | #9 — T7 `cendra_decision_capture` → `brain_decision` | live (when gates active) |
| Case read API | `GET /v1/brain/cases` (service_api/brain) | live — Decision Card list (§4.4.2) reads this |
| Calibration persistence | #4 — `brain_calibration` (T5) | live (write path); no read/metrics surface — see P2 |

## 10. Platform Asks (for Atlas — to be converted into interface issues for Forge's org; not created from this issue)

- **P1 — Per-tenant observe enrollment interface.** A supported, auditable way to add/remove a tenant from the gate-chain allowlist without a code change — admin API or documented config operation with an audit trail. Today's mechanism (env-level config) is workable for tenant #2 but does not scale to "all partners, in batches, with rollback."
- **P2 — Accrual & calibration metrics read.** Per-tenant aggregates the program and console need: DecisionCase counts over time windows (count/aggregate semantics on `GET /v1/brain/cases` or a sibling endpoint) and calibration-window fill per tenant (`brain_calibration` has no read surface today). These power §5's two primary metrics.
- **P3 — Posture-change audit record.** A durable record (who, when, which tenant, off↔observe) for every posture change, queryable for the program log and the consent trail (§4.2).
- **P4 — Capture-health signal.** An operationally observable error/latency signal for T1/T7 capture (guardrail metric, §5) so rollback triggers are evidence-based rather than anecdotal.
- **P5 — Per-tenant redaction secret provider.** HASH redaction of PII in captured payloads awaits a per-tenant secret provider (map #14, `partial`). Required before observe-mode capture includes raw guest-message content at scale; until then, capture scope must be confirmed PII-safe (see R3).

Console build-out of §4.4 (status indicator, Decision Card list, accrual counter) is **Pixel's lane** against existing + P2 APIs and is scoped as implementation issues after this PRD is accepted — it is not a platform ask.

## 11. Acceptance Criteria

1. **Enrollment:** a design-partner tenant can be enrolled into observe per §4.1–4.2 — consent recorded, allowlist updated (via P1 or its interim documented operation), posture change audited (P3 or interim log).
2. **Accrual verified per tenant:** within one week of enrollment, the tenant's `brain_decision` ledger shows DecisionCases corresponding to real gated dispatches, and calibration-window fill is nonzero — verified via P2 (or direct query as interim evidence).
3. **No behavior change:** observe enrollment causes no dispatch refusals, no guest-visible changes, and capture-health (P4 or interim measurement) stays within the agreed guardrail.
4. **Rollback verified:** removing a tenant restores `off` posture with ledger retained; re-enrollment resumes accrual idempotently.
5. **Copy compliance:** every partner-facing string shipped under this program passes §8 rules C1–C7; no surface or deck claims learning, autonomy, signed receipts, or zero-core-edit integration.
6. **Metrics live:** §5's two primary metrics are reportable per tenant (dashboard or recurring report) — this is the input to the G3 evidence pack.

## 12. Out of Scope

- `enforce` mode anywhere, for anyone; any autonomy-promotion flow (separate PRD, gated on promotion-gate evidence).
- Operator-facing posture toggle (program-team controlled in this wave).
- Case classification, learning loops, pattern mining, certificate minting, Art. 12 emission, TrustMeter display/gating, HITL routing (§7 roadmap).
- Any change to deployment topology or the board-owned license track; any modification of Dify branding.

## 13. Risks

- **R1 — Empty-ledger enrollments.** A partner with low automation volume accrues slowly, making the program look stalled. Mitigation: selection criterion §4.1.1 and per-tenant accrual-rate reporting rather than aggregate totals.
- **R2 — Over-claim drift.** Sales/demo pressure to say "learning" or "autonomy" before the rows read `implemented`. Mitigation: §6 and §8 are binding; the map's demo-narrative rule is board-confirmed.
- **R3 — PII in captured payloads.** Until P5 lands, captured dispatch content may include guest PII with redaction incomplete. Mitigation: confirm capture scope for enrolled tenants is PII-safe before batch rollout; P5 is the structural fix; design-partner agreement discloses capture scope (§4.2).
- **R4 — Gate-chain latency regression at partner scale.** Observe posture adds wrapper work per dispatch. Mitigation: guardrail metric + rollback trigger (§4.3, §5).
