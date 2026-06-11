"""brain: add label to brain_workflow_kinds (cendra)

Adds the nullable operator-facing ``label`` column surfaced via the Trust
Meter read (CEN-50).  Additive only — readers fall back to ``kind`` when
null so the wire value is never empty.

Revision ID: c3ndra0b6001
Revises: c3ndra0b5001
Create Date: 2026-06-11

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c3ndra0b6001'
down_revision = 'c3ndra0b5001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('brain_workflow_kinds', sa.Column('label', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('brain_workflow_kinds', 'label')
