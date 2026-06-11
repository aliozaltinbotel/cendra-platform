"""brain: durable Art. 12 receipt rows (cendra)

Adds the append-only tenant-scoped receipt table behind Art. 12 audit
emission.  This stores the unsigned decision record plus its chained
digest so later receipt-signing and read APIs can extend the same durable
surface.

Revision ID: c3ndra0b7001
Revises: c3ndra0b6001
Create Date: 2026-06-11

"""

import sqlalchemy as sa
from alembic import op

import models as models

# revision identifiers, used by Alembic.
revision = "c3ndra0b7001"
down_revision = "c3ndra0b6001"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        _now = sa.text("CURRENT_TIMESTAMP(0)")
    else:
        _now = sa.func.current_timestamp()

    op.create_table(
        "brain_art12_receipts",
        sa.Column("tenant_id", models.types.StringUUID(), nullable=False),
        sa.Column("decision_id", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("property_id", sa.String(length=255), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("action_kind", sa.String(length=255), nullable=False),
        sa.Column("handler_solver", sa.String(length=32), nullable=False),
        sa.Column("rationale", models.types.LongText(), nullable=False),
        sa.Column("provenance_digest", sa.String(length=64), nullable=False),
        sa.Column("autonomy_tier", sa.String(length=255), nullable=True),
        sa.Column("planner_style", sa.String(length=255), nullable=True),
        sa.Column("extra", models.types.AdjustedJSON(), nullable=False),
        sa.Column("prev_digest", sa.String(length=64), nullable=False),
        sa.Column("record_digest", sa.String(length=64), nullable=False),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=_now, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=_now, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("brain_art12_receipts_pkey")),
        sa.UniqueConstraint("tenant_id", "decision_id", name="brain_art12_receipts_tenant_decision_uq"),
        sa.UniqueConstraint("tenant_id", "prev_digest", name="brain_art12_receipts_tenant_prev_digest_uq"),
    )
    op.create_index("brain_art12_receipts_tenant_idx", "brain_art12_receipts", ["tenant_id"], unique=False)
    op.create_index(
        "brain_art12_receipts_occurred_idx",
        "brain_art12_receipts",
        ["tenant_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "brain_art12_receipts_digest_idx",
        "brain_art12_receipts",
        ["tenant_id", "record_digest"],
        unique=False,
    )


def downgrade():
    op.drop_table("brain_art12_receipts")
