-- ---------------------------------------------------------
-- Stage 1 — Property bootstrap state SSoT.
--
-- Single source of truth for "is this property warmed up?".
-- Replaces the three scattered signals the legacy auto-trigger
-- relied on:
--
--   * ``PropertyProfile exists in property_profiles``
--     (false negatives when harvester cannot build a profile)
--   * ``last_auto_attempted_at`` on
--     ``property_tenant_registry`` (cooldown only — does not
--     answer "what stage is this property in right now?")
--   * in-process ``_pending: set[str]`` on
--     :class:`AutoBootstrapTrigger` (lost on pod restart,
--     invisible across replicas)
--
-- A single row per ``property_channel_id`` records every
-- transition between ``cold → queued → warming → primed →
-- stale → failed`` so the bootstrap-intent function
-- (PR-B) and the future Service Bus worker (Stage 2) both
-- read and write the same record.
--
-- Schema notes:
--   * ``property_channel_id`` mirrors the PK of
--     ``property_tenant_registry`` (migration 032) so a
--     downstream FK is trivial to add when both tables have
--     fully landed.  No FK in this migration yet — keeping
--     the table standalone lets operators backfill state
--     without waiting on registry coverage.
--   * ``status`` is ``TEXT NOT NULL`` with a ``CHECK``
--     constraint — same pattern as migration 032's
--     ``source`` column.  PostgreSQL ENUM types are
--     deliberately avoided: their migrations are awkward
--     (``ALTER TYPE`` cannot drop labels in a single DDL)
--     and the brain engine never compares status with
--     ordering semantics.
--   * ``current_job_id`` and ``intent_dedup_key`` are
--     nullable because they are only meaningful while a
--     row is in ``queued`` / ``warming``.  Worker drains
--     them on terminal transitions (``primed`` /
--     ``failed``) so a stale ``job_id`` cannot leak into
--     the dashboard after a successful run.
--   * ``window_days`` records the actual archive window the
--     last warming consumed (UI requests can ask for a
--     short window; nightly refresh asks for the full 2y).
--     Operators read this to decide whether a ``primed``
--     row needs an extended re-warm.
--   * ``last_data_event_at`` is the timestamp of the most
--     recent OTA event we have ingested for this property
--     (booking, message, etc.).  Stage 3 nightly
--     consolidator uses this to detect ``primed → stale``.
--   * ``retry_count`` is bumped by the worker on transient
--     failures so the bootstrap-intent function can
--     dead-letter after N attempts (Stage 2 concern).
--
-- Indexes:
--   * PK on ``property_channel_id`` for the hot lookup
--     path (every Sandbox UI message touches it).
--   * ``status`` index for the dashboard query "how many
--     properties are warming / primed / failed right now".
--   * ``customer_id`` index for the nightly consolidator
--     that iterates per-customer.
--
-- Idempotency:
--   * ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF
--     NOT EXISTS`` so the migration is safe to apply twice.
--   * ``infra/postgres-init/`` only runs on first Postgres
--     pod boot (see ``project_cd_migration_gap`` memory) —
--     for existing dev / prod databases apply manually:
--
--       psql "$DATABASE_URL" \
--            -f infra/postgres-init/034_property_state.sql
-- ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS property_state (
    property_channel_id  TEXT PRIMARY KEY,
    customer_id          TEXT NOT NULL,
    org_id               TEXT,
    provider_type        TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'cold'
        CHECK (status IN (
            'cold', 'queued', 'warming',
            'primed', 'stale', 'failed'
        )),
    current_job_id       TEXT,
    intent_dedup_key     TEXT,
    conversations_loaded INTEGER NOT NULL DEFAULT 0
        CHECK (conversations_loaded >= 0),
    cases_extracted      INTEGER NOT NULL DEFAULT 0
        CHECK (cases_extracted >= 0),
    rules_emitted        INTEGER NOT NULL DEFAULT 0
        CHECK (rules_emitted >= 0),
    profile_built        BOOLEAN NOT NULL DEFAULT FALSE,
    window_days          INTEGER
        CHECK (window_days IS NULL OR window_days > 0),
    first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_bootstrap_at    TIMESTAMPTZ,
    last_data_event_at   TIMESTAMPTZ,
    last_error           TEXT,
    retry_count          INTEGER NOT NULL DEFAULT 0
        CHECK (retry_count >= 0),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ps_status
    ON property_state (status);

CREATE INDEX IF NOT EXISTS idx_ps_customer
    ON property_state (customer_id);

COMMENT ON TABLE property_state IS
    'Stage 1 — single source of truth for property bootstrap '
    'lifecycle (cold → queued → warming → primed → stale → '
    'failed).  Replaces ad-hoc dedup signals (profile presence, '
    'last_auto_attempted_at, in-proc set).';

COMMENT ON COLUMN property_state.status IS
    'Bootstrap lifecycle stage.  Allowed values: cold, queued, '
    'warming, primed, stale, failed.  Enforced by CHECK '
    'constraint — invalid values surface as IntegrityError at '
    'transition time rather than as silent corruption.';

COMMENT ON COLUMN property_state.current_job_id IS
    'Bootstrap job currently owning this row.  NULL when the '
    'row is in a terminal state (primed / failed / cold / '
    'stale) so a stale job_id cannot leak into observability.';

COMMENT ON COLUMN property_state.intent_dedup_key IS
    'sha256(property_channel_id, window_days, day_bucket) — '
    'Service Bus dedup MessageId.  Populated on queued '
    'transition (PR-B), consumed by Stage 2 worker.';

COMMENT ON COLUMN property_state.window_days IS
    'Actual archive window the last warming consumed.  NULL '
    'until the first primed transition.  Operators compare '
    'against the requested window to spot truncated runs.';

COMMENT ON COLUMN property_state.last_data_event_at IS
    'Timestamp of the most recent OTA event ingested for this '
    'property.  Stage 3 nightly consolidator reads this to '
    'flip primed → stale when data drift exceeds the TTL.';

COMMENT ON COLUMN property_state.retry_count IS
    'Monotonic count of transient failures since the last '
    'primed transition.  Worker dead-letters when this '
    'exceeds the configured retry budget (Stage 2 concern).';

-- ---------------------------------------------------------
-- Grant the application login role access to the table.
--
-- Init scripts run as the bootstrap superuser, so a freshly
-- created table is owned by ``postgres`` and the application
-- role (``brain_engine``) has NO privileges by default.  That
-- gap left ``property_state`` unwritable on dev: every write
-- raised InsufficientPrivilegeError, surfaced as the recurring
-- ``auto_bootstrap_trigger_failed`` warning and a stuck Sandbox
-- response.  Mirror the ALL grant that
-- ``property_tenant_registry`` (migration 032) carries so the
-- SSoT is writable the instant the table exists.
--
-- Guarded + idempotent: a no-op on clusters where the role has
-- not been created yet, re-runnable without error.
-- ---------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_engine') THEN
        EXECUTE 'GRANT ALL ON TABLE property_state TO brain_engine';
    END IF;
END
$$;
