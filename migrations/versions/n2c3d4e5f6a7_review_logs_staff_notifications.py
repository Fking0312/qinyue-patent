"""review logs: add staff notification recipient and read time

Revision ID: n2c3d4e5f6a7
Revises: m1b2c3d4e5f6
Create Date: 2026-07-18

"""

from alembic import op
import sqlalchemy as sa


revision = "n2c3d4e5f6a7"
down_revision = "m1b2c3d4e5f6"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("case_review_logs")
    }


def upgrade():
    columns = _columns()
    with op.batch_alter_table("case_review_logs") as batch_op:
        if "recipient_id" not in columns:
            batch_op.add_column(sa.Column("recipient_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_case_review_logs_recipient_id_users",
                "users",
                ["recipient_id"],
                ["id"],
            )
            batch_op.create_index(
                "ix_case_review_logs_recipient_id",
                ["recipient_id"],
                unique=False,
            )
        if "read_at" not in columns:
            batch_op.add_column(sa.Column("read_at", sa.DateTime(), nullable=True))


def downgrade():
    columns = _columns()
    with op.batch_alter_table("case_review_logs") as batch_op:
        if "read_at" in columns:
            batch_op.drop_column("read_at")
        if "recipient_id" in columns:
            batch_op.drop_index("ix_case_review_logs_recipient_id")
            batch_op.drop_constraint(
                "fk_case_review_logs_recipient_id_users",
                type_="foreignkey",
            )
            batch_op.drop_column("recipient_id")
