"""brain: Batch 2 kernel tables (cendra)

Creates the seven tenant-scoped Cendra brain tables ported in Phase 1 /
Batch 2 (see PORTING_MAP.md): pattern rules (bi-temporal), decision
cases (append-only episodic evidence), epistemic observations/beliefs,
blockers, per-workflow autonomy state, and the per-tenant workflow-kind
registry.  Additive only — no upstream table is touched.

Revision ID: c3ndra0b2001
Revises: 7bad07dc267d
Create Date: 2026-06-10

"""
import sqlalchemy as sa
from alembic import op

import models as models

# revision identifiers, used by Alembic.
revision = 'c3ndra0b2001'
down_revision = '7bad07dc267d'
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL uses CURRENT_TIMESTAMP(0) (upstream convention); other
    # dialects (tests run SQLite) take the generic form.
    if op.get_bind().dialect.name == "postgresql":
        _now = sa.text("CURRENT_TIMESTAMP(0)")
    else:
        _now = sa.func.current_timestamp()
    op.create_table('brain_beliefs',
    sa.Column('tenant_id', models.types.StringUUID(), nullable=False),
    sa.Column('belief_id', sa.String(length=64), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('promoted_value', models.types.AdjustedJSON(), nullable=False),
    sa.Column('wilson_lb', sa.Float(), nullable=False),
    sa.Column('sample_size', sa.Integer(), nullable=False),
    sa.Column('supporting_observation_ids', models.types.AdjustedJSON(), nullable=False),
    sa.Column('promoted_at', sa.DateTime(), nullable=False),
    sa.Column('promoted_by', sa.String(length=255), nullable=False),
    sa.Column('extra', models.types.AdjustedJSON(), nullable=False),
    sa.Column('id', models.types.StringUUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('brain_beliefs_pkey')),
    sa.UniqueConstraint('tenant_id', 'subject', name='brain_beliefs_tenant_subject_uq')
    )
    op.create_index('brain_beliefs_tenant_idx', 'brain_beliefs', ['tenant_id'], unique=False)

    op.create_table('brain_blockers',
    sa.Column('tenant_id', models.types.StringUUID(), nullable=False),
    sa.Column('blocker_id', sa.String(length=64), nullable=False),
    sa.Column('blocker_type', sa.String(length=255), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('property_id', sa.String(length=255), nullable=False),
    sa.Column('reservation_id', sa.String(length=255), nullable=True),
    sa.Column('description', models.types.LongText(), nullable=False),
    sa.Column('blocks_actions', models.types.AdjustedJSON(), nullable=False),
    sa.Column('metadata_json', models.types.AdjustedJSON(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('resolved_by', sa.String(length=255), nullable=True),
    sa.Column('id', models.types.StringUUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('brain_blockers_pkey')),
    sa.UniqueConstraint('tenant_id', 'blocker_id', name='brain_blockers_tenant_blocker_uq')
    )
    op.create_index('brain_blockers_active_idx', 'brain_blockers', ['tenant_id', 'property_id', 'reservation_id', 'resolved_at'], unique=False)
    op.create_index('brain_blockers_tenant_idx', 'brain_blockers', ['tenant_id'], unique=False)

    op.create_table('brain_decision_cases',
    sa.Column('tenant_id', models.types.StringUUID(), nullable=False),
    sa.Column('case_id', sa.String(length=64), nullable=False),
    sa.Column('stage', sa.String(length=255), nullable=False),
    sa.Column('scenario', sa.String(length=255), nullable=False),
    sa.Column('decision_type', sa.String(length=32), nullable=False),
    sa.Column('property_id', sa.String(length=255), nullable=False),
    sa.Column('owner_id', sa.String(length=255), nullable=False),
    sa.Column('reservation_id', sa.String(length=255), nullable=True),
    sa.Column('guest_id', sa.String(length=255), nullable=True),
    sa.Column('message_text', models.types.LongText(), nullable=False),
    sa.Column('message_language', sa.String(length=16), nullable=False),
    sa.Column('response_text', models.types.LongText(), nullable=False),
    sa.Column('extracted_entities', models.types.AdjustedJSON(), nullable=False),
    sa.Column('pms_snapshot', models.types.AdjustedJSON(), nullable=False),
    sa.Column('calendar_snapshot', models.types.AdjustedJSON(), nullable=False),
    sa.Column('ops_snapshot', models.types.AdjustedJSON(), nullable=False),
    sa.Column('guest_snapshot', models.types.AdjustedJSON(), nullable=False),
    sa.Column('decision', models.types.AdjustedJSON(), nullable=False),
    sa.Column('executed_actions', models.types.AdjustedJSON(), nullable=False),
    sa.Column('outcome', models.types.AdjustedJSON(), nullable=False),
    sa.Column('evidence_source_ids', models.types.AdjustedJSON(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('orchestrator_verdict', models.types.AdjustedJSON(), nullable=False),
    sa.Column('archived_at', sa.DateTime(), nullable=True),
    sa.Column('foundation_scenario_id', sa.String(length=255), nullable=True),
    sa.Column('origin', models.types.AdjustedJSON(), nullable=False),
    sa.Column('id', models.types.StringUUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('brain_decision_cases_pkey')),
    sa.UniqueConstraint('tenant_id', 'case_id', name='brain_decision_cases_tenant_case_uq')
    )
    op.create_index('brain_decision_cases_created_idx', 'brain_decision_cases', ['tenant_id', 'created_at'], unique=False)
    op.create_index('brain_decision_cases_reservation_idx', 'brain_decision_cases', ['tenant_id', 'reservation_id'], unique=False)
    op.create_index('brain_decision_cases_search_idx', 'brain_decision_cases', ['tenant_id', 'scenario', 'property_id', 'owner_id', 'stage'], unique=False)
    op.create_index('brain_decision_cases_tenant_idx', 'brain_decision_cases', ['tenant_id'], unique=False)

    op.create_table('brain_observations',
    sa.Column('tenant_id', models.types.StringUUID(), nullable=False),
    sa.Column('observation_id', sa.String(length=64), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('value', models.types.AdjustedJSON(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.Column('provenance_kind', sa.String(length=32), nullable=False),
    sa.Column('provenance_source_id', sa.String(length=255), nullable=False),
    sa.Column('provenance_correlation_id', sa.String(length=255), nullable=True),
    sa.Column('integrity_hex', sa.String(length=64), nullable=False),
    sa.Column('id', models.types.StringUUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('brain_observations_pkey')),
    sa.UniqueConstraint('tenant_id', 'observation_id', name='brain_observations_tenant_obs_uq')
    )
    op.create_index('brain_observations_subject_idx', 'brain_observations', ['tenant_id', 'subject'], unique=False)
    op.create_index('brain_observations_tenant_idx', 'brain_observations', ['tenant_id'], unique=False)

    op.create_table('brain_pattern_rules',
    sa.Column('tenant_id', models.types.StringUUID(), nullable=False),
    sa.Column('pattern_id', sa.String(length=64), nullable=False),
    sa.Column('scenario', sa.String(length=255), nullable=False),
    sa.Column('scope', sa.String(length=32), nullable=False),
    sa.Column('scope_id', sa.String(length=255), nullable=False),
    sa.Column('conditions', models.types.AdjustedJSON(), nullable=False),
    sa.Column('action', models.types.AdjustedJSON(), nullable=False),
    sa.Column('blocker_types', models.types.AdjustedJSON(), nullable=False),
    sa.Column('support_count', sa.Integer(), nullable=False),
    sa.Column('counterexample_count', sa.Integer(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('risk_level', sa.String(length=16), nullable=False),
    sa.Column('execution_mode', sa.String(length=16), nullable=False),
    sa.Column('valid_from', sa.DateTime(), nullable=False),
    sa.Column('valid_to', sa.DateTime(), nullable=True),
    sa.Column('invalid_at', sa.DateTime(), nullable=True),
    sa.Column('deactivated_at', sa.DateTime(), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('source_case_ids', models.types.AdjustedJSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('foundation_scenario_id', sa.String(length=255), nullable=True),
    sa.Column('origin', models.types.AdjustedJSON(), nullable=False),
    sa.Column('id', models.types.StringUUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('brain_pattern_rules_pkey')),
    sa.UniqueConstraint('tenant_id', 'pattern_id', name='brain_pattern_rules_tenant_pattern_uq')
    )
    op.create_index('brain_pattern_rules_active_scope_idx', 'brain_pattern_rules', ['tenant_id', 'active', 'scenario', 'scope', 'scope_id'], unique=False)
    op.create_index('brain_pattern_rules_tenant_idx', 'brain_pattern_rules', ['tenant_id'], unique=False)

    op.create_table('brain_workflow_autonomy',
    sa.Column('tenant_id', models.types.StringUUID(), nullable=False),
    sa.Column('property_id', sa.String(length=255), nullable=False),
    sa.Column('workflow', sa.String(length=255), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('sample_size', sa.Integer(), nullable=False),
    sa.Column('success_rate', sa.Float(), nullable=False),
    sa.Column('override_rate', sa.Float(), nullable=False),
    sa.Column('incidents', sa.Integer(), nullable=False),
    sa.Column('mean_latency_seconds', sa.Float(), nullable=False),
    sa.Column('hold_seconds', sa.Integer(), nullable=False),
    sa.Column('changed_at', sa.DateTime(), nullable=False),
    sa.Column('changed_by', sa.String(length=255), nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=False),
    sa.Column('id', models.types.StringUUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('brain_workflow_autonomy_pkey')),
    sa.UniqueConstraint('tenant_id', 'property_id', 'workflow', name='brain_workflow_autonomy_scope_uq')
    )
    op.create_index('brain_workflow_autonomy_property_idx', 'brain_workflow_autonomy', ['tenant_id', 'property_id'], unique=False)
    op.create_index('brain_workflow_autonomy_tenant_idx', 'brain_workflow_autonomy', ['tenant_id'], unique=False)

    op.create_table('brain_workflow_kinds',
    sa.Column('tenant_id', models.types.StringUUID(), nullable=False),
    sa.Column('kind', sa.String(length=255), nullable=False),
    sa.Column('event_aliases', models.types.AdjustedJSON(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('id', models.types.StringUUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('brain_workflow_kinds_pkey')),
    sa.UniqueConstraint('tenant_id', 'kind', name='brain_workflow_kinds_tenant_kind_uq')
    )
    op.create_index('brain_workflow_kinds_tenant_idx', 'brain_workflow_kinds', ['tenant_id'], unique=False)


def downgrade():
    op.drop_table('brain_workflow_kinds')
    op.drop_table('brain_workflow_autonomy')
    op.drop_table('brain_pattern_rules')
    op.drop_table('brain_observations')
    op.drop_table('brain_decision_cases')
    op.drop_table('brain_blockers')
    op.drop_table('brain_beliefs')
