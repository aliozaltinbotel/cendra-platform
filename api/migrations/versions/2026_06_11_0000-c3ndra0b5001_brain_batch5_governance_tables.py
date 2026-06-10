"""brain: Batch 5 governance tables (cendra)

Adds the abstention calibration window (persistent) and the owner-policy
document registry.  Additive only.

Revision ID: c3ndra0b5001
Revises: c3ndra0b2001
Create Date: 2026-06-11

"""
import sqlalchemy as sa
from alembic import op

import models as models

# revision identifiers, used by Alembic.
revision = 'c3ndra0b5001'
down_revision = 'c3ndra0b2001'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        _now = sa.text("CURRENT_TIMESTAMP(0)")
    else:
        _now = sa.func.current_timestamp()
    op.create_table('brain_calibration_samples',
    sa.Column('tenant_id', models.types.StringUUID(), nullable=False),
    sa.Column('tool_id', sa.String(length=255), nullable=False),
    sa.Column('predicted_confidence', sa.Float(), nullable=False),
    sa.Column('actual_success', sa.Boolean(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.Column('id', models.types.StringUUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('brain_calibration_samples_pkey'))
    )
    op.create_index('brain_calibration_tenant_tool_idx', 'brain_calibration_samples', ['tenant_id', 'tool_id', 'recorded_at'], unique=False)

    op.create_table('brain_owner_policies',
    sa.Column('tenant_id', models.types.StringUUID(), nullable=False),
    sa.Column('owner_id', sa.String(length=255), nullable=False),
    sa.Column('document_text', models.types.LongText(), nullable=False),
    sa.Column('compiled', models.types.AdjustedJSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', models.types.StringUUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=_now, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('brain_owner_policies_pkey')),
    sa.UniqueConstraint('tenant_id', 'owner_id', name='brain_owner_policies_tenant_owner_uq')
    )
    op.create_index('brain_owner_policies_tenant_idx', 'brain_owner_policies', ['tenant_id'], unique=False)


def downgrade():
    op.drop_table('brain_owner_policies')
    op.drop_table('brain_calibration_samples')
