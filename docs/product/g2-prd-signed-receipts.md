# G2 PRD — Signed Criticality Receipts + Art. 12 / Art. 50 Emission

> **Owner:** Porter (Brain Kernel Engineer)
> **Status:** Design / PRD — **not** an implementation record. No mechanism described here is live; see §9 Maturity & Demo Rule.
> **Binding input (board-confirmed 2026-06-11, [CEN-4](/CEN/issues/CEN-4)):** [`moat-fit-map.md`](./moat-fit-map.md) — verdicts, Moat Maturity Ruling, and the observe-mode demo rule are in force.
> **Map rows this PRD builds on:** Part A **#3** (Signed criticality certificates) and **#10** (Art. 12 / Art. 50 governance receipts); Part C **Compliance Receipts** surface; Defensible Surface **2** (calibrated abstention + signed receipts).
> **Cross-links:** [Dify Capability Register](./dify-capability-register.md) · [Hospitality Productization Map](./hospitality-productization-map.md) · [EU AI Act Compliance Guide](../eu-ai-act-compliance.md)
> **Last updated:** 2026-06-11

---

## 0. Constraints inherited from the Moat Fit Map (binding)

These apply to every claim in this document and to any implementation that follows it:

1. **Cite the map rows.** This PRD is anchored to rows **#3** and **#10** and the **Compliance Receipts** productization surface. It introduces no new moat claim.
2. **No live-capability claim until the row reads `implemented`.** Rows #3 and #10 read `partial` today. Until the wiring in this PRD lands *and is verified*, no marketing, demo, or PRD copy may state that signed receipts are minted or emitted live. (§9.)
3. **No zero-core-edit claim.** Cendra is a *maintained fork*. The emission wiring rides existing touchpoints **T1** and **T7** (already core-editing files) plus additive kernel/service modules; it does **not** add a *new* core-edit touchpoint, but the product as a whole must never be described as zero-core-edit.
4. **License track is board-owned.** Out of scope here; no Dify branding is touched.

---

## 1. Problem statement

Defensible Surface 2 — *"calibrated abstention with signed governance receipts"* — is the compliance/trust differentiator that a generic Dify fork cannot match. Calibrated abstention (#4) is **live**. The "signed receipt" half is **not**:

- **#3** — the certificate module is ported but **certificates are never minted at runtime** (`runtime_gateway` constructs the verifier with a placeholder key and passes `certificate=None` on every request, so the certificate gate is always skipped). The signing primitive today is **HMAC (symmetric)**.
- **#10** — the **Art. 12 decision-record builder exists** (`compliance/art12_decision.py`) but is **never emitted**: the gate chain's `audit_factory` seam defaults to `None`, so `PipelineDecision.audit_record` is always `None`. **Art. 50** disclosure + PII redaction is live via the T4 moderation module; **Art. 12 per-action records are not**.

The surface ("Compliance Receipts") is therefore a **shell with no artifact behind it**. This PRD specifies: (a) the signing-scheme decision, (b) the emission wiring at named attachment points, (c) receipt storage/retrieval, and (d) the operator-facing surface framing.

### What already exists (code-verified, `origin/cendra/main`)

| Component | Path | State |
|---|---|---|
| Certificate value object / issuer / verifier | `api/core/brain/certificates/{cert,issuer,verifier,policy,tier}.py` | Ported; HMAC-SHA256; **unwired** (placeholder key, `certificate=None`) |
| Gate chain + `audit_factory` seam | `api/core/brain/gates.py::DecisionPipelineAdapter` | Ported; `audit_factory=None` → `audit_record=None` |
| Runtime gateway (T1 kernel side) | `api/core/brain/runtime_gateway.py::evaluate_tool_dispatch` / `record_tool_outcome` | Live; constructs adapter **without** audit_factory; cert verifier uses `_PLACEHOLDER_KEY` |
| Art. 12 record schema + chain digest | `api/core/brain/compliance/art12_decision.py` (`Art12Decision`, `chained_digest`, BLAKE2B) | Ported; **never built/persisted** |
| Append-only audit log | `api/core/brain/compliance/audit.py` (`AuditLogger` Protocol, `InMemoryAuditLogger`) | Protocol + in-memory only; **no durable backend** ("next branch") |
| Outcome ledger (#9) | `models/brain_decision.py::BrainDecisionCase` + `patterns/case_store.py` (T7) | Live when gates active; **no cert / Art. 12 columns** |
| Service API surface | `controllers/service_api/brain/__init__.py` | retrieval / trust-meter / policies / cases; **no receipts endpoint** |

---

## 2. Signing-scheme decision (acceptance criterion #1)

### 2.1 The decision

**Adopt public-key (Ed25519) signing for the dispute-grade receipt. Reject HMAC for that artifact.** The internal short-lived *authorization* certificate may remain HMAC (§2.4).

### 2.2 Why HMAC fails the stated use case

Map row #3's promise is third-party dispute evidence: *"if a guest disputes a charge the system handled, you have a signed log of exactly why and when."* The parties to such a dispute are the **operator**, the **guest**, a **regulator/court**, and **Cendra** — four *distinct trust domains*.

HMAC-SHA256 is symmetric: **the verifier holds the same secret the signer used.** Whoever can verify can also forge. Consequences:

- If the key lives in the operator's tenant config (`BRAIN_AUTONOMY_CERT_KEY`, the current design), the operator can mint or backdate any receipt. A guest or regulator cannot treat it as evidence *against* the operator — it carries **zero non-repudiation**.
- If the key lives only with Cendra, a guest/regulator still cannot verify a receipt **without Cendra disclosing the key**, and disclosing it would let the recipient forge. Verification and forgeability are inseparable under symmetric keys.

HMAC delivers **integrity within one trust domain** (detecting accidental corruption or tampering by a party *without* the key). It cannot deliver **non-repudiation across trust domains**, which is exactly what "dispute evidence" means. The chained BLAKE2B digest in `art12_decision.py` is in the same category — it detects backdated insertion *within* the log, but proves nothing to an outside party about authorship.

### 2.3 Why Ed25519

- **Non-repudiation.** A private key held by Cendra's control plane signs; the corresponding public key is published. Anyone — guest, regulator, court — verifies independently and **cannot forge**. The operator cannot later deny or fabricate a receipt.
- **Contained change.** The receipt signs the *canonical bytes of the Art. 12 record* (`canonical_record()` already produces deterministic, sorted-key JSON). Only the signing/verifying primitive changes (`hmac.new` → Ed25519 sign; `compare_digest` → Ed25519 verify). `signature_hex` stays a hex field; no payload-format break for downstream readers.
- **Fast & small.** Ed25519 sign/verify is sub-millisecond; 64-byte signatures. No perf concern at dispatch rates.
- **Publishable trust root.** Public keys exposed via a JWKS-style endpoint (§5.3) so verification needs no Cendra cooperation.

### 2.4 Two distinct artifacts (design clarification)

The ported `AutonomyCertificate`/`CertificateVerifier` was built as an **input authorization token** ("this `(action_kind, property, owner)` tuple may act at this tier until `expires_at`"), verified *before* dispatch by the certificate gate. Row #3's dispute-evidence need is an **output attestation** ("this decision was made; here is the signed proof of what, why, and at what confidence").

This PRD separates them:

| Artifact | Role | Direction | Signing | Verifier |
|---|---|---|---|---|
| **Authorization certificate** (`AutonomyCertificate`) | Pre-grant a tier for a window; consumed by the certificate gate | Input to gate chain | HMAC acceptable (internal, server-to-server, short-lived) | `CertificateVerifier` in-chain |
| **Criticality receipt** (new envelope over `Art12Decision`) | Signed record of the decision/reasoning/confidence/tier at the moment of action | Output at PROCEED | **Ed25519 (required)** | Public — anyone with the published key |

Today's HMAC-only design conflated the two. The receipt — the thing the operator shows a guest or regulator — is the Ed25519-signed envelope. Wiring the authorization certificate into the cert gate (so `certificate=None` stops skipping it) is **separate, downstream, and optional** for Surface 2; it is *not* required to make receipts real and is therefore deferred (§7).

### 2.5 Key custody (honest dependency)

- **Per-tenant Ed25519 keypair.** Private key custody in a **KMS-backed per-tenant secret provider** — the *same* infra dependency the map notes is awaited for #14's HASH redaction (`HASH redaction awaits per-tenant secret provider`). Receipt minting therefore **shares and is blocked on** that secret-provider work; this PRD does not re-scope it.
- **Key rotation** with a `key_id` stamped into each receipt; old public keys stay published so historical receipts remain verifiable.
- **Fallback posture:** if no signing key is provisioned for a tenant, the emitter records an **unsigned** Art. 12 record (chain-linked, integrity-only) and flags it `signed=false`. No silent failure, no fake signature. The surface (§6) renders unsigned records honestly.

---

## 3. What the receipt binds (so it is real evidence)

The current `AutonomyCertificate` payload binds only authorization scope (`action_kind|property|owner|tier|window`). A dispute-grade receipt must additionally bind the **decision**. The receipt envelope signs the `Art12Decision` canonical bytes, which carry:

- `decision_id` — the join key to the run record / DecisionCase (#9) and the gate trace.
- `occurred_at` (tz-aware UTC), `property_id`, `owner_id`, `action_kind`.
- `autonomy_tier` (#3) and `handler_solver` (llm / utility / smt / deterministic / hitl).
- `rationale` (one-line plain-English) and **`provenance_digest`** (BLAKE2B over the evidence bundle that drove the decision — rules/cases/blockers/facts).
- `prev_digest` → chain link (backdated insertion breaks the chain).
- **Added by this PRD:** a compact `gate_trace` summary (per-gate verdict + the abstention gate's **calibrated confidence**) carried in `Art12Decision.extra`, so "reasoning and confidence at the moment of action" (row #3's exact wording) is inside the signed bytes — not just alongside them.

The Ed25519 signature is computed over `canonical_record(decision)`; `key_id` and `signature_hex` ride beside the record in the persisted receipt.

---

## 4. Emission wiring (acceptance criterion #2)

The emission path mirrors the existing **two-phase** dispatch/outcome structure (`evaluate_tool_dispatch` then `record_tool_outcome`).

### 4.1 Attachment point A — mint at PROCEED via the `audit_factory` seam

The seam already exists: `DecisionPipelineAdapter.__init__(..., audit_factory: Callable[[PipelineRequest, datetime], Any] | None = None)`. On a PROCEED verdict the adapter calls it and rides the result on `PipelineDecision.audit_record` (the reference's "PROCEED-must-carry-audit" invariant).

**Wiring:** implement a real factory — `ReceiptEmitter` (new: `api/core/brain/compliance/receipt_emitter.py`) — and inject it in `runtime_gateway._adapter_for(tenant_id)` (today it passes no `audit_factory`). On PROCEED the emitter:

1. Builds `Art12Decision` from the `PipelineRequest` + decision (incl. the §3 `gate_trace`/confidence in `extra`).
2. Reads the tenant's audit-chain head (`last_digest`), sets `prev_digest`, computes `chained_digest`.
3. Mints the **Ed25519 receipt signature** over `canonical_record(...)` using the tenant signing key (§2.5); records `key_id`. If no key → unsigned, `signed=false`.
4. Persists the receipt (§5.1), keyed by `decision_id`, idempotent (`ON CONFLICT (decision_id) DO NOTHING`, matching the ledger's append semantics).
5. Returns the record so it rides on `PipelineDecision.audit_record`.

**Observe vs. enforce:** `evaluate_tool_dispatch` returns early in observe mode *after* `decide()` has run — so the emitter fires in **both observe and enforce**. This is intentional and aligned with the Maturity Ruling: *"Cendra is watching, scoring, and accruing your ledger today."* Receipts accrue in observe; enforcement is a separate axis. (Minting in observe does **not** unlock any live-receipt marketing claim — §9.)

### 4.2 Attachment point B — outcome stitch at T7

`record_tool_outcome` / `api/core/callback_handler/cendra_decision_capture.py` (T7) already captures the DecisionCase keyed by `conversation_id` and feeds calibration. **Extend T7** to stitch the executed action's outcome (success/failure) back onto the receipt by `decision_id`, and to write the receipt's `case_id` so the run record ↔ receipt link is bidirectional. The receipt's decision phase (A) and outcome phase (B) together form one complete record. T7 stays idempotent.

### 4.3 Durable audit backend

`compliance/audit.py` ships only `InMemoryAuditLogger` (chain dies with the process). Implement a **SQLAlchemy-backed `AuditLogger`** (new: `api/core/brain/compliance/sa_audit.py`, mirroring the `patterns/case_store.py` sync/tenant-scoped convention) that preserves the chain head across restarts by reading the last committed `record_digest` for the tenant. The `ReceiptEmitter` depends on this, not on the in-memory logger.

### 4.4 Touchpoint summary (no new core edit)

| Step | Module | New / edit | Touchpoint |
|---|---|---|---|
| `ReceiptEmitter` | `core/brain/compliance/receipt_emitter.py` | new (additive kernel) | — |
| Ed25519 sign/verify | `core/brain/certificates/signing.py` | new (additive kernel) | — |
| Durable audit logger | `core/brain/compliance/sa_audit.py` | new (additive kernel) | — |
| Inject `audit_factory` | `core/brain/runtime_gateway.py::_adapter_for` | edit (kernel, additive) | — |
| Outcome stitch | `core/callback_handler/cendra_decision_capture.py` | edit | **T7** (already an edited file) |
| Receipt table | new Alembic migration + `models/brain_receipt.py` | new (additive) | — |
| Service API | `controllers/service_api/brain/__init__.py` | edit (additive routes) | C6 additive import (existing) |

No *new* numbered core-edit touchpoint is introduced; the only upstream-file edit (T7) is already a recorded touchpoint. **This is not a zero-core-edit claim** (§0.3) — it is a statement that the fork's edit surface does not grow.

---

## 5. Storage & retrieval

### 5.1 Receipt store (new table)

`brain_governance_receipts` (additive migration; append-only — no UPDATE/DELETE API path):

| Column | Notes |
|---|---|
| `id`, `tenant_id`, `created_at` | standard |
| `decision_id` | **unique**; join key to gate trace + run record |
| `case_id` | nullable until T7 stitch; links to `brain_decision_cases.case_id` |
| `conversation_id` | T7 join key |
| `property_id`, `owner_id`, `action_kind`, `autonomy_tier` | scope |
| `occurred_at`, `rationale`, `provenance_digest` | from Art. 12 record |
| `record_json` | canonical Art. 12 record (the signed bytes, verbatim) |
| `prev_digest`, `record_digest` | chain link (tamper evidence) |
| `signed` (bool), `key_id`, `signature_alg` (`ed25519`), `signature_hex` | signature; `signed=false` ⇒ no `signature_hex` |
| `outcome_json` | stitched at T7 phase B; nullable |

The chain (`prev_digest` → `record_digest`) gives sequence-integrity; the Ed25519 `signature_hex` gives non-repudiation. Both are independently checkable.

### 5.2 Service layer

`BrainGovernanceService` (existing, `services/brain_governance_service.py`) gains `list_receipts(property_id, limit, offset)`, `get_receipt(decision_id)`, and `verify_receipt(decision_id)` (re-checks Ed25519 signature **and** recomputes the chain digest, returning a structured pass/fail per check, mirroring `CertificateVerifier`'s distinct-outcome style).

### 5.3 Service API (additive routes, service-token auth like every `service_api` route)

- `GET /v1/brain/receipts?property_id=&limit=&offset=` — list (paginated, newest-first).
- `GET /v1/brain/receipts/<decision_id>` — one receipt incl. chain links and signature.
- `GET /v1/brain/receipts/<decision_id>/verify` — independent verification result.
- `GET /v1/brain/receipts/export?from=&to=` — Art. 12 batch export for a regulator (NDJSON of canonical records + chain head).
- `GET /v1/brain/receipts/jwks` — published per-tenant **public** verification keys (enables third-party verification without Cendra).

---

## 6. Operator surface — "Compliance Receipts" (acceptance criterion #4)

Aligns with the Part C row: anchored to MOAT (#3 + #10), *"ship the surface, but no signed receipts are minted today… label receipts 'pending' until that wiring lands; do not demo signed receipts as live."*

- **Surface:** a per-property Compliance Receipts list. Each row: timestamp, action kind, autonomy tier, decision verdict, calibrated confidence, one-line rationale, **verification status** (✓ signed & chain-intact / ⚠ unsigned-integrity-only / ✗ verification failed), and a chain-link indicator. Row detail shows the full Art. 12 record + a one-click **Verify** (calls §5.3) and **Export for regulator**.
- **Honest banner until rows #3/#10 read `implemented`:** *"Governance receipts are being recorded in observe mode. Dispute-grade signed receipts activate once your workspace's gate chain and certificate minting are enabled."* No copy asserts live signed receipts.
- **Unsigned rows render as unsigned** (`signed=false`) — never as signed. The verification status column makes the difference legible to the operator.
- **Anchoring is explicit:** the surface is the face of #3 (signed criticality) + #10 (Art. 12/50) over the #9 ledger. Stripped of those mechanisms it is a generic audit-log view (TABLE-STAKES, the Dify EU AI Act guide) — so the surface must never ship decoupled from real emission.

---

## 7. Out of scope / deferred

- **Authorization-certificate enforcement** (stop `certificate=None` skipping the cert gate; mint pre-grants at tier-grant time). Separate from receipts; tracked against #3's gate role, not Surface 2.
- **Owner-policy DSL (#6/#2) writing `TierPolicy` ceilings.** Receipts consume whatever tier was granted; they do not author policy.
- **HSM/KMS provisioning itself** — shared dependency with #14's per-tenant secret provider (§2.5); this PRD depends on it, does not build it.
- **Art. 50 changes** — already live via T4; this PRD only *references* it in the receipt (records that disclosure occurred).

---

## 8. Acceptance-criteria trace

| Acceptance criterion | Addressed in |
|---|---|
| Signing-scheme decision with rationale (HMAC vs public-key, defensibility + dispute-evidence) | §2 (Ed25519 for receipts; HMAC rejected for non-repudiation; HMAC retained only for internal auth cert) |
| Emission wiring at named attachment points | §4 (`audit_factory` seam in `_adapter_for`; T7 outcome stitch; durable audit backend) |
| No marketing/demo claim of live receipts until rows read `implemented` | §0.2, §4.1, §6 banner, §9 |
| Operator surface framing aligned with productization map | §6 (Compliance Receipts, pending/observe banner, anchored to #3/#10/#9) |

---

## 9. Maturity & demo rule (binding)

Per the **Moat Maturity Ruling** ([`moat-fit-map.md`](./moat-fit-map.md) §Atlas Synthesis): certificate minting (#3) and Art. 12 emission (#10) are **"Designed, not live."** This PRD is a **roadmap with explicit dependencies**, not a shipping record. Rows #3 and #10 stay `partial` until: (a) the Ed25519 signing + per-tenant key provider land, (b) `ReceiptEmitter` is wired into `_adapter_for` with the durable audit backend, (c) the receipt store + retrieval ship, and (d) all are verified. **Only then** may any row be flipped to `implemented` and only then may demo/marketing reference live signed receipts. Until then the surface shows the §6 observe-mode banner and "today's design is HMAC, not public-key" remains the honest status of what is *in the code* (this PRD is the decision to change it, not the change itself).
