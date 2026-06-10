# core.brain — the Cendra decision/governance/learning kernel

Ported module-by-module from the Brain Engine (`reference/brain_engine/`, see
`PORTING_MAP.md` at the repo root for what lands where and when).

## The kernel rule

**`core.brain` imports nothing from `core.workflow`, `core.app`, or `core.agent`.**
Adapters at the registered touchpoints (T1–T8 in `FORK_LEDGER.md`) import brain —
never the reverse. Enforced by an import-linter contract (`api/.importlinter`,
"Cendra brain kernel isolation"). This keeps the kernel extractable and
vertical-neutral.

No vertical (e.g. hospitality) semantics belong here either: workflow kinds,
scenario content, DSL vocabularies and channel specifics live in `packs/` data
or tenant-scoped DB rows.

## Layout

- `abstention/` — calibrated confidence + split-conformal abstention gate
- `certificates/` — decision certificates: tier, issuance, verification
- `policy/` — owner policy DSL, compiler, Z3 checks (Batch 5)
- `epistemic/` — observation/belief models + promotion
- `patterns/` — Wilson bounds, rule stores, mining (Batches 1–2)
- `autonomy/` — trust meter, autonomy engine, approval routing (Batch 2+)
- `memory/` — working/episodic/semantic tiers, hybrid search (Batch 3)
- `cognition/` — ACE×Memory-R1 cognition loops, critic, friction (Batch 1/6)
- `compliance/` — EU AI Act Art. 12/50, PII, retention (Batch 5)
- `planning/`, `twin/` — HTN/behavior trees, property twin (deferred, Batch 7)
- `gates.py` — gate-chain composition consumed by the touchpoint adapters (Batch 4)
