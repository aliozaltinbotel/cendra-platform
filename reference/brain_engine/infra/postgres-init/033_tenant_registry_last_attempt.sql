-- ---------------------------------------------------------
-- Phase 4 reliability — record when Phase 4 auto-bootstrap
-- last attempted to prime a property, so the trigger can
-- skip re-firing while a cooldown window is in effect.
--
-- Why we need this:
--   The original Phase 4 dedup signal was "PropertyProfile
--   exists in property_profiles".  That works for the happy
--   path (bootstrap pulls real data, harvester writes a
--   profile, future requests skip), but fails for the edge
--   case where the harvester cannot build a profile
--   (unified-data GraphQL returns no detail for the
--   ``(customer_id, provider_type, channel_entity_id)``
--   triplet).  Without a profile the trigger re-fires on
--   every request — eternal background bootstrap loop
--   that wastes GraphQL traffic.
--
-- Fix:
--   Persist ``last_auto_attempted_at`` on the
--   ``property_tenant_registry`` row whenever the trigger
--   dispatches.  The trigger consults this timestamp on the
--   next request and skips while inside the cooldown window
--   (default 1 hour, tunable via the
--   ``AUTO_BOOTSTRAP_COOLDOWN_HOURS`` env var).
--
--   Restart-safe (the column survives pod restarts) and
--   multi-pod-safe (every replica reads the same row).
--
-- Idempotent: ``ADD COLUMN IF NOT EXISTS`` keeps re-apply
-- safe.  ``infra/postgres-init/`` only runs on first pod
-- boot — for existing dev / prod databases apply manually:
--
--     psql "$DATABASE_URL" \
--          -f infra/postgres-init/033_tenant_registry_last_attempt.sql
-- ---------------------------------------------------------

ALTER TABLE property_tenant_registry
    ADD COLUMN IF NOT EXISTS last_auto_attempted_at TIMESTAMPTZ;

COMMENT ON COLUMN property_tenant_registry.last_auto_attempted_at IS
    'Timestamp of the most recent Phase 4 auto-bootstrap fire for '
    'this property.  Updated after the background bootstrap_fast '
    'task completes (success or failure).  The trigger skips re-'
    'firing while ``now() - last_auto_attempted_at`` is less than '
    'the configured cooldown window (default 1 hour).';
