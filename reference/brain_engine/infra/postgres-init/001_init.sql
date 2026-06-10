-- ════════════════════════════════════════════════════════════
-- Brain Engine initial schema (migration 001)
-- ════════════════════════════════════════════════════════════
-- Runs as the brain_engine owner on the brain_engine database.
-- Extensions (vector, pg_trgm, uuid-ossp) are installed by the
-- Cluster bootstrap block and assumed present.

-- ---------------------------------------------------------
-- DECISION_CASES — canonical record of every agent decision.
-- Structured truth; all learning derives from this table.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS decision_cases (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    case_id TEXT UNIQUE NOT NULL,

    stage TEXT NOT NULL,
    scenario TEXT NOT NULL,
    decision_type TEXT NOT NULL,

    property_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    reservation_id TEXT,
    guest_id TEXT,

    message_text TEXT DEFAULT '',
    message_language TEXT DEFAULT 'en',
    response_text TEXT DEFAULT '',
    extracted_entities JSONB DEFAULT '{}'::jsonb,

    pms_snapshot JSONB DEFAULT '{}'::jsonb,
    calendar_snapshot JSONB DEFAULT '{}'::jsonb,
    ops_snapshot JSONB DEFAULT '{}'::jsonb,
    guest_snapshot JSONB DEFAULT '{}'::jsonb,

    decision JSONB NOT NULL,
    executed_actions TEXT[] DEFAULT '{}',
    outcome JSONB DEFAULT '{}'::jsonb,

    evidence_source_ids TEXT[] DEFAULT '{}',

    message_embedding vector(1536),
    context_embedding vector(1536),

    search_doc tsvector GENERATED ALWAYS AS (
        to_tsvector('simple',
            coalesce(message_text, '') || ' ' ||
            coalesce(response_text, '') || ' ' ||
            coalesce(decision->>'reasoning', '')
        )
    ) STORED,

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dc_scope
    ON decision_cases (owner_id, property_id, scenario, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dc_stage
    ON decision_cases (stage, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dc_reservation
    ON decision_cases (reservation_id) WHERE reservation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dc_guest
    ON decision_cases (guest_id) WHERE guest_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dc_fts
    ON decision_cases USING GIN (search_doc);
CREATE INDEX IF NOT EXISTS idx_dc_created
    ON decision_cases (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dc_msg_embedding
    ON decision_cases USING hnsw (message_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_dc_ctx_embedding
    ON decision_cases USING hnsw (context_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ---------------------------------------------------------
-- PATTERN_RULES — learned rules extracted from DecisionCases.
-- Wilson lower bound applied in extractor.py.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS pattern_rules (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    pattern_id TEXT UNIQUE NOT NULL,

    scenario TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_id TEXT NOT NULL,

    conditions JSONB NOT NULL DEFAULT '{}'::jsonb,
    action JSONB NOT NULL,
    blocker_types TEXT[] DEFAULT '{}',

    support_count INTEGER DEFAULT 0,
    counterexample_count INTEGER DEFAULT 0,
    confidence NUMERIC(5,4) DEFAULT 0.0,

    risk_level TEXT DEFAULT 'medium',
    execution_mode TEXT DEFAULT 'ask',

    valid_from TIMESTAMPTZ DEFAULT now(),
    valid_to TIMESTAMPTZ,
    superseded_by UUID REFERENCES pattern_rules(id),

    source_case_ids TEXT[] DEFAULT '{}',

    active BOOLEAN DEFAULT true,
    last_seen_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pr_active
    ON pattern_rules (scenario, scope, scope_id)
    WHERE active = true AND valid_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_pr_confidence
    ON pattern_rules (confidence DESC) WHERE active = true;

-- ---------------------------------------------------------
-- ZFS_BLOCKS / ZFS_POINTERS / ZFS_SNAPSHOTS
-- Content-addressed storage for BrainZFS (conversation archive,
-- dedup of property-level repeated context).
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS zfs_blocks (
    block_hash TEXT PRIMARY KEY,
    data BYTEA NOT NULL,
    size_bytes INTEGER NOT NULL,
    ref_count INTEGER DEFAULT 1,
    compressed BOOLEAN DEFAULT false,
    compression_algo TEXT DEFAULT 'none',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS zfs_pointers (
    dataset TEXT NOT NULL,
    path TEXT NOT NULL,
    block_hash TEXT NOT NULL REFERENCES zfs_blocks(block_hash),
    version INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (dataset, path, version)
);

CREATE INDEX IF NOT EXISTS idx_zp_current
    ON zfs_pointers (dataset, path, version DESC);

CREATE TABLE IF NOT EXISTS zfs_snapshots (
    name TEXT PRIMARY KEY,
    dataset TEXT NOT NULL,
    pointer_table JSONB NOT NULL,
    pointer_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------
-- GUEST_MEMORIES — per-guest durable memory.
-- Migrates from Redis (currently ephemeral with TTL).
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS guest_memories (
    guest_id TEXT PRIMARY KEY,
    total_stays INTEGER DEFAULT 0,
    total_interactions INTEGER DEFAULT 0,
    language TEXT DEFAULT '',
    communication_style TEXT DEFAULT '',
    avg_satisfaction NUMERIC(3,2) DEFAULT 0.0,
    satisfaction_scores JSONB DEFAULT '[]'::jsonb,
    preferences JSONB DEFAULT '{}'::jsonb,
    common_requests JSONB DEFAULT '[]'::jsonb,
    incidents JSONB DEFAULT '[]'::jsonb,
    risk_flags JSONB DEFAULT '[]'::jsonb,
    patterns JSONB DEFAULT '[]'::jsonb,
    property_history JSONB DEFAULT '[]'::jsonb,
    notes JSONB DEFAULT '[]'::jsonb,
    first_seen TIMESTAMPTZ DEFAULT now(),
    last_seen TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gm_risk
    ON guest_memories USING GIN (risk_flags);
CREATE INDEX IF NOT EXISTS idx_gm_last_seen
    ON guest_memories (last_seen DESC);

-- ---------------------------------------------------------
-- INTERACTIONS — long-term interaction log.
-- Replaces the 90-day Redis TTL for seasonal pattern learning.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS interactions (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    interaction_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    property_id TEXT,
    owner_id TEXT,

    input_message TEXT DEFAULT '',
    output_response TEXT DEFAULT '',
    output_actions JSONB DEFAULT '[]'::jsonb,

    confidence NUMERIC(4,3),
    cognitive_level TEXT,
    grader_score NUMERIC(4,3),

    owner_approved BOOLEAN,
    owner_intervened BOOLEAN DEFAULT false,
    guest_satisfied BOOLEAN,

    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ix_type
    ON interactions (event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ix_property
    ON interactions (property_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ix_created
    ON interactions (created_at DESC);
