"""cases: move legacy free-form project_type text into case_note

Revision ID: l0a1b2c3d4e5
Revises: k9f0a1b2c3d4
Create Date: 2026-07-17

"""

from alembic import op
import sqlalchemy as sa


revision = "l0a1b2c3d4e5"
down_revision = "k9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    # 仅迁移没有新标签的旧自由文本；已有 case_note 时以换行合并，避免信息丢失。
    op.execute(
        sa.text(
            """
            UPDATE cases
            SET case_note = CASE
                WHEN case_note IS NULL OR TRIM(case_note) = '' THEN project_type
                ELSE case_note || CHAR(10) || project_type
            END
            WHERE (case_type_code IS NULL OR case_type_code = '')
              AND project_type IS NOT NULL
              AND TRIM(project_type) <> ''
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE cases
            SET project_type = NULL
            WHERE (case_type_code IS NULL OR case_type_code = '')
              AND project_type IS NOT NULL
              AND TRIM(project_type) <> ''
            """
        )
    )


def downgrade():
    # 旧字段的语义已是自由备注，无法可靠地从合并后的 case_note 反向拆分。
    pass
