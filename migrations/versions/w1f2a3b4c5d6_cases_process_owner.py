"""add case process owner assigned by admin

Revision ID: w1f2a3b4c5d6
Revises: v0e1f2a3b4c5
Create Date: 2026-09-10

流程负责人与承办撰写师分开；业务下单确认时一并指定。
"""
from alembic import op
import sqlalchemy as sa


revision = "w1f2a3b4c5d6"
down_revision = "v0e1f2a3b4c5"
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
    _add_if_missing("cases", "process_owner_id", sa.Column("process_owner_id", sa.Integer(), nullable=True))
    _add_if_missing(
        "cases", "process_owner_label", sa.Column("process_owner_label", sa.String(length=120), nullable=True)
    )
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("cases")}
    if "ix_cases_process_owner_id" not in indexes:
        op.create_index("ix_cases_process_owner_id", "cases", ["process_owner_id"], unique=False)


def downgrade():
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("cases")}
    if "ix_cases_process_owner_id" in indexes:
        op.drop_index("ix_cases_process_owner_id", table_name="cases")
    names = _column_names("cases")
    with op.batch_alter_table("cases") as batch_op:
        if "process_owner_label" in names:
            batch_op.drop_column("process_owner_label")
        if "process_owner_id" in names:
            batch_op.drop_column("process_owner_id")
