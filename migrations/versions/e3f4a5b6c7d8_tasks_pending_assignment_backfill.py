"""tasks: set pending_assignment for unassigned cases

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-06-17

"""

from alembic import op
import sqlalchemy as sa


revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None

_TERMINAL = frozenset(
    {
        "closed_granted",
        "closed_rejected",
        "closed_withdrawn",
        "completed",
    },
)


def upgrade():
    bind = op.get_bind()
    tasks = sa.table(
        "tasks",
        sa.column("id", sa.Integer),
        sa.column("case_id", sa.Integer),
        sa.column("phase_status", sa.String),
    )
    cases = sa.table(
        "cases",
        sa.column("id", sa.Integer),
        sa.column("business_owner_id", sa.Integer),
    )
    rows = bind.execute(
        sa.select(tasks.c.id, tasks.c.phase_status, cases.c.business_owner_id)
        .select_from(tasks.join(cases, tasks.c.case_id == cases.c.id))
        .where(cases.c.business_owner_id.is_(None))
    ).fetchall()
    for row in rows:
        if row.phase_status in _TERMINAL:
            continue
        bind.execute(
            tasks.update().where(tasks.c.id == row.id).values(phase_status="pending_assignment")
        )


def downgrade():
    pass
