"""cases: project_type, order/return dates, case_note, material_upload_port

Revision ID: f1e2d3c4b5a6
Revises: d1a2b3c4d5e6
Create Date: 2026-05-04

此前仅在启动时通过 SQLite PRAGMA + ALTER 补齐的列，统一纳入迁移；
开发环境若未执行 upgrade，仍可在 __init__ 中启用兜底（见 Config.DEBUG / app.debug）。
"""

from alembic import op
import sqlalchemy as sa


revision = "f1e2d3c4b5a6"
down_revision = "d1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "cases" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("cases")}
    adds = []
    if "project_type" not in cols:
        adds.append(("project_type", sa.String(length=80), True))
    if "order_at" not in cols:
        adds.append(("order_at", sa.DateTime(), True))
    if "expected_return_at" not in cols:
        adds.append(("expected_return_at", sa.DateTime(), True))
    if "actual_return_at" not in cols:
        adds.append(("actual_return_at", sa.DateTime(), True))
    if "case_note" not in cols:
        adds.append(("case_note", sa.Text(), True))
    if "material_upload_port" not in cols:
        adds.append(("material_upload_port", sa.String(length=255), True))
    if not adds:
        return
    with op.batch_alter_table("cases", schema=None) as batch_op:
        for name, typ, nullable in adds:
            batch_op.add_column(sa.Column(name, typ, nullable=nullable))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "cases" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("cases")}
    drops = [
        c
        for c in (
            "material_upload_port",
            "case_note",
            "actual_return_at",
            "expected_return_at",
            "order_at",
            "project_type",
        )
        if c in cols
    ]
    if not drops:
        return
    with op.batch_alter_table("cases", schema=None) as batch_op:
        for name in drops:
            batch_op.drop_column(name)
