"""users: rename nickname to phone for login

Revision ID: b8c9d0e1f2a3
Revises: a7c8d9e0f1a2
Create Date: 2026-06-17

"""

from alembic import op
import sqlalchemy as sa


revision = "b8c9d0e1f2a3"
down_revision = "a7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("users")}
    if "nickname" in cols and "phone" not in cols:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.alter_column("nickname", new_column_name="phone", existing_type=sa.String(length=50))
    elif "phone" not in cols:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(sa.Column("phone", sa.String(length=20), nullable=True))

    indexes = {idx["name"] for idx in insp.get_indexes("users")}
    if "uq_users_phone" not in indexes:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.create_index("uq_users_phone", ["phone"], unique=True)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("users")}
    indexes = {idx["name"] for idx in insp.get_indexes("users")}
    if "uq_users_phone" in indexes:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_index("uq_users_phone")
    if "phone" in cols and "nickname" not in cols:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.alter_column("phone", new_column_name="nickname", existing_type=sa.String(length=50))
