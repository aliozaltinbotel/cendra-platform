"""brain: published verification-key registry (cendra)

Adds the additive ``brain_signing_keys`` table used by the receipt
verification-key publication surface. The table stores only published
verification metadata and the KMS locator needed by the custody layer;
private signing material remains out of band.

Revision ID: c3ndra0b7002
Revises: c3ndra0b7001
Create Date: 2026-06-11

"""

import sqlalchemy as sa
from alembic import op

import models

# revision identifiers, used by Alembic.
revision = "c3ndra0b7002"
down_revision = "c3ndra0b7001"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        _now = sa.text("CURRENT_TIMESTAMP(0)")
    else:
        _now = sa.func.current_timestamp()

    op.create_table(
        "brain_signing_keys",
        sa.Column("tenant_id", models.types.StringUUID(), nullable=False),
        sa.Column("purpose", sa.String(length=255), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("key_id", sa.String(length=255), nullable=False),
        sa.Column("public_key_base64url", sa.String(length=255), nullable=False),
        sa.Column("kms_key_ref", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("id", models.types.StringUUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=_now, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=_now, nullable=False),
        sa.CheckConstraint("status IN ('active', 'retired')", name="brain_signing_keys_status_ck"),
        sa.PrimaryKeyConstraint("id", name=op.f("brain_signing_keys_pkey")),
        sa.UniqueConstraint("key_id", name="brain_signing_keys_key_id_uq"),
    )
    op.create_index("brain_signing_keys_tenant_idx", "brain_signing_keys", ["tenant_id"], unique=False)
    op.create_index(
        "brain_signing_keys_tenant_status_activated_idx",
        "brain_signing_keys",
        ["tenant_id", "status", "activated_at"],
        unique=False,
    )
    op.create_index(
        "brain_signing_keys_active_tenant_purpose_uq",
        "brain_signing_keys",
        ["tenant_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade():
    op.drop_index("brain_signing_keys_active_tenant_purpose_uq", table_name="brain_signing_keys")
    op.drop_index("brain_signing_keys_tenant_status_activated_idx", table_name="brain_signing_keys")
    op.drop_index("brain_signing_keys_tenant_idx", table_name="brain_signing_keys")
    op.drop_table("brain_signing_keys")
