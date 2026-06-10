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
