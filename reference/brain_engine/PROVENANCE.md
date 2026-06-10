# PROVENANCE — reference/brain_engine/

| Field | Value |
|---|---|
| Source remote | `https://dev.azure.com/book-ly/bookly/_git/brainengine` |
| Branch | `dev` |
| Commit SHA | `a761e29d345d7d076e141dfe301027c47344f33f` |
| Copy date | 2026-06-10 |
| Excluded from copy | `.git/`, venvs, `node_modules`, `.pytest_cache`, `__pycache__`, `.mypy_cache`, `.ruff_cache` |

Note: the repo also carries an `OpsEngine` branch (`23ba65518f8ce108d5ada4d950457143e84bcdb4`); this copy was taken from `dev` per the Phase 1 / Batch 1 session instruction.

**Redaction notice:** `deploy/brain-engine-dev.yaml` in the source repo embeds live credentials (Redis access key, Auth0 M2M client secret, Postgres password in `DATABASE_URL`, Azure Service Bus connection string, Azure OpenAI API key). Those five values — and only those values — were replaced with `"REDACTED-AT-VENDOR"` before publication; GitHub push protection blocks the originals, and they must never enter this repo. This is the single sanctioned deviation from byte-faithful vendoring. Deploy manifests are retired content (see PORTING_MAP.md) and are never a porting source. The originals remain in the Azure DevOps repo — **they should be rotated**, since that repo's history exposes them to everyone with repo access.

Read-only porting reference — never imported, never edited; fixes go into `api/core/brain/`.
