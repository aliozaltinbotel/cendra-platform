-- ---------------------------------------------------------
-- PROPERTY_PM_FACTS — manager-confirmed knowledge captured
-- when a PM replies through PM Chat (Sandbox v2) or the
-- regenerate-pm-knowledge endpoint.  Read by the live-chat
-- pipeline so the AI answers directly on the next guest turn
-- instead of repeating the BRAIN flag forever.
--
-- ``property_channel_id = ''`` means "customer-wide": the PM
-- Chat call did not carry a property selection, so the fact
-- applies to every property under the customer.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS property_pm_facts (
    id                   BIGSERIAL    PRIMARY KEY,
    customer_id          TEXT         NOT NULL,
    org_id               TEXT         NOT NULL DEFAULT '',
    property_channel_id  TEXT         NOT NULL DEFAULT '',
    fact_text            TEXT         NOT NULL,
    source_message_id    TEXT         NOT NULL DEFAULT '',
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Idempotency on the natural key — replaying the same PM
-- correction must not bloat the prompt with duplicates.
-- The unique index is partial on ``length(fact_text) > 0``
-- so empty placeholder rows would never collide (they should
-- not be inserted in the first place, but defensive is cheap).
ALTER TABLE property_pm_facts
    DROP CONSTRAINT IF EXISTS uq_property_pm_facts_natural_key;
ALTER TABLE property_pm_facts
    ADD CONSTRAINT uq_property_pm_facts_natural_key
    UNIQUE (customer_id, property_channel_id, fact_text);

-- Live-chat read path filters by (customer_id,
-- property_channel_id IN (target, '')) ORDER BY created_at —
-- the composite index covers it without a separate sort.
CREATE INDEX IF NOT EXISTS idx_property_pm_facts_lookup
    ON property_pm_facts (
        customer_id,
        property_channel_id,
        created_at
    );
