"""merge reject/attribution into staff-function line

Revision ID: s7b8c9d0e1f2
Revises: q5f6a7b8c9d0, r6a7b8c9d0e1
Create Date: 2026-09-08 13:24:34.314620

空迁移，只为合并两个 head，不改表结构。

退稿/归属（r6a7b8c9d0e1）是先做在云服务器实际版本 o3d4e5f6a7b8 上并单独上线的，
而本地的职能分流（p4e5f6a7b8c9 → q5f6a7b8c9d0）也从 o3d4e5f6a7b8 分出，于是两条线
并列。不要把 r6a7b8c9d0e1 改成接在 q5f6a7b8c9d0 后面：线上库已经记录了
r6a7b8c9d0e1，那样改会让 Alembic 认为 p 和 q 已经跑过而直接跳过，缺列且不报错。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 's7b8c9d0e1f2'
down_revision = ('q5f6a7b8c9d0', 'r6a7b8c9d0e1')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
