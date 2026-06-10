-- Bi-temporal soft-invalidate columns on pattern_rules.
--
-- Adds two timestamps that distinguish the *application time* a rule
-- stopped being true (the world changed: PM shifted policy) from the
-- *transaction time* Brain Engine learned about it.  The pair is the
-- structured equivalent of Zep / Graphiti's `invalid_at` / `expired_at`
-- (arXiv 2501.13956 §3.2) — port adapted to a deterministic identity
-- tuple so no LLM is required for conflict detection.
--
-- Why a separate column (instead of reusing ``valid_to``):
--   * ``valid_to`` is a *scheduled* expiration the miner sets when a
--     rule has intrinsic shelf-life (seasonal policy, time-bounded
--     promotion).  It encodes the rule's intent.
--   * ``invalid_at`` is *evidence-driven*: another rule with newer
--     evidence and a different action_type supplanted this one at a
--     specific real-world moment.
--
-- Why two columns (T vs T'):
--   * Out-of-order ingestion: a re-bootstrap may surface old PM
--     decisions that supersede an existing rule months ago.  The world
--     changed back then (``invalid_at``); the registry only learned
--     today (``deactivated_at``).
--   * Audit: enables point-in-time queries — "what rule did Brain
--     Engine apply for THIS reservation, given the registry state on
--     THIS date?"
--
-- Backfill: existing rows keep both columns NULL.  Deactivated rows
-- (``active = false``) get ``deactivated_at = updated_at`` so audit
-- queries still locate the moment of deactivation.

BEGIN;

ALTER TABLE pattern_rules
    ADD COLUMN IF NOT EXISTS invalid_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ;

UPDATE pattern_rules
SET deactivated_at = updated_at
WHERE active = false
  AND deactivated_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_pattern_rules_invalid_at
    ON pattern_rules (invalid_at);

CREATE INDEX IF NOT EXISTS idx_pattern_rules_deactivated_at
    ON pattern_rules (deactivated_at);

COMMIT;
