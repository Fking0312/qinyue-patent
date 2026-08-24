"""users: nickname for staff/client display

Revision ID: a7c8d9e0f1a2
Revises: f1e2d3c4b5a6
Create Date: 2026-06-15

"""

from alembic import op
import sqlalchemy as sa


revision = "a7c8d9e0f1a2"
down_revision = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("users")}
    if "nickname" not in cols:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(sa.Column("nickname", sa.String(length=50), nullable=True))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("users")}
    if "nickname" in cols:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("nickname")
