"""login_throttles: failed login counters for lockout

Revision ID: q5f6a7b8c9d0
Revises: p4e5f6a7b8c9
Create Date: 2026-08-20

"""

from alembic import op
import sqlalchemy as sa


revision = "q5f6a7b8c9d0"
down_revision = "p4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    # create_app() 启动时的 db.create_all() 会先把这张表建出来，本迁移随后再
    # CREATE TABLE 就会以 "table already exists" 失败，导致整个 upgrade 中断。
    # 已存在时直接跳过：表结构与下面的定义一致，跳过不会留下差异。
    if sa.inspect(op.get_bind()).has_table("login_throttles"):
        return
    op.create_table(
        "login_throttles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("fail_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "key", name="uq_login_throttles_scope_key"),
    )


def downgrade():
    if not sa.inspect(op.get_bind()).has_table("login_throttles"):
        return
    op.drop_table("login_throttles")
