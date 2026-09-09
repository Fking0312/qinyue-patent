"""add case intake owner for business-staff order review

Revision ID: u9d0e1f2a3b4
Revises: t8c9d0e1f2a3
Create Date: 2026-09-09

业务人员下单后先进入「待下单确认」，与承办撰写师分开记在 intake_owner 上。
"""
from alembic import op
import sqlalchemy as sa


revision = "u9d0e1f2a3b4"
down_revision = "t8c9d0e1f2a3"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _add_if_missing(table: str, name: str, column: sa.Column) -> None:
    if name in _column_names(table):
        return
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(column)


def upgrade():
    _add_if_missing("cases", "intake_owner_id", sa.Column("intake_owner_id", sa.Integer(), nullable=True))
    _add_if_missing("cases", "intake_owner_label", sa.Column("intake_owner_label", sa.String(length=120), nullable=True))
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("cases")}
    if "ix_cases_intake_owner_id" not in indexes:
        op.create_index("ix_cases_intake_owner_id", "cases", ["intake_owner_id"], unique=False)


def downgrade():
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("cases")}
    if "ix_cases_intake_owner_id" in indexes:
        op.drop_index("ix_cases_intake_owner_id", table_name="cases")
    names = _column_names("cases")
    with op.batch_alter_table("cases") as batch_op:
        if "intake_owner_label" in names:
            batch_op.drop_column("intake_owner_label")
        if "intake_owner_id" in names:
            batch_op.drop_column("intake_owner_id")
