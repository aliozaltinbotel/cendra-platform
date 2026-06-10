# Brain Engine — Filing-Ready Patent Claim Drafts

**Status:** draft v0.1 — 2026-05-11
**Owner:** Cendra / Bookly
**Inventor:** Devlet Ataoglu (and contributors per repository history)
**Prepared by:** in-house engineering, awaiting outside-counsel review before USPTO filing

This document collects USPTO-style independent-claim drafts for the
seven patent candidates identified in `latest_research.md` plus one
additional candidate (Sprint 5, MAR moat) shipped in the M25 Reflexion
Critic + Friction tracker.  Each candidate has:

1. A short technical-novelty statement contrasting prior art.
2. One **independent system claim**.
3. One **independent method claim** drafted as a means-equivalent.
4. One **CRM (computer-readable medium) claim** mirroring the method.
5. 1–3 **dependent claims** illustrating preferred embodiments.
6. **Implementation pointers** — code paths in the dev branch that
   reduce the invention to practice.

All claims are drafted in the active voice, recite **specific
technical limitations** rather than abstract ideas, and avoid pre-
emption by tying each step to **named, measurable artifacts** that
the dev-branch implementation can be inspected against.

---

## Table of contents

1. **Candidate 1 (M1 / M24)** — Bi-temporal Wilson + Conformal
   Abstention gate for regulated-domain agentic tools
2. **Candidate 2 (M13 / M17)** — Property Twin Brain — non-medical
   Digital Twin with imagined-rollout reward
3. **Candidate 3 (M2 / M22)** — Owner-policy DSL with SMT-backed
   semantic verifier
4. **Candidate 4 (M3)** — Criticality-tiered autonomy ladder with
   HMAC-signed autonomy certificates
5. **Candidate 5 (M7)** — Observation-vs-belief schema with
   conformal-bound belief promotion across multi-tier memory
6. **Candidate 6 (M14)** — Interaction protocol between online ACE
   loop, per-step RL-policy memory updates, and sleep-time
   consolidation
7. **Candidate 7 (M12 / M15 / M16)** — HTN + LATS-MCTS + retrieval-
   augmented world-model hybrid with tool-grounded metacognitive
   monitor
8. **Candidate 8 (M25 — Sprint 5)** — Memory-Augmented Reflexion:
   verbal critic output translated into a per-state scalar friction
   multiplier on the policy's reward signal

---

## Candidate 1 — Bi-temporal Wilson + Conformal Abstention gate

### Novelty contrast
Wilson lower bound (Wilson, 1927), bi-temporal databases (Snodgrass,
2000) and split-conformal prediction (Vovk-Gammerman-Shafer, 2005)
are individually mature.  No published agentic-tool runtime composes
**(a) per-rule bi-temporal lifecycle filtering**, **(b) Wilson lower-
bound calibration on the surviving rules**, and **(c) split-conformal
coverage gating** as a single abstention decision in regulated
domains (property management, healthcare-adjacent, financial advice).

### 1. Independent system claim
A computer-implemented system for selectively abstaining from
invoking an agentic tool, the system comprising:
- a **bi-temporal rule store** configured to record, for each
  candidate decision rule, at least the fields `valid_from`,
  `valid_to`, `invalid_at`, `deactivated_at`, and `last_seen_at`;
- a **temporal filter** configured to admit, at decision time, only
  those rules whose `valid_from <= now < valid_to` and
  `invalid_at IS NULL`;
- a **Wilson-lower-bound calibrator** configured to compute, on the
  filtered rules' empirical success counts, the Wilson lower bound
  at a configured confidence level `z`;
- a **split-conformal calibrator** configured to compute, over a
  bounded calibration window of post-hoc-labeled outcomes, an
  empirical `(1 - alpha)` non-conformity quantile via the
  correction `ceil((n + 1)(1 - alpha)) / n`;
- an **abstention gate** configured to (i) return PROCEED when the
  Wilson lower bound exceeds a Wilson threshold AND the conformal
  prediction set is a singleton, (ii) return ABSTAIN otherwise,
  and (iii) return INSUFFICIENT_DATA when the calibration window is
  below a configured minimum size;
- an **audit module** configured to persist, for each abstention
  decision, the rule identifiers admitted, the Wilson lower bound,
  the conformal threshold, and the resulting verdict.

### 2. Independent method claim
A computer-implemented method for selectively abstaining from
invoking an agentic tool in a regulated domain, comprising:
- maintaining, in non-transitory storage, a bi-temporal rule store
  recording per-rule `valid_from`, `valid_to`, `invalid_at`,
  `deactivated_at`, and `last_seen_at` fields;
- responsive to a tool-invocation request, retrieving only those
  rules whose temporal interval is open at the current instant;
- computing a Wilson lower bound on the success rate of the
  retrieved rules at a configured confidence level;
- computing an empirical split-conformal `(1 - alpha)` quantile on
  a bounded sliding calibration window of post-hoc-labeled
  outcomes, using the `ceil((n + 1)(1 - alpha)) / n` correction;
- emitting a three-valued verdict PROCEED, ABSTAIN, or
  INSUFFICIENT_DATA according to a deterministic combination of
  the Wilson lower bound, the conformal set cardinality, and the
  calibration window size;
- persisting an audit record carrying the rule identifiers, the
  Wilson lower bound, the conformal threshold, and the verdict.

### 3. CRM claim
A non-transitory computer-readable medium storing instructions that,
when executed by one or more processors, cause the processors to
perform the method of Claim 2.

### Dependent claims
- 4. The system of Claim 1 wherein the split-conformal calibrator
  uses Least Ambiguous set-valued Classifier (LAC) non-conformity
  scoring.
- 5. The system of Claim 1 wherein the audit module is bi-temporal
  and exposes a regulator-facing query interface returning the
  full sequence of abstention decisions made for a named property
  and named tool within a queried time interval.
- 6. The method of Claim 2 wherein the regulated domain is property
  management and the agentic tool is selected from at least
  pricing, messaging, booking acceptance, and refund.

### Implementation pointers (dev branch)
- `brain_engine/abstention/__init__.py`
- `brain_engine/abstention/calibrator.py` (Wilson)
- `brain_engine/abstention/split_conformal.py` (pure-Python)
- `brain_engine/abstention/mapie_calibrator.py` (library-backed)
- `brain_engine/abstention/gate.py` (combination)
- `brain_engine/patterns/postgres_rule_store.py` (bi-temporal)

---

## Candidate 2 — Property Twin Brain (non-medical Digital Twin)

### Novelty contrast
Industrial Digital Twins (Grieves, 2003 onwards) and medical Digital
Twins (Bjornsson et al., 2020) are mature.  RSSM-style latent world
models (Hafner et al., DreamerV3, 2023) and retrieval-augmented world
models (Liu et al., R-WoM, 2024) are mature in robotics.  No published
system applies a Digital Twin Brain to **short-stay rental
properties** with a latent state that fuses bookings, channel events,
owner policies, and historical guest interactions into an
**imagined-rollout reward signal** consumed by a Memory-R1 policy.

### 1. Independent system claim
A computer-implemented system for predicting outcomes of candidate
agent actions on a short-stay rental property, the system
comprising:
- a **Property Twin store** configured to maintain a per-property
  state vector fusing at least booking history, channel-event log,
  owner-policy state, and historical guest-interaction outcomes;
- a **world-model module** configured to predict, from the
  Property Twin state and a candidate action, a numeric reward
  estimate;
- an **imagined-rollout module** configured to roll the world
  model forward for `k` simulated steps under a candidate policy
  and accumulate a discounted reward;
- a **Memory-R1 trainer** configured to use the imagined-rollout
  reward as the advantage signal in a group-relative policy
  optimisation update.

### 2. Independent method claim
A computer-implemented method comprising the analogous steps of
Claim 1.

### 3. CRM claim
Mirror of Claim 1.

### Dependent claims
- 4. The system of Claim 1 wherein the world-model module is a
  linear analytical baseline.
- 5. The system of Claim 1 wherein the world-model module is a
  recurrent state-space model with a learned latent.
- 6. The system of Claim 1 wherein the imagined-rollout module is
  invoked only when the abstention gate of Candidate 1 returns
  PROCEED.

### Implementation pointers (dev branch)
- `brain_engine/property_twin/__init__.py`
- `brain_engine/property_twin/linear_world_model.py` (linear baseline)
- `brain_engine/property_twin/twin.py`
- `brain_engine/property_twin/protocols.py` (seam for v1.0 RSSM)
- `brain_engine/cognition_loops/grpo.py` (advantage trainer)

---

## Candidate 3 — Owner-policy DSL with SMT-backed verifier

### Novelty contrast
Domain-specific languages for access control (XACML, OPA Rego),
formal verification of policies (Z3, CVC4), and Lark-based parsers
are individually mature.  No published system pairs **a Lark-parsed
DSL that accepts numeric range constraints expressed by owners in
natural-language-adjacent syntax** with **a Z3 SMT verifier that
proves the rule set is satisfiable, identifies which rule subset
witnesses a violation, and emits the witnessing assignment** for
property-management owner policies.

### 1. Independent system claim
A computer-implemented system for enforcing owner-supplied
policies on an agentic property-management runtime, comprising:
- a **DSL parser** configured to parse a domain-specific language
  expressing per-property numeric and symbolic constraints,
  including ranges on `min_nights`, `nightly_rate`, `max_guests`,
  and categorical equalities on guest attributes;
- a **compiler** configured to translate parsed rules into Z3 SMT
  formulas;
- a **verifier** configured to (i) check satisfiability of the
  conjunction of rules, (ii) on UNSAT, return a minimal witnessing
  subset of rules, and (iii) on SAT, return the assignment;
- a **runtime guard** configured to evaluate a candidate action
  against the verified rule set and reject the action when the
  verifier reports a witnessed violation.

### 2. Independent method claim, 3. CRM claim
Mirror of Claim 1.

### Dependent claims
- 4. The system of Claim 1 wherein the DSL exposes hard-constraint
  syntax `must <attribute> <op> <literal>` and soft-constraint
  syntax `prefer <attribute> <op> <literal>` and the compiler
  emits the latter as soft assertions to a MaxSMT solver.
- 5. The system of Claim 1 wherein the runtime guard composes with
  the abstention gate of Candidate 1, requiring both PROCEED and
  verifier SAT before the candidate action is invoked.

### Implementation pointers (dev branch)
- `brain_engine/owner_policy/grammar.lark`
- `brain_engine/owner_policy/parser.py`
- `brain_engine/owner_policy/ast.py`
- `brain_engine/owner_policy/compiler.py`
- `brain_engine/owner_policy/z3_compiler.py` (SMT verifier)
- `brain_engine/owner_policy/registry.py`

---

## Candidate 4 — Criticality-tiered autonomy ladder with HMAC certificates

### Novelty contrast
Tiered-autonomy frameworks (Sheridan-Verplank levels of automation;
ISO 9241-810) and HMAC-signed authorisation tokens (RFC 2104) are
individually mature.  No published agentic-runtime system pairs
**five action-criticality tiers** with **HMAC-SHA256 autonomy
certificates that carry both the tier admitted to the agent and the
named class of actions the certificate authorises**, gated by a
runtime verifier that refuses to dispatch a tool call whose tier
exceeds the certificate's tier.

### 1. Independent system claim
A computer-implemented system for governing autonomous-agent tool
invocation, comprising:
- an **action-class registry** mapping each tool to one of five
  criticality tiers (T0–T4) ranked by reversibility, blast radius,
  and regulator exposure;
- an **autonomy-certificate issuer** configured to emit, for a
  given operator-principal pair, an HMAC-SHA256 signature over a
  canonical certificate body that names the tier admitted and the
  list of action classes authorised;
- a **runtime verifier** configured to refuse a tool invocation
  whose tier exceeds the certificate's tier;
- an **audit module** configured to persist, for each invocation,
  the certificate identifier, the action class, and the verifier's
  verdict.

### 2. Independent method claim, 3. CRM claim
Mirror of Claim 1.

### Dependent claims
- 4. The system of Claim 1 wherein the issuer rotates HMAC keys on
  a fixed cadence and the verifier accepts the union of the
  current and immediately-prior keys.
- 5. The system of Claim 1 wherein the audit module is bi-temporal.

### Implementation pointers (dev branch)
- `brain_engine/certificates/__init__.py`
- `brain_engine/certificates/cert.py`
- `brain_engine/certificates/issuer.py`
- `brain_engine/certificates/verifier.py`
- `brain_engine/certificates/policy.py`
- `brain_engine/certificates/tier.py`
- `brain_engine/autonomy/gate.py`
- `brain_engine/autonomy/engine.py`

---

## Candidate 5 — Observation-vs-belief schema with conformal-bound belief promotion

### Novelty contrast
Observation/belief schemata (POMDP literature, Kaelbling 1998),
multi-tier memory (Letta 2024; MemGPT 2023), and conformal prediction
are individually mature.  No published system maintains a
**bi-temporal `observation` vs `belief` distinction at every memory
tier** (working, episodic, semantic, procedural, KG) and **promotes
an observation to a belief only when the conformal prediction set on
that observation collapses to a singleton**.

### 1. Independent system claim
A computer-implemented system for managing agentic memory with
calibrated belief promotion, comprising:
- a **multi-tier memory store** with at least working, episodic,
  semantic, and procedural tiers;
- a **per-tier observation log** recording, for each datum, the
  source channel, the recording instant, and the post-hoc outcome
  when available;
- a **belief store** distinct from the observation log;
- a **belief promoter** configured to consult a split-conformal
  calibrator on the observation log and promote an observation to
  a belief only when the conformal prediction set is a singleton.

### 2-3. Method and CRM claims
Mirror of Claim 1.

### Dependent claims
- 4. The system of Claim 1 wherein the belief store is bi-temporal
  with `valid_from`, `valid_to`, `invalid_at`, `deactivated_at`,
  and `last_seen_at` fields.
- 5. The system of Claim 1 wherein the belief promoter additionally
  consults a Wilson lower bound on the observation log.

### Implementation pointers (dev branch)
- `brain_engine/epistemic/__init__.py`
- `brain_engine/epistemic/models.py`
- `brain_engine/epistemic/store.py`
- `brain_engine/epistemic/promotion.py`
- `brain_engine/memory/`
- `brain_engine/abstention/split_conformal.py`

---

## Candidate 6 — ACE + sleep + Memory-R1 interaction protocol

### Novelty contrast
The Online ACE loop (Zhang et al., arXiv:2510.04618), Memory-R1
per-step RL policy (Yan et al., arXiv:2508.19828), and Letta-style
nightly sleep-time consolidation (arXiv:2504.13171) are each
published in isolation.  No published agentic runtime composes all
three with an **explicit interaction protocol** that resolves
conflicts when the ACE Curator writes ADD while Memory-R1 votes
DELETE, or when the Reflector rejects an ACE candidate.

### 1. Independent system claim
A computer-implemented system for autonomous-agent memory writes,
comprising:
- an **ACE loop module** running Generator → Reflector → Curator
  on every action and emitting an `AceCycle` value object;
- a **Memory-R1 policy module** voting one of `ADD`, `UPDATE`,
  `DELETE`, `NOOP`, `SUMMARIZE`, `RETRIEVE` on the same target as
  the ACE cycle;
- an **interaction protocol** module configured to deterministically
  resolve the joint output via a conflict-resolution state
  machine that returns one of `READ-ONLY-PASS`, `NOOP-VETO`,
  `DEFER`, `RATIFY`, with `DEFER` triggering a nightly sleep-time
  consolidation worker that consumes the deferred entries and
  emits a playbook delta.

### 2-3. Method and CRM claims
Mirror of Claim 1.

### Dependent claims
- 4. The system of Claim 1 wherein the Memory-R1 policy is a
  multinomial-logit classifier trained by group-relative policy
  optimisation on imagined-rollout rewards from a Property Twin.
- 5. The system of Claim 1 wherein the sleep-time worker emits a
  playbook bump only when the number of resolved decisions in the
  consolidation window exceeds a configured threshold.

### Implementation pointers (dev branch)
- `brain_engine/cognition_loops/protocol.py`
- `brain_engine/cognition_loops/sleep.py`
- `brain_engine/cognition_loops/policy.py`
- `brain_engine/cognition_loops/trainer.py`
- `brain_engine/cognition_loops/grpo.py`

---

## Candidate 7 — HTN + LATS-MCTS + retrieval world-model hybrid

### Novelty contrast
HTN planning (Nau et al., 1999), LATS-MCTS (Zhou et al., 2024) and
retrieval-augmented world models (R-WoM, 2024) are mature in
isolation.  No published agentic-runtime system fuses **HTN macro-
actions tailored to short-stay-rental flows**, **LATS-MCTS expansion
with UCB1 over those macro-actions**, **retrieval-augmented value
estimation grounded in a bag-of-name plus embedding similarity
score**, and a **tool-grounded metacognitive monitor** that rejects
expansions whose tool calls fail the abstention gate.

### 1. Independent system claim
A computer-implemented system for long-horizon agentic planning,
comprising:
- an **HTN module** decomposing tasks into named macro-actions
  drawn from a per-domain library;
- a **LATS-MCTS expansion module** searching over the macro-action
  space with a UCB1 tree policy;
- a **retrieval-augmented value estimator** scoring leaf nodes by
  combining a bag-of-name overlap with an embedding-similarity
  lookup against a knowledge base;
- a **metacognitive monitor** configured to reject any expansion
  whose macro-action's tool calls fail an abstention gate that
  combines Wilson lower bound, split-conformal coverage, and
  owner-policy verifier verdicts.

### 2-3. Method and CRM claims
Mirror of Claim 1.

### Dependent claims
- 4. The system of Claim 1 wherein the embedding-similarity lookup
  uses a sklearn `NearestNeighbors` ANN index with cosine metric.
- 5. The system of Claim 1 wherein the retrieval-augmented value
  estimator falls back to the bag-of-name overlap when the ANN
  index is unavailable.

### Implementation pointers (dev branch)
- `brain_engine/htn/__init__.py`
- `brain_engine/htn/planner.py`
- `brain_engine/htn/search.py` (LATS MCTS expansion)
- `brain_engine/htn/tree.py`
- `brain_engine/htn/rwom.py` (bag-of-name R-WoM)
- `brain_engine/htn/embedding_rwom.py` (ANN-backed R-WoM)
- `brain_engine/htn/ann_corpus.py`

---

## Candidate 8 — Memory-Augmented Reflexion (MAR) Critic + Friction

### Novelty contrast
Reflexion-style verbal reinforcement (Shinn et al., arXiv:2303.11366)
and reward-shaping memory of past failures (Memory-Augmented
Reflexion, arXiv:2512.20845) are each published in isolation.  No
published agentic-runtime system translates the **verbal critique
output** into a **per-state, per-action scalar friction multiplier
in `[0, 1]`** computed via `exp(-alpha * max(0, -EMA_reward) *
log1p(count))` and applied to the next reward signal a Memory-R1 /
GRPO trainer consumes.

### 1. Independent system claim
A computer-implemented system for memory-augmented reinforcement
of an agentic memory-write policy, comprising:
- a **Critic module** receiving a trajectory of
  `(features, chosen_action, reward)` tuples and emitting a
  natural-language reflection string plus a per-action
  `avoidance_hints` mapping in `[0, 1]`;
- a **Friction tracker** keyed by `(state_key, action)` with an
  exponential moving average of realised reward and an observation
  count, exposing a friction multiplier in `[0, 1]` via the
  closed-form `exp(-alpha * max(0, -EMA_reward) * log1p(count))`;
- an **absorption module** translating each non-zero
  `avoidance_hint` into a synthetic negative reward fed back into
  the friction tracker;
- a **reward-shaping wrapper** around any `RewardSimulator`
  Protocol implementation that multiplies the realised reward by
  the friction multiplier before returning it to a downstream
  GRPO trainer.

### 2-3. Method and CRM claims
Mirror of Claim 1.

### Dependent claims
- 4. The system of Claim 1 wherein the Critic module's reflection
  string identifies, by reward-correlation gap analysis, the worst
  feature names contributing to the trajectory's punishment.
- 5. The system of Claim 1 wherein the friction tracker resets a
  named `(state_key, action)` entry on operator demand and
  preserves all other entries.

### Implementation pointers (dev branch)
- `brain_engine/cognition_loops/critic.py`
- `brain_engine/cognition_loops/friction.py`

---

## Honest scope and disclaimers

1. **Draft status.**  These claims are engineering-prepared drafts,
   not attorney-finalised filings.  Outside counsel should review
   for §101 patent-eligibility, §102 / §103 prior-art carving, and
   formal claim-language compliance before any USPTO submission.
2. **Implementation-grounded.**  Every claim limitation maps to a
   named module in the dev branch.  Reviewing counsel can run
   `pytest` against the dev branch to confirm reduction-to-practice.
3. **Pre-emption.**  Claims recite specific algorithms (Wilson,
   split-conformal correction `ceil((n+1)(1-alpha))/n`, HMAC-SHA256,
   `exp(-alpha * |EMA⁻| * log1p(n))`) and named field schemas
   (`valid_from`, `valid_to`, `invalid_at`, `deactivated_at`,
   `last_seen_at`) rather than abstract ideas, to reduce §101
   rejection risk.
4. **External-blocker honesty.**  Claim 5 of Candidate 2 (RSSM with
   learned latent) and Candidate 6 dependent claims about
   group-relative policy optimisation on imagined-rollout rewards
   describe **planned embodiments** the production training
   pipeline does not yet exercise (DreamerV3 training data not
   available; production GRPO rollout integration pending).  Filing
   counsel should decide whether to keep these as dependent claims
   or carve them into a continuation-in-part filed once the
   reduction-to-practice lands.

---

## Reviewer checklist

- [ ] Outside counsel §101 / §102 / §103 review.
- [ ] Inventor declarations.
- [ ] Assignment to Cendra / Bookly.
- [ ] Drawings (architecture block diagrams for each candidate;
      module map in this repository → figure mapping).
- [ ] Provisional vs non-provisional decision.
- [ ] PCT route decision.
