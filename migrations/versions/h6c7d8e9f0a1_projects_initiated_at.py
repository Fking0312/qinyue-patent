"""projects: add initiated_at (立项时间)

Revision ID: h6c7d8e9f0a1
Revises: g5b6c7d8e9f0
Create Date: 2026-07-10

"""

from alembic import op
import sqlalchemy as sa


revision = "h6c7d8e9f0a1"
down_revision = "g5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("initiated_at", sa.DateTime(), nullable=True))
    projects = sa.table(
        "projects",
        sa.column("initiated_at", sa.DateTime()),
        sa.column("created_at", sa.DateTime()),
    )
    op.execute(projects.update().values(initiated_at=projects.c.created_at))


def downgrade():
    op.drop_column("projects", "initiated_at")
