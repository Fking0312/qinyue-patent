"""official notice fee paid at

Revision ID: z4c5d6e7f8a9
Revises: y3b4c5d6e7f8
Create Date: 2026-09-11

缴费通知的官方缴费由流程人员办理，与业务向客户收账分开。
"""
from alembic import op
import sqlalchemy as sa


revision = "z4c5d6e7f8a9"
down_revision = "y3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "official_notices" not in tables:
        return
    columns = {col["name"] for col in inspector.get_columns("official_notices")}
    if "fee_paid_at" in columns:
        return
    with op.batch_alter_table("official_notices") as batch_op:
        batch_op.add_column(sa.Column("fee_paid_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_official_notices_fee_paid_at",
        "official_notices",
        ["fee_paid_at"],
        unique=False,
    )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "official_notices" not in tables:
        return
    columns = {col["name"] for col in inspector.get_columns("official_notices")}
    if "fee_paid_at" not in columns:
        return
    op.drop_index("ix_official_notices_fee_paid_at", table_name="official_notices")
    with op.batch_alter_table("official_notices") as batch_op:
        batch_op.drop_column("fee_paid_at")
