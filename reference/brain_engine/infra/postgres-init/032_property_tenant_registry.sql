-- ---------------------------------------------------------
-- Phase 3 — Property → Tenant Registry (auto-resolve).
--
-- Persists the ``(customer_id, org_id, provider_type)`` triplet
-- per ``property_channel_id`` so the brain engine can resolve
-- the correct tenant for any incoming request that carries
-- only a property identifier (Sandbox UI auto-resolve, AG-UI
-- handshake, async webhook fan-out).
--
-- Schema notes:
--   * ``property_channel_id`` is the short Cendra channel id
--     (e.g. "598808") that the UI passes through everywhere —
--     not the unified-data UUID.
--   * ``org_id`` is nullable because the Cendra workspace UUID
--     is unknown for legacy properties bootstrapped before the
--     org plumbing landed (early 2026).  Resolver treats NULL
--     as "drop optional GraphQL filter" — same semantics as the
--     Phase 1 bootstrap override blank-string contract.
--   * ``source`` records HOW the row was populated so operators
--     can audit registry quality:
--       - ``bootstrap`` — written by ``/bootstrap/property/{id}``
--         after a successful fetch.
--       - ``sync``      — nightly cron that refreshes against
--         the unified-data ``/properties?customerId=*`` query.
--       - ``lazy``      — request-time fallback: middleware
--         could not find the property in registry, probed the
--         GraphQL gateway, found a match, upserted before the
--         downstream pipeline ran.
--       - ``manual``    — operator backfill, e.g. one-shot
--         scan of ``decision_cases`` to seed the table from
--         existing tenant-tagged history.
--
-- Indexes:
--   * PK on ``property_channel_id`` for the hot lookup path
--     (every request touches it).
--   * ``customer_id`` index for the nightly sync cron that
--     iterates ``DISTINCT customer_id`` rows.
--   * ``provider_type`` index for adapter-specific operational
--     queries ("how many Lodgify properties do we know?").
--   * ``updated_at`` index for stale-row alerting (rows older
--     than N days are candidates for a re-sync).
--
-- Idempotency:
--   * ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT
--     EXISTS`` so the migration is safe to apply twice.
--   * ``infra/postgres-init/`` runs only on first Postgres pod
--     boot (see ``project_cd_migration_gap`` memory) — for
--     existing dev / prod databases this DDL must be applied
--     manually:
--
--       psql "$DATABASE_URL" \
--            -f infra/postgres-init/032_property_tenant_registry.sql
-- ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS property_tenant_registry (
    property_channel_id TEXT PRIMARY KEY,
    customer_id         TEXT NOT NULL,
    org_id              TEXT,
    provider_type       TEXT NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source              TEXT NOT NULL DEFAULT 'bootstrap'
        CHECK (source IN ('bootstrap', 'sync', 'lazy', 'manual'))
);

CREATE INDEX IF NOT EXISTS idx_ptr_customer
    ON property_tenant_registry (customer_id);

CREATE INDEX IF NOT EXISTS idx_ptr_provider
    ON property_tenant_registry (provider_type);

CREATE INDEX IF NOT EXISTS idx_ptr_updated_at
    ON property_tenant_registry (updated_at);

COMMENT ON TABLE property_tenant_registry IS
    'Phase 3 — auto-resolve tenant for any property_id. '
    'Sandbox UI / live conversation paths look up tenant '
    'here when the request body does not carry it.';

COMMENT ON COLUMN property_tenant_registry.source IS
    'How this row was populated: bootstrap (per-property '
    'bootstrap hook), sync (nightly cron), lazy (request-time '
    'fallback), manual (operator backfill).';
