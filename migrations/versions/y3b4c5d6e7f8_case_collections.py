"""billing owner and case collections

Revision ID: y3b4c5d6e7f8
Revises: x2a3b4c5d6e7
Create Date: 2026-09-10

收账负责人挂在案件上；缴费通知转交后生成收账记录，证明文件与撰写材料分开存。
"""
from alembic import op
import sqlalchemy as sa


revision = "y3b4c5d6e7f8"
down_revision = "x2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    case_columns = {col["name"] for col in inspector.get_columns("cases")}
    if "billing_owner_id" not in case_columns:
        with op.batch_alter_table("cases") as batch_op:
            batch_op.add_column(sa.Column("billing_owner_id", sa.Integer(), nullable=True))
        op.create_index("ix_cases_billing_owner_id", "cases", ["billing_owner_id"], unique=False)
        case_columns = {col["name"] for col in sa.inspect(bind).get_columns("cases")}
    if "billing_owner_label" not in case_columns:
        with op.batch_alter_table("cases") as batch_op:
            batch_op.add_column(sa.Column("billing_owner_label", sa.String(length=120), nullable=True))

    tables = inspector.get_table_names()
    if "case_collections" not in tables:
        op.create_table(
            "case_collections",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("case_id", sa.Integer(), nullable=False),
            sa.Column("notice_id", sa.Integer(), nullable=True),
            sa.Column("amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("submitted_by_id", sa.Integer(), nullable=True),
            sa.Column("submitted_by_label", sa.String(length=120), nullable=True),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("confirmed_by_id", sa.Integer(), nullable=True),
            sa.Column("confirmed_by_label", sa.String(length=120), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("reject_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
            sa.ForeignKeyConstraint(["notice_id"], ["official_notices.id"]),
            sa.ForeignKeyConstraint(["submitted_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["confirmed_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("notice_id", name="uq_case_collections_notice_id"),
        )
        op.create_index("ix_case_collections_case_id", "case_collections", ["case_id"], unique=False)
        op.create_index("ix_case_collections_notice_id", "case_collections", ["notice_id"], unique=False)
        op.create_index("ix_case_collections_status", "case_collections", ["status"], unique=False)
        op.create_index("ix_case_collections_submitted_by_id", "case_collections", ["submitted_by_id"], unique=False)
        op.create_index("ix_case_collections_confirmed_by_id", "case_collections", ["confirmed_by_id"], unique=False)

    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "case_collection_proofs" not in tables:
        op.create_table(
            "case_collection_proofs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("collection_id", sa.Integer(), nullable=False),
            sa.Column("uploaded_by_id", sa.Integer(), nullable=False),
            sa.Column("uploaded_by_label", sa.String(length=120), nullable=True),
            sa.Column("original_name", sa.String(length=255), nullable=False),
            sa.Column("stored_name", sa.String(length=255), nullable=False),
            sa.Column("note", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["collection_id"], ["case_collections.id"]),
            sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_case_collection_proofs_collection_id",
            "case_collection_proofs",
            ["collection_id"],
            unique=False,
        )
        op.create_index(
            "ix_case_collection_proofs_uploaded_by_id",
            "case_collection_proofs",
            ["uploaded_by_id"],
            unique=False,
        )
        op.create_index(
            "ix_case_collection_proofs_stored_name",
            "case_collection_proofs",
            ["stored_name"],
            unique=True,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "case_collection_proofs" in tables:
        op.drop_index("ix_case_collection_proofs_stored_name", table_name="case_collection_proofs")
        op.drop_index("ix_case_collection_proofs_uploaded_by_id", table_name="case_collection_proofs")
        op.drop_index("ix_case_collection_proofs_collection_id", table_name="case_collection_proofs")
        op.drop_table("case_collection_proofs")
    if "case_collections" in tables:
        op.drop_index("ix_case_collections_confirmed_by_id", table_name="case_collections")
        op.drop_index("ix_case_collections_submitted_by_id", table_name="case_collections")
        op.drop_index("ix_case_collections_status", table_name="case_collections")
        op.drop_index("ix_case_collections_notice_id", table_name="case_collections")
        op.drop_index("ix_case_collections_case_id", table_name="case_collections")
        op.drop_table("case_collections")
    case_columns = {col["name"] for col in inspector.get_columns("cases")}
    indexes = {index["name"] for index in inspector.get_indexes("cases")}
    if "ix_cases_billing_owner_id" in indexes:
        op.drop_index("ix_cases_billing_owner_id", table_name="cases")
    with op.batch_alter_table("cases") as batch_op:
        if "billing_owner_label" in case_columns:
            batch_op.drop_column("billing_owner_label")
        if "billing_owner_id" in case_columns:
            batch_op.drop_column("billing_owner_id")
