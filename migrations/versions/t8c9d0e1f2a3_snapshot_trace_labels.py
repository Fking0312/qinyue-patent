"""snapshot actor names onto case traces so they survive account deactivation

Revision ID: t8c9d0e1f2a3
Revises: s7b8c9d0e1f2
Create Date: 2026-09-08

离职目前只是停用账号，User 行还在；但材料分组、审核历史都靠实时 JOIN，
账号一旦硬删或 JOIN 不到，上传人和承办人就会变成「—」，已提交的文件也会
从撰写材料列表里消失。这里把当时的姓名钉在记录上，并回填存量数据。
"""
from alembic import op
import sqlalchemy as sa


revision = "t8c9d0e1f2a3"
down_revision = "s7b8c9d0e1f2"
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
    _add_if_missing("cases", "business_owner_label", sa.Column("business_owner_label", sa.String(length=120), nullable=True))
    _add_if_missing("tasks", "assignee_label", sa.Column("assignee_label", sa.String(length=120), nullable=True))
    _add_if_missing("case_review_logs", "operator_label", sa.Column("operator_label", sa.String(length=120), nullable=True))
    _add_if_missing("case_review_logs", "recipient_label", sa.Column("recipient_label", sa.String(length=120), nullable=True))
    _add_if_missing("case_materials", "uploaded_by_label", sa.Column("uploaded_by_label", sa.String(length=120), nullable=True))
    _add_if_missing("case_materials", "uploaded_by_role", sa.Column("uploaded_by_role", sa.String(length=20), nullable=True))
    _add_if_missing(
        "case_material_download_logs",
        "operator_label",
        sa.Column("operator_label", sa.String(length=120), nullable=True),
    )

    bind = op.get_bind()
    # 回填：优先手机号（账号名），没有手机号就只用账号名。已停用的账号同样回填。
    bind.execute(
        sa.text(
            """
            UPDATE cases
            SET business_owner_label = (
                SELECT CASE
                    WHEN users.phone IS NOT NULL AND TRIM(users.phone) != ''
                    THEN users.phone || '（' || users.username || '）'
                    ELSE users.username
                END
                FROM users WHERE users.id = cases.business_owner_id
            )
            WHERE business_owner_id IS NOT NULL
              AND (business_owner_label IS NULL OR business_owner_label = '')
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE tasks
            SET assignee_label = (
                SELECT CASE
                    WHEN users.phone IS NOT NULL AND TRIM(users.phone) != ''
                    THEN users.phone || '（' || users.username || '）'
                    ELSE users.username
                END
                FROM users WHERE users.id = tasks.assignee_id
            )
            WHERE assignee_id IS NOT NULL
              AND (assignee_label IS NULL OR assignee_label = '')
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE case_review_logs
            SET operator_label = (
                SELECT CASE
                    WHEN users.phone IS NOT NULL AND TRIM(users.phone) != ''
                    THEN users.phone || '（' || users.username || '）'
                    ELSE users.username
                END
                FROM users WHERE users.id = case_review_logs.operator_id
            )
            WHERE operator_id IS NOT NULL
              AND (operator_label IS NULL OR operator_label = '')
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE case_review_logs
            SET recipient_label = (
                SELECT CASE
                    WHEN users.phone IS NOT NULL AND TRIM(users.phone) != ''
                    THEN users.phone || '（' || users.username || '）'
                    ELSE users.username
                END
                FROM users WHERE users.id = case_review_logs.recipient_id
            )
            WHERE recipient_id IS NOT NULL
              AND (recipient_label IS NULL OR recipient_label = '')
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE case_materials
            SET uploaded_by_label = (
                SELECT CASE
                    WHEN users.phone IS NOT NULL AND TRIM(users.phone) != ''
                    THEN users.phone || '（' || users.username || '）'
                    ELSE users.username
                END
                FROM users WHERE users.id = case_materials.uploaded_by_id
            ),
            uploaded_by_role = (
                SELECT users.role FROM users WHERE users.id = case_materials.uploaded_by_id
            )
            WHERE uploaded_by_id IS NOT NULL
              AND (
                uploaded_by_label IS NULL OR uploaded_by_label = ''
                OR uploaded_by_role IS NULL OR uploaded_by_role = ''
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE case_material_download_logs
            SET operator_label = (
                SELECT CASE
                    WHEN users.phone IS NOT NULL AND TRIM(users.phone) != ''
                    THEN users.phone || '（' || users.username || '）'
                    ELSE users.username
                END
                FROM users WHERE users.id = case_material_download_logs.operator_id
            )
            WHERE operator_id IS NOT NULL
              AND (operator_label IS NULL OR operator_label = '')
            """
        )
    )


def downgrade():
    for table, column in (
        ("cases", "business_owner_label"),
        ("tasks", "assignee_label"),
        ("case_review_logs", "operator_label"),
        ("case_review_logs", "recipient_label"),
        ("case_materials", "uploaded_by_label"),
        ("case_materials", "uploaded_by_role"),
        ("case_material_download_logs", "operator_label"),
    ):
        if column not in _column_names(table):
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column(column)
