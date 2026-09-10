"""official notices uploaded by process staff

Revision ID: x2a3b4c5d6e7
Revises: w1f2a3b4c5d6
Create Date: 2026-09-10

官方来文与案件材料分开：流程人员从专利局系统下载后上传、转交撰写师。
"""
from alembic import op
import sqlalchemy as sa


revision = "x2a3b4c5d6e7"
down_revision = "w1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "official_notices" in tables:
        return
    op.create_table(
        "official_notices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("notice_type", sa.String(length=40), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("stored_name", sa.String(length=255), nullable=False),
        sa.Column("official_due_at", sa.DateTime(), nullable=True),
        sa.Column("internal_due_at", sa.DateTime(), nullable=True),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_label", sa.String(length=120), nullable=True),
        sa.Column("forwarded_to_id", sa.Integer(), nullable=True),
        sa.Column("forwarded_to_label", sa.String(length=120), nullable=True),
        sa.Column("forwarded_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["forwarded_to_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_official_notices_case_id", "official_notices", ["case_id"], unique=False)
    op.create_index("ix_official_notices_notice_type", "official_notices", ["notice_type"], unique=False)
    op.create_index("ix_official_notices_official_due_at", "official_notices", ["official_due_at"], unique=False)
    op.create_index("ix_official_notices_stored_name", "official_notices", ["stored_name"], unique=True)
    op.create_index("ix_official_notices_uploaded_by_id", "official_notices", ["uploaded_by_id"], unique=False)
    op.create_index("ix_official_notices_forwarded_to_id", "official_notices", ["forwarded_to_id"], unique=False)
    op.create_index("ix_official_notices_forwarded_at", "official_notices", ["forwarded_at"], unique=False)


def downgrade():
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "official_notices" not in tables:
        return
    op.drop_index("ix_official_notices_forwarded_at", table_name="official_notices")
    op.drop_index("ix_official_notices_forwarded_to_id", table_name="official_notices")
    op.drop_index("ix_official_notices_uploaded_by_id", table_name="official_notices")
    op.drop_index("ix_official_notices_stored_name", table_name="official_notices")
    op.drop_index("ix_official_notices_official_due_at", table_name="official_notices")
    op.drop_index("ix_official_notices_notice_type", table_name="official_notices")
    op.drop_index("ix_official_notices_case_id", table_name="official_notices")
    op.drop_table("official_notices")
