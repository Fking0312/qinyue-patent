"""backfill empty staff_function to writer for existing staff

Revision ID: p4e5f6a7b8c9
Revises: o3d4e5f6a7b8
Create Date: 2026-08-20

部署前请先备份数据库，再执行 `flask db upgrade`。
本迁移只回填员工空职能，不改表结构，也不在应用启动时写业务数据。
"""

from alembic import op
import sqlalchemy as sa


revision = "p4e5f6a7b8c9"
down_revision = "o3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        sa.text(
            "UPDATE users SET staff_function = 'writer' "
            "WHERE role = 'staff' AND (staff_function IS NULL OR staff_function = '')"
        )
    )


def downgrade():
    pass
