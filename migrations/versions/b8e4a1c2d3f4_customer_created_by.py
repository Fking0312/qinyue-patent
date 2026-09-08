"""customers created_by_id for audit filter

Revision ID: b8e4a1c2d3f4
Revises: 3bf7fcc83ff3
Create Date: 2026-05-03

"""

from alembic import op
import sqlalchemy as sa


revision = "b8e4a1c2d3f4"
down_revision = "3bf7fcc83ff3"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("customers")}
    if "created_by_id" not in cols:
        with op.batch_alter_table("customers", schema=None) as batch_op:
            batch_op.add_column(sa.Column("created_by_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_customers_created_by_id_users",
                "users",
                ["created_by_id"],
                ["id"],
            )
            batch_op.create_index(batch_op.f("ix_customers_created_by_id"), ["created_by_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("customers")}
    if "created_by_id" in cols:
        with op.batch_alter_table("customers", schema=None) as batch_op:
            batch_op.drop_index(batch_op.f("ix_customers_created_by_id"))
            batch_op.drop_constraint("fk_customers_created_by_id_users", type_="foreignkey")
            batch_op.drop_column("created_by_id")
