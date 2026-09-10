"""staff internal document library

Revision ID: v0e1f2a3b4c5
Revises: u9d0e1f2a3b4
Create Date: 2026-09-09

所内资料库：仅管理员上传，按员工职能控制可见范围。
"""
from alembic import op
import sqlalchemy as sa


revision = "v0e1f2a3b4c5"
down_revision = "u9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "staff_documents" in tables:
        return
    op.create_table(
        "staff_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("stored_name", sa.String(length=255), nullable=False),
        sa.Column("audience", sa.String(length=20), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_label", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_staff_documents_audience", "staff_documents", ["audience"], unique=False)
    op.create_index("ix_staff_documents_stored_name", "staff_documents", ["stored_name"], unique=True)
    op.create_index("ix_staff_documents_uploaded_by_id", "staff_documents", ["uploaded_by_id"], unique=False)


def downgrade():
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "staff_documents" not in tables:
        return
    op.drop_index("ix_staff_documents_uploaded_by_id", table_name="staff_documents")
    op.drop_index("ix_staff_documents_stored_name", table_name="staff_documents")
    op.drop_index("ix_staff_documents_audience", table_name="staff_documents")
    op.drop_table("staff_documents")
