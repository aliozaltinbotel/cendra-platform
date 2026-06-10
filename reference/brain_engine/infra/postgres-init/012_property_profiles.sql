-- ---------------------------------------------------------
-- PROPERTY_PROFILES — durable cache of the onboarding-built
-- ``PropertyProfile`` snapshot, keyed by ``propertyChannelId``.
-- One row per property; ``payload`` carries the full unified
-- static payload (WiFi, parking, amenities, descriptions, …)
-- plus the aggregate KnowledgeSection / ReviewAggregate blocks.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS property_profiles (
    property_channel_id  TEXT         NOT NULL PRIMARY KEY,
    payload              JSONB        NOT NULL,
    built_at             TIMESTAMPTZ  NOT NULL,
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Listing path orders by ``built_at DESC`` so the UI surfaces the
-- freshest profile first; mirrors ``InMemoryPropertyProfileStore.
-- list_all`` ordering after a stable ascending sort upstream.
CREATE INDEX IF NOT EXISTS idx_property_profiles_built_at
    ON property_profiles (built_at DESC);

-- ---------------------------------------------------------
-- Grant the application login role access to the table.
--
-- Init scripts run as the bootstrap superuser, so a freshly
-- created table is owned by ``postgres`` and the application
-- role (``brain_engine``) has NO privileges by default.  That gap
-- left ``property_profiles`` unwritable on dev: the bootstrap
-- worker's PgPropertyProfileStore upsert raised
-- InsufficientPrivilegeError, the harvested profile never
-- persisted, ``GET /properties/{id}/knowledge`` 404'd (the store's
-- read swallows the error and returns None), and the agent
-- deferred every answer ("no info, ask PM") even though the data
-- was in Elasticsearch.  Mirror the ALL grant that
-- ``property_state`` (migration 034) carries so the profile cache
-- is read/writable the instant the table exists.
--
-- Guarded + idempotent: a no-op on clusters where the role has not
-- been created yet, re-runnable without error.
-- ---------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brain_engine') THEN
        EXECUTE 'GRANT ALL ON TABLE property_profiles TO brain_engine';
    END IF;
END
$$;
