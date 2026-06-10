# Hospitality pack

Vertical pack #1 for cendra-platform: hospitality-specific vocabulary and
defaults consumed by the vertical-neutral kernel (`api/core/brain/`).
The kernel never imports from `packs/` — pack content is loaded as data
(loader infrastructure lands in Batch 6, see `PORTING_MAP.md`).

Contents so far:

- `tier_defaults.yaml` — autonomy tier ceilings per action kind, feeding
  `core.brain.certificates.TierPolicy`. Extracted from the reference's
  `certificates/policy.py` defaults + `cards/action_kinds.py` vocabulary
  when the certificates module was genericised in Batch 1.

Planned (Batch 6): 482-scenario foundation content, DSL vocabulary,
workflow templates, WorkflowKind registry seeds.
