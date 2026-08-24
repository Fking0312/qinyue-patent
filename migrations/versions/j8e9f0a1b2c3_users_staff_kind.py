"""users: add staff_kind (formal / outsource)

Revision ID: j8e9f0a1b2c3
Revises: i7d8e9f0a1b2
Create Date: 2026-07-17

"""

from alembic import op
import sqlalchemy as sa


revision = "j8e9f0a1b2c3"
down_revision = "i7d8e9f0a1b2"
branch_labels = None
depends_on = None


def _users_columns() -> set[str]:
    bind = op.get_bind()
    rows = bind.execute(sa.text("PRAGMA table_info(users)")).fetchall()
    return {row[1] for row in rows}


def upgrade():
    cols = _users_columns()
    if "staff_kind" not in cols:
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(sa.Column("staff_kind", sa.String(length=20), nullable=True))
    op.execute(
        sa.text(
            "UPDATE users SET staff_kind = 'formal' WHERE role = 'staff' AND (staff_kind IS NULL OR staff_kind = '')"
        )
    )


def downgrade():
    cols = _users_columns()
    if "staff_kind" in cols:
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_column("staff_kind")
