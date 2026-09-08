"""tasks: merge legacy closed_* terminal phases into completed

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-06-17

"""

from alembic import op
import sqlalchemy as sa


revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None

_LEGACY_TERMINAL = (
    "closed_granted",
    "closed_rejected",
    "closed_withdrawn",
)


def upgrade():
    bind = op.get_bind()
    tasks = sa.table("tasks", sa.column("phase_status", sa.String))
    for legacy in _LEGACY_TERMINAL:
        bind.execute(
            tasks.update().where(tasks.c.phase_status == legacy).values(phase_status="completed")
        )


def downgrade():
    pass
