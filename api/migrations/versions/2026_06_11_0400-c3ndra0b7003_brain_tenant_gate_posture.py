"""brain: add tenant gate posture override + audit tables (cendra)

Adds the CEN-31 observe-only per-tenant posture state plus the
append-only audit trail used for evidence packs.

Revision ID: c3ndra0b7003
Revises: c3ndra0b7002
Create Date: 2026-06-11

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3ndra0b7003"
down_revision = "c3ndra0b7002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "brain_tenant_gate_postures",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("posture", sa.String(length=16), nullable=False),
        sa.Column("actor_kind", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("changed_by", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("brain_tenant_gate_postures_pkey")),
        sa.UniqueConstraint("tenant_id", name="brain_tenant_gate_postures_tenant_uq"),
    )
    op.create_index(
        "brain_tenant_gate_postures_tenant_idx",
        "brain_tenant_gate_postures",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "brain_tenant_gate_posture_audits",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("prior_posture", sa.String(length=16), nullable=False),
        sa.Column("new_posture", sa.String(length=16), nullable=False),
        sa.Column("prior_effective_posture", sa.String(length=16), nullable=False),
        sa.Column("new_effective_posture", sa.String(length=16), nullable=False),
        sa.Column("actor_kind", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("changed_by", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("brain_tenant_gate_posture_audits_pkey")),
    )
    op.create_index(
        "brain_tenant_gate_posture_audits_keyset_idx",
        "brain_tenant_gate_posture_audits",
        ["tenant_id", "occurred_at", "id"],
        unique=False,
    )


def downgrade():
    op.drop_index("brain_tenant_gate_posture_audits_keyset_idx", table_name="brain_tenant_gate_posture_audits")
    op.drop_table("brain_tenant_gate_posture_audits")
    op.drop_index("brain_tenant_gate_postures_tenant_idx", table_name="brain_tenant_gate_postures")
    op.drop_table("brain_tenant_gate_postures")
