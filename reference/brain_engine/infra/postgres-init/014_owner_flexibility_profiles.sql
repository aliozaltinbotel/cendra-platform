-- ---------------------------------------------------------
-- OWNER_FLEXIBILITY_PROFILES — owner baseline used by
-- ExecutionOrchestrator as the "preference" tier before
-- falling through to "ask".  Composite primary key
-- (owner_id, property_id) lets one owner carry distinct
-- profiles across the portfolio.
--
-- JSONB columns hold field groups so the harvester and the
-- pm-correction writer can update one cluster (e.g.
-- ``fee_rules``) without rewriting unrelated groups.
-- ``source_of_truth`` records the most recent writer per
-- field group: ``graphql`` (harvested), ``pm_correction``
-- (sandbox / regenerate), ``owner_directive`` (explicit).
--
-- ``version`` is a monotonic counter writers CAS on to
-- detect conflicting concurrent updates.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS owner_flexibility_profiles (
    owner_id              TEXT         NOT NULL,
    property_id           TEXT         NOT NULL,
    tenant_id             TEXT         NOT NULL DEFAULT '',
    occupancy_capacity    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    fee_rules             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    stay_rules            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    checkin_rules         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    amenity_exceptions    JSONB        NOT NULL DEFAULT '[]'::jsonb,
    flexibility           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    approval_thresholds   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    source_of_truth       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    version               BIGINT       NOT NULL DEFAULT 1,
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, property_id)
);

-- Tenant-scoped listings (one tenant sweeping every owner
-- baseline) bypass the natural PK, so a secondary index keeps
-- that read off a sequential scan.
CREATE INDEX IF NOT EXISTS idx_owner_flex_tenant_owner
    ON owner_flexibility_profiles (tenant_id, owner_id);
