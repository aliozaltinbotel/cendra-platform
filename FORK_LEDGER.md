# FORK_LEDGER.md — cendra-platform

The single source of truth for how this fork diverges from upstream `langgenius/dify`. If a diff from upstream is not explainable by this file plus the additive paths, the build fails (`make fork-drift`).

## Pin

| Field | Value |
|---|---|
| Upstream | https://github.com/langgenius/dify |
| Base SHA (`.fork-base-sha`) | `a83118c0f4377bcd49929f434178ae6a95ea7a0f` |
| Upstream version at fork | v1.14.2 (api/pyproject.toml) |
| graphon pin | `0.4.0` |
| Fork date | 2026-06-10 |
| Brain Engine reference | branch `dev` @ `a761e29d345d7d076e141dfe301027c47344f33f` → `reference/brain_engine/` (see PROVENANCE.md) |

## Additive paths (never count as drift)

`api/core/brain/**` · `api/models/brain_*.py` · `api/migrations/versions/<cendra ids>` · `api/services/brain_*.py` · `api/controllers/console/brain/**` · `api/controllers/service_api/brain/**` · `api/tasks/brain_*.py` · `api/tests/unit_tests/brain/**` · `web/**/brain/**` · `packs/**` · `reference/**` · `scripts/check_fork_drift.sh` · `FORK_LEDGER.md` · `PORTING_MAP.md` · `CLAUDE.md` · `docs/upstream-CLAUDE.md` · `.fork-base-sha`

## Runtime touchpoints (T-entries) — max 8

| ID | File | Marker count | Reason | PR | Last rebase verified |
|----|------|-------------|--------|----|---------------------|
| T1 | `api/core/workflow/node_runtime.py` | 0 (not yet landed) | Gate chain around tool dispatch | — | — |
| T2 | `api/core/workflow/node_factory.py` | 0 | Register wrapped runtimes | — | — |
| T3 | `api/core/workflow/nodes/agent_v2/…` | 0 | Gate + memory injection in agent loop | — | — |
| T4 | `api/core/moderation/…` (target: zero-edit Extensible module) | 0 | Art. 50 disclosure + PII | — | — |
| T5 | `api/extensions/ext_celery.py` | 0 | Additive beat_schedule entries | — | — |
| T6 | `api/core/rag/retrieval/…` (or zero-edit loopback) | 0 | Brain memory as retrieval source | — | — |
| T7 | `api/core/callback_handler/…` | 0 | DecisionCase capture on run events | — | — |
| T8 | `docker/**` env/compose | 0 | Qdrant sparse vectors, brain env | — | — |

Every hooked block in code: `# CENDRA-HOOK(Tn): <reason>`. Rule: `git grep -c "CENDRA-HOOK"` totals must match this table.

## Config edits (C-entries) — tooling/infra only, no runtime behavior

| ID | File | What | PR |
|----|------|------|----|
| C1 | `api/.ruff.toml` | Defensive `reference` entry in `exclude`. Note: ruff/pyrefly/mypy/pytest/import-linter all run scoped to `api/`, and `reference/` sits at repo root — outside every tool's tree by construction, so no other exclude edits were needed. Brain deps in `api/pyproject.toml` are logged here as they are added. | Batch 1 |
| C2 | `.dockerignore` (repo root) | Exclude `reference/` (`build-web` uses repo-root context; `build-api` context is `./api`, which never contains `reference/`) | Batch 1 |
| C3 | `api/.importlinter` | Contract `brain-kernel-isolation`: `core.brain ↛ core.workflow / core.app / core.agent` (`reference/` is not a root package, so no ignore needed) | Batch 1 |
| C4 | `Makefile` | Add `fork-drift` target (additive) | Batch 1 |
| C5 | `CLAUDE.md` | Replaced with Cendra version; upstream copy moved to `docs/upstream-CLAUDE.md` | Batch 1 |

## Rebase log

| Date | Upstream version | New base SHA | Conflicts in hooked files | Hooks re-verified (count) | Notes |
|------|-----------------|--------------|---------------------------|---------------------------|-------|
| — | — | — | — | — | — |

## Drift check — `scripts/check_fork_drift.sh`

```bash
#!/usr/bin/env bash
# Fails if any upstream-tracked file is modified without a T/C registration.
set -euo pipefail
git fetch upstream --quiet
BASE="$(git merge-base upstream/main HEAD)"

ALLOW='^(api/core/brain/|api/models/brain_|api/services/brain_|api/tasks/brain_'
ALLOW+='|api/controllers/console/brain/|api/controllers/service_api/brain/'
ALLOW+='|api/tests/unit_tests/brain/|api/migrations/versions/|web/.*/brain/'
ALLOW+='|packs/|reference/|scripts/check_fork_drift.sh'
ALLOW+='|FORK_LEDGER.md|PORTING_MAP.md|CLAUDE.md|docs/upstream-CLAUDE.md|\.fork-base-sha)'

REGISTERED='^(api/core/workflow/node_runtime.py|api/core/workflow/node_factory.py'
REGISTERED+='|api/core/workflow/nodes/agent_v2/|api/core/moderation/'
REGISTERED+='|api/extensions/ext_celery.py|api/core/rag/retrieval/'
REGISTERED+='|api/core/callback_handler/|docker/'
REGISTERED+='|api/pyproject.toml|api/uv.lock|api/.ruff.toml|.dockerignore|Makefile|api/.importlinter)'   # keep in sync with T/C tables

VIOLATIONS=$(git diff --name-only "$BASE"..HEAD \
  | grep -Ev "$ALLOW" \
  | grep -Ev "$REGISTERED" || true)

if [[ -n "$VIOLATIONS" ]]; then
  echo "FORK DRIFT — unregistered upstream modifications:" >&2
  echo "$VIOLATIONS" >&2
  exit 1
fi

LEDGER_HOOKS=$(grep -cE '^\| T[0-9]' FORK_LEDGER.md || true)
echo "fork-drift: clean (allowlist ok, ${LEDGER_HOOKS} touchpoint rows registered)"
```

Notes: the migrations allowlist line is broad — review brain migrations by Alembic id prefix in PR review (drift script can't distinguish authorship of new files there). Keep `REGISTERED` in exact sync with the T/C tables; PR review enforces that `CENDRA-HOOK` markers exist inside any registered file's diff. Deviations from the kit template (all Batch 1): `.fork-base-sha` added to `ALLOW` (the pin file itself isn't upstream); `api/uv.lock` and `api/.ruff.toml` added to `REGISTERED` (the lockfile moves with C1 dependency additions; the ruff config carries the C1 defensive exclude).
