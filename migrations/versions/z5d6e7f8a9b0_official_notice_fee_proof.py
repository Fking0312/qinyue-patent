"""official notice fee payment proof

Revision ID: z5d6e7f8a9b0
Revises: z4c5d6e7f8a9
Create Date: 2026-09-11

标记官方缴费必须提交证明文件。
"""
from alembic import op
import sqlalchemy as sa


revision = "z5d6e7f8a9b0"
down_revision = "z4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "official_notices" not in tables:
        return
    columns = {col["name"] for col in inspector.get_columns("official_notices")}
    with op.batch_alter_table("official_notices") as batch_op:
        if "fee_paid_proof_original_name" not in columns:
            batch_op.add_column(sa.Column("fee_paid_proof_original_name", sa.String(length=255), nullable=True))
        if "fee_paid_proof_stored_name" not in columns:
            batch_op.add_column(sa.Column("fee_paid_proof_stored_name", sa.String(length=255), nullable=True))
    columns = {col["name"] for col in sa.inspect(bind).get_columns("official_notices")}
    indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("official_notices")}
    if "fee_paid_proof_stored_name" in columns and "ix_official_notices_fee_paid_proof_stored_name" not in indexes:
        op.create_index(
            "ix_official_notices_fee_paid_proof_stored_name",
            "official_notices",
            ["fee_paid_proof_stored_name"],
            unique=True,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "official_notices" not in tables:
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("official_notices")}
    if "ix_official_notices_fee_paid_proof_stored_name" in indexes:
        op.drop_index("ix_official_notices_fee_paid_proof_stored_name", table_name="official_notices")
    columns = {col["name"] for col in inspector.get_columns("official_notices")}
    with op.batch_alter_table("official_notices") as batch_op:
        if "fee_paid_proof_stored_name" in columns:
            batch_op.drop_column("fee_paid_proof_stored_name")
        if "fee_paid_proof_original_name" in columns:
            batch_op.drop_column("fee_paid_proof_original_name")
