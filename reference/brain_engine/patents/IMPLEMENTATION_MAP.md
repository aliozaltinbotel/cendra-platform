# Implementation Map — Claim Limitation → Dev-Branch Module

This document is the **reviewer's index** that lets outside counsel
verify every claim limitation in `PATENT_CLAIMS.md` against the
working code on the `dev` branch.  Each row maps one specific claim
limitation to the file + symbol that reduces it to practice and the
test that exercises it.

The map below is generated against `dev` at the time of writing.
Re-run `pytest tests/` on `dev` HEAD to confirm green status before
counsel review.

---

## Candidate 1 — Bi-temporal Wilson + Conformal Abstention

| Claim limitation | Module | Symbol | Test |
| --- | --- | --- | --- |
| Bi-temporal rule store (`valid_from`, …, `last_seen_at`) | `brain_engine/patterns/postgres_rule_store.py` | `PatternRule` + `PostgresRuleStore` | `tests/patterns/test_postgres_rule_store.py` |
| Temporal filter at decision time | `brain_engine/patterns/temporal_resolver.py` | `resolve_active_rules` | `tests/patterns/test_temporal_resolver.py` |
| Wilson lower bound | `brain_engine/abstention/calibrator.py` | `ConformalCalibrator.wilson_lower_bound` | `tests/abstention/test_calibrator.py` |
| Split-conformal quantile correction | `brain_engine/abstention/split_conformal.py` | `empirical_conformal_quantile` | `tests/abstention/test_split_conformal.py` |
| Library-backed (MAPIE LAC) variant | `brain_engine/abstention/mapie_calibrator.py` | `MapieSplitConformalCalibrator` | `tests/abstention/test_mapie_calibrator.py` |
| 3-valued abstention gate | `brain_engine/abstention/gate.py` | `AbstentionGate.decide` | `tests/abstention/test_gate.py` |
| Conformal abstention gate variant | `brain_engine/abstention/split_conformal.py` | `ConformalAbstainGate.decide` | `tests/abstention/test_split_conformal.py` |
| Audit persistence | `brain_engine/abstention/models.py` | `AbstentionDecision` | covered by gate tests |

## Candidate 2 — Property Twin Brain

| Claim limitation | Module | Symbol | Test |
| --- | --- | --- | --- |
| Per-property fused state | `brain_engine/property_twin/twin.py` | `PropertyTwin` | `tests/property_twin/test_twin.py` |
| World-model module (linear baseline) | `brain_engine/property_twin/linear_world_model.py` | `LinearWorldModel` | `tests/property_twin/test_linear_world_model.py` |
| World-model module (Protocol for v1.0 RSSM) | `brain_engine/property_twin/protocols.py` | `WorldModel` | covered by twin tests |
| Imagined-rollout reward → Memory-R1 trainer | `brain_engine/cognition_loops/grpo.py` | `GRPOTrainer` | `tests/cognition_loops/test_grpo.py` |

## Candidate 3 — Owner-policy DSL + SMT verifier

| Claim limitation | Module | Symbol | Test |
| --- | --- | --- | --- |
| Lark DSL grammar | `brain_engine/owner_policy/grammar.lark` | (file) | `tests/owner_policy/test_parser.py` |
| Parser | `brain_engine/owner_policy/parser.py` | `parse_policy` | `tests/owner_policy/test_parser.py` |
| AST | `brain_engine/owner_policy/ast.py` | `PolicyRule` / `Constraint` | `tests/owner_policy/test_ast.py` |
| Compiler | `brain_engine/owner_policy/compiler.py` | `compile_rules` | `tests/owner_policy/test_compiler.py` |
| Z3 SMT verifier | `brain_engine/owner_policy/z3_compiler.py` | `Z3Verifier` | `tests/owner_policy/test_z3_compiler.py` |
| Runtime registry | `brain_engine/owner_policy/registry.py` | `PolicyRegistry` | `tests/owner_policy/test_registry.py` |

## Candidate 4 — Tiered autonomy + HMAC certificates

| Claim limitation | Module | Symbol | Test |
| --- | --- | --- | --- |
| Action-class registry | `brain_engine/certificates/tier.py` | `ActionTier` | `tests/certificates/test_tier.py` |
| Certificate issuer (HMAC-SHA256) | `brain_engine/certificates/issuer.py` | `CertificateIssuer.issue` | `tests/certificates/test_issuer.py` |
| Certificate value object | `brain_engine/certificates/cert.py` | `AutonomyCertificate` | `tests/certificates/test_cert.py` |
| Runtime verifier | `brain_engine/certificates/verifier.py` | `CertificateVerifier.verify` | `tests/certificates/test_verifier.py` |
| Per-tier policy | `brain_engine/certificates/policy.py` | `TierPolicy` | `tests/certificates/test_policy.py` |
| Runtime gate composition | `brain_engine/autonomy/gate.py` | `AutonomyGate` | `tests/autonomy/test_gate.py` |

## Candidate 5 — Observation/belief schema + promoter

| Claim limitation | Module | Symbol | Test |
| --- | --- | --- | --- |
| Per-tier observation / belief value objects | `brain_engine/epistemic/models.py` | `Observation`, `Belief` | `tests/epistemic/test_models.py` |
| Store | `brain_engine/epistemic/store.py` | `EpistemicStore` | `tests/epistemic/test_store.py` |
| Belief promoter | `brain_engine/epistemic/promotion.py` | `BeliefPromoter.promote` | `tests/epistemic/test_promotion.py` |
| Conformal singleton check | `brain_engine/abstention/split_conformal.py` | `ConformalSet.is_singleton` | covered by `test_split_conformal.py` |

## Candidate 6 — ACE + sleep + Memory-R1 interaction protocol

| Claim limitation | Module | Symbol | Test |
| --- | --- | --- | --- |
| ACE cycle value object | `brain_engine/cognition_loops/models.py` | `AceCycle` | `tests/cognition_loops/test_models.py` |
| Memory-R1 op value object | `brain_engine/cognition_loops/models.py` | `MemoryOp`, `MemoryOpKind` | `tests/cognition_loops/test_models.py` |
| Memory-R1 policy (multinomial logit) | `brain_engine/cognition_loops/policy.py` | `MultinomialLogitPolicy` | `tests/cognition_loops/test_policy.py` |
| Reward-weighted SGD trainer | `brain_engine/cognition_loops/trainer.py` | `SGDTrainer` | `tests/cognition_loops/test_trainer.py` |
| GRPO trainer | `brain_engine/cognition_loops/grpo.py` | `GRPOTrainer` | `tests/cognition_loops/test_grpo.py` |
| Interaction protocol state machine | `brain_engine/cognition_loops/protocol.py` | `InteractionProtocol.resolve` | `tests/cognition_loops/test_protocol.py` |
| Sleep-time consolidation worker | `brain_engine/cognition_loops/sleep.py` | `summarise_decisions`, `ConsolidationReport` | `tests/cognition_loops/test_sleep.py` |

## Candidate 7 — HTN + LATS + R-WoM

| Claim limitation | Module | Symbol | Test |
| --- | --- | --- | --- |
| HTN decomposition into macro-actions | `brain_engine/htn/planner.py` | `HTNPlanner.decompose` | `tests/htn/test_planner.py` |
| LATS-MCTS expansion (UCB1) | `brain_engine/htn/search.py` | `LATSSearch.expand` | `tests/htn/test_search.py` |
| Tree node + value bookkeeping | `brain_engine/htn/tree.py` | `LATSNode` | covered by `test_search.py` |
| Bag-of-name R-WoM value estimator | `brain_engine/htn/rwom.py` | `RWoMValueEstimator` | `tests/htn/test_rwom.py` |
| Embedding-similarity R-WoM | `brain_engine/htn/embedding_rwom.py` | `EmbeddingRWoMValueEstimator` | `tests/htn/test_embedding_rwom.py` |
| ANN corpus (sklearn NearestNeighbors) | `brain_engine/htn/ann_corpus.py` | `AnnCorpus` | `tests/htn/test_ann_corpus.py` |
| Tool-grounded metacognitive monitor | `brain_engine/decision_pipeline/adapter.py` | `DecisionPipelineAdapter` | `tests/decision_pipeline/test_adapter.py` |

## Candidate 8 — Memory-Augmented Reflexion (MAR)

| Claim limitation | Module | Symbol | Test |
| --- | --- | --- | --- |
| Reflexion Critic Protocol | `brain_engine/cognition_loops/critic.py` | `Critic` | `tests/cognition_loops/test_critic.py` |
| Critic trajectory event value object | `brain_engine/cognition_loops/critic.py` | `CriticEvent` | `tests/cognition_loops/test_critic.py` |
| Critique report (reflection + hints) | `brain_engine/cognition_loops/critic.py` | `CritiqueReport` | `tests/cognition_loops/test_critic.py` |
| Heuristic reference Critic | `brain_engine/cognition_loops/critic.py` | `ReflexionCritic` | `tests/cognition_loops/test_critic.py` |
| Friction tracker (EMA + count) | `brain_engine/cognition_loops/friction.py` | `FrictionTracker` | `tests/cognition_loops/test_friction.py` |
| Friction multiplier kernel `exp(-α·\|EMA⁻\|·log1p(n))` | `brain_engine/cognition_loops/friction.py` | `FrictionTracker.friction` | `test_friction_kernel_matches_documented_formula` |
| Critique-absorption module | `brain_engine/cognition_loops/friction.py` | `FrictionTracker.absorb_critique` | `tests/cognition_loops/test_friction.py` |
| Reward-shaping wrapper | `brain_engine/cognition_loops/friction.py` | `FrictionRewardSimulator` | `tests/cognition_loops/test_friction.py` |

---

## Verification protocol for counsel

Run on `dev` HEAD:

```bash
git checkout dev && git pull
pytest tests/ -v --ignore=tests/integration
```

Expected at time of writing: ≥1406 tests pass, zero failures.  Any
non-green result before counsel review is a blocker — the
implementation map and the claim drafts must agree.
