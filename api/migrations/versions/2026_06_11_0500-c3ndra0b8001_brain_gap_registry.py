"""brain: knowledge gap registry table (cendra, CEN-28)

Creates ``brain_gap`` — the per-event, append-only Knowledge Gap
registry emitted by the abstention gate (CEN-15 Part B, ruling §E2:
one row per abstention; dedup happens at the read API).  Additive only.

Revision ID: c3ndra0b8001
Revises: c3ndra0b7003
Create Date: 2026-06-11

"""
import sqlalchemy as sa
from alembic import op

import models as models

# revision identifiers, used by Alembic.
revision = 'c3ndra0b8001'
down_revision = 'c3ndra0b7003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'brain_gap',
        sa.Column('id', models.types.StringUUID(), nullable=False),
        sa.Column('tenant_id', models.types.StringUUID(), nullable=False),
        sa.Column('gap_id', sa.String(length=64), nullable=False),
        sa.Column('subject_ref', sa.String(length=255), nullable=False),
        sa.Column('run_id', sa.String(length=255), nullable=False),
        sa.Column('query', sa.Text(), nullable=False),
        sa.Column('missing_predicate', sa.Text(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('threshold', sa.Float(), nullable=True),
        sa.Column('wilson_lb', sa.Float(), nullable=False),
        sa.Column('as_of', sa.DateTime(), nullable=False),
        sa.Column('dispatched_at', sa.DateTime(), nullable=False),
        sa.Column('kg_snapshot_ref', sa.String(length=512), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'gap_id', name='brain_gap_tenant_gap_uq'),
    )
    op.create_index('brain_gap_subject_idx', 'brain_gap', ['tenant_id', 'subject_ref'])
    op.create_index('brain_gap_predicate_idx', 'brain_gap', ['tenant_id', 'subject_ref', 'missing_predicate'])


def downgrade():
    op.drop_index('brain_gap_predicate_idx', table_name='brain_gap')
    op.drop_index('brain_gap_subject_idx', table_name='brain_gap')
    op.drop_table('brain_gap')
