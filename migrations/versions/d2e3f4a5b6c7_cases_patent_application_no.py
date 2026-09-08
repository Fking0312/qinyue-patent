"""cases: add patent_application_no (official filing number)

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-17

"""

from alembic import op
import sqlalchemy as sa


revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.add_column(sa.Column("patent_application_no", sa.String(length=100), nullable=True))
        batch_op.create_index(batch_op.f("ix_cases_patent_application_no"), ["patent_application_no"], unique=False)


def downgrade():
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cases_patent_application_no"))
        batch_op.drop_column("patent_application_no")
