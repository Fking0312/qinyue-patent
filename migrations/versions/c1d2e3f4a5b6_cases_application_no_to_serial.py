"""cases: regenerate application_no as serial number

Revision ID: c1d2e3f4a5b6
Revises: b8c9d0e1f2a3
Create Date: 2026-06-17

"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "c1d2e3f4a5b6"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


cases_table = sa.table(
    "cases",
    sa.column("id", sa.Integer),
    sa.column("application_no", sa.String),
    sa.column("created_at", sa.DateTime),
)


def _coerce_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def upgrade():
    bind = op.get_bind()
    rows = list(bind.execute(sa.select(cases_table.c.id, cases_table.c.created_at).order_by(cases_table.c.created_at, cases_table.c.id)))

    for row in rows:
        bind.execute(
            cases_table.update()
            .where(cases_table.c.id == row.id)
            .values(application_no=f"__serial_tmp_{row.id}"),
        )

    for idx, row in enumerate(rows, start=1):
        created_at = _coerce_dt(row.created_at)
        serial = f"{created_at:%y%m}{idx:04d}"
        bind.execute(
            cases_table.update()
            .where(cases_table.c.id == row.id)
            .values(application_no=serial),
        )


def downgrade():
    # Data-only migration; the previous arbitrary application numbers cannot be reconstructed.
    pass
