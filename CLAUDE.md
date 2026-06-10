# CLAUDE.md — cendra-platform

This repo is **cendra-platform**: a fork of `langgenius/dify` (pinned base SHA in `.fork-base-sha`, graphon version in `FORK_LEDGER.md`) into which we port the moats of our in-house Brain Engine. End state: one codebase, one deployment — Dify's orchestration chassis + Cendra's decision/governance/learning kernel — packaged as a vertical-agnostic AI operations platform (hospitality is vertical pack #1).

Companion docs (read with this one): `PORTING_MAP.md` (what goes where, batch order, status) and `FORK_LEDGER.md` (pinned upstream, registered touchpoints, drift check). Upstream Dify's original dev guide is preserved at `docs/upstream-CLAUDE.md` — its build/test knowledge still applies.

---

## Golden rules (every session, every commit)

1. **Additive-first.** New code lives in Cendra-owned paths (below). Editing an upstream file is exceptional and follows the touchpoint protocol.
2. **Touchpoint protocol.** Max **8 runtime touchpoints** (T1–T8, enumerated in `FORK_LEDGER.md`). Every edited upstream line block carries `# CENDRA-HOOK(Tn): <one-line reason>` and a ledger entry. Tooling/config edits are **C-entries** in the ledger. `make fork-drift` must pass before any merge to `cendra/main`.
3. **Kernel isolation.** `api/core/brain/` imports **nothing** from `core.workflow`, `core.app`, `core.agent`, or `controllers` — enforced by an import-linter contract. Adapters at the touchpoints import brain; never the reverse. This keeps the kernel extractable and vertical-neutral.
4. **No hospitality semantics in the kernel.** Workflow kinds, scenario content, DSL vocabularies, channel specifics → `packs/` data or tenant-scoped DB rows, never kernel code.
5. **Tests travel with code.** A module is "ported" only when its test file is ported and green in `api/tests/unit_tests/brain/`. The Brain Engine suite (~1,406 tests) is our parity instrument with production.
6. **License guardrail.** Never remove/alter Dify branding, logos, or copyright in `web/` or anywhere else; never weaken license files. Pending the LangGenius commercial agreement, branding work is frozen.
7. **`reference/brain_engine/` is read-only.** It is the porting source (branch `OpsEngine`, provenance in `reference/brain_engine/PROVENANCE.md`), excluded from lint/type/test/docker. Never import it; never fix bugs there — fix forward in `api/core/brain/` and note it in the commit.

## Directory contract (Cendra-owned paths)

```
api/core/brain/                  # the kernel: abstention/ certificates/ policy/ epistemic/
                                 # patterns/ autonomy/ memory/ cognition/ compliance/
                                 # planning/ twin/  + gates.py (gate-chain composition)
api/models/brain_*.py            # SQLAlchemy models (tenant-scoped via tenant_id, Dify convention)
api/migrations/versions/*        # Alembic migrations for brain_* tables (additive only)
api/services/brain_*.py          # service layer
api/controllers/console/brain/   # console API (TrustMeter, policy, audit)
api/controllers/service_api/brain/  # public API
api/tasks/brain_*.py             # Celery tasks (consolidation, mining, autonomy eval, friction decay)
api/tests/unit_tests/brain/      # mirrors api/core/brain/
web/**/brain/                    # console UI modules (Batch 6+; API-first before screens)
packs/                           # vertical packs: scenarios, DSL vocab, workflow templates, tier defaults
reference/brain_engine/          # read-only porting source
scripts/check_fork_drift.sh      # drift check (wired to `make fork-drift`)
```

## The 8 runtime touchpoints (only places upstream code may change)

| ID | File | Purpose |
|----|------|---------|
| T1 | `api/core/workflow/node_runtime.py` | Wrap `DifyToolNodeRuntime` (+ agent tool dispatch) with the gate chain from `core/brain/gates.py` |
| T2 | `api/core/workflow/node_factory.py` | Register wrapped runtimes / Cendra node variants |
| T3 | `api/core/workflow/nodes/agent_v2/` adapter | Inject gates + brain memory context into the agent loop |
| T4 | `api/core/moderation/` (or zero-edit `Extensible` module) | Art. 50 disclosure + PII redaction on chat apps |
| T5 | `api/extensions/ext_celery.py` | Additive `beat_schedule` entries for brain tasks |
| T6 | `api/core/rag/retrieval/` | Register brain memory as a retrieval source for knowledge nodes |
| T7 | workflow/agent run completion events (`api/core/callback_handler/` area) | DecisionCase capture |
| T8 | `docker/` compose + env | Qdrant sparse-vector config, brain env vars |

Touchpoints land in **Batch 4–5 only** (see `PORTING_MAP.md`). If a need arises that doesn't fit T1–T8, stop and discuss — do not invent T9 unilaterally.

## Porting checklist (apply to every module from `reference/`)

1. **Async → sync.** `async def` → `def`; remove `await`; `asyncpg`/async-SQLAlchemy stores → Dify's sync SQLAlchemy 2.0 select-style sessions (see `api/models/` and `api/services/` for idioms). Never run `asyncio.run()` inside Flask request paths or Celery tasks. Pure-computation modules usually need zero changes.
2. **Python 3.12.** The reference targets 3.11; fix any 3.12 deprecation warnings forward (upstream runs strict tooling — keep it clean).
3. **Dependencies.** Add via `uv add --project api <pkg>`; justify in the commit message; check license (Apache/BSD/MIT only without discussion). Expected over the project: `mapie`, `lark`, `z3-solver`, `fastembed`, `sentence-transformers` (weigh image size — flag-gate heavy ones like the reference does), `scikit-learn`. Drop reference deps we replace: `litellm`→`model_runtime`, `fastapi/uvicorn`→Flask, `elevenlabs`/channel SDKs→plugins.
4. **Config & logging.** Env config via `configs.dify_config` patterns (not BaseSettings copies); logging via Dify's logging setup (structlog calls → stdlib logger with structured extras, matching surrounding code).
5. **Pydantic v2 stays.** Dify uses it; keep the reference models, trim unused fields.
6. **Keep the IP markers.** Docstrings with `Moat #N` and arXiv citations stay verbatim — they map code to `patents/PATENT_CLAIMS.md` in the original repo.
7. **Stores follow Dify, not the reference.** The reference's Protocol + InMemory + Postgres pattern is kept *conceptually* (Protocol + InMemory for tests), but the persistent impl is Dify SQLAlchemy models + Alembic migration, tenant-scoped.

## Commands (verified for this repo)

```bash
make dev-setup                 # one-time: docker deps, web, api (uv-based)
./dev/start-api | start-worker | start-beat | start-web
make lint                      # ruff + lint-imports + dotenv-linter (api)
make type-check                # pyrefly/ty
make test                      # unit tests
uv run --project api pytest api/tests/unit_tests/brain/ -v     # brain subtree
cd api && uv run flask db migrate -m "brain: <what>" && uv run flask db upgrade
make fork-drift                # zero unregistered upstream edits, or fail
```

## Branch & commit conventions

- `main` = clean upstream mirror (rebases only). `cendra/main` = integration branch. Work branches: `cendra/phase<N>-<topic>`.
- Commits: `port(brain/<module>): …` for ports, `hook(Tn): …` for touchpoints, `chore(fork): …` for scaffolding/config, `pack(hospitality): …` for pack content. One module per port commit, referencing the reference short-SHA.
- Upstream rebase ritual (monthly, dedicated session): fetch upstream → rebase `main` → merge into `cendra/main` → re-verify every `CENDRA-HOOK` survives (`git grep "CENDRA-HOOK"` count must match ledger) → `make fork-drift && make test` → log in the ledger's rebase table.

## Phase gates (current status lives in PORTING_MAP.md)

- **P1 Kernel** (Batches 1–2): pure kernels + stores/migrations. Fork still behaves identically to upstream at runtime.
- **P2 Wiring** (Batches 3–4): memory tiers + touchpoints T1–T3/T6–T8 live; one workspace runs `inquiry_reply` in OBSERVE; ledger filling.
- **P3 Governed autonomy** (Batch 5): policy/Z3, compliance, beat jobs, service_api; SEMI_AUTO via Human Input.
- **P4 Learning + console + packs** (Batch 6): ACE/Memory-R1/sleep wired, TrustMeter UI, hospitality pack extracted; old Brain Engine frozen → cutover → archived.

## When unsure

Stop and ask in these cases: a kernel module seems to need a workflow/app import; a touchpoint outside T1–T8 looks necessary; an upstream rebase conflicts inside a hooked file; a dependency has a non-permissive license; anything touching branding or the LICENSE. Improvised architecture is more expensive than a question.
