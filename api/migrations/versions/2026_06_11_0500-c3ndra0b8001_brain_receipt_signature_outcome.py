"""brain: receipt signature metadata + T7 outcome stitch columns (cendra)

Extends ``brain_art12_receipts`` (CEN-80) for live emission (CEN-81):
signature metadata written once at emission (honest ``signed=false``
when no tenant key is provisioned) and the post-hoc T7 outcome stitch
(``case_id`` / ``outcome_status``).  Both layers live outside the
canonical record bytes, so existing digests and chain semantics are
unchanged.  Additive only.

Revision ID: c3ndra0b8001
Revises: c3ndra0b7001
Create Date: 2026-06-11

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3ndra0b8001"
down_revision = "c3ndra0b7001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "brain_art12_receipts",
        sa.Column("signed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("brain_art12_receipts", sa.Column("key_id", sa.String(length=64), nullable=True))
    op.add_column("brain_art12_receipts", sa.Column("algorithm", sa.String(length=32), nullable=True))
    op.add_column("brain_art12_receipts", sa.Column("signature_hex", sa.String(length=128), nullable=True))
    op.add_column("brain_art12_receipts", sa.Column("case_id", sa.String(length=64), nullable=True))
    op.add_column("brain_art12_receipts", sa.Column("outcome_status", sa.String(length=16), nullable=True))
    op.add_column("brain_art12_receipts", sa.Column("outcome_recorded_at", sa.DateTime(), nullable=True))
    op.create_index(
        "brain_art12_receipts_case_idx",
        "brain_art12_receipts",
        ["tenant_id", "case_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("brain_art12_receipts_case_idx", table_name="brain_art12_receipts")
    op.drop_column("brain_art12_receipts", "outcome_recorded_at")
    op.drop_column("brain_art12_receipts", "outcome_status")
    op.drop_column("brain_art12_receipts", "case_id")
    op.drop_column("brain_art12_receipts", "signature_hex")
    op.drop_column("brain_art12_receipts", "algorithm")
    op.drop_column("brain_art12_receipts", "key_id")
    op.drop_column("brain_art12_receipts", "signed")
