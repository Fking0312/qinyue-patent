"""users: add review inbox seen timestamp

Revision ID: m1b2c3d4e5f6
Revises: l0a1b2c3d4e5
Create Date: 2026-07-18

"""

from alembic import op
import sqlalchemy as sa


revision = "m1b2c3d4e5f6"
down_revision = "l0a1b2c3d4e5"
branch_labels = None
depends_on = None


def _user_columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}


def upgrade():
    if "review_inbox_seen_at" not in _user_columns():
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(sa.Column("review_inbox_seen_at", sa.DateTime(), nullable=True))


def downgrade():
    if "review_inbox_seen_at" in _user_columns():
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_column("review_inbox_seen_at")
