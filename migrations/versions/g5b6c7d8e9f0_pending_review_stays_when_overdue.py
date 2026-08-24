"""tasks: keep pending_review when overdue (revert overdue_pending_review)

Revision ID: g5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-06-17

"""

from alembic import op
import sqlalchemy as sa


revision = "g5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tasks = sa.table("tasks", sa.column("phase_status", sa.String))
    bind.execute(
        tasks.update()
        .where(tasks.c.phase_status == "overdue_pending_review")
        .values(phase_status="pending_review")
    )


def downgrade():
    pass
