"""cases: 专利局退稿标记与案件归属（退稿一律转内部案件）

Revision ID: r6a7b8c9d0e1
Revises: o3d4e5f6a7b8
Create Date: 2026-09-08

接在 o3d4e5f6a7b8（云服务器实际版本）之后。本地 main 上的 p4e5f6a7b8c9 同样
接在 o3d4e5f6a7b8 之后，因此两条线并列；将来把本功能合进 main 时 Alembic 会
出现两个 head，需要改本文件的 down_revision 或加一条 merge revision。
"""

from alembic import op
import sqlalchemy as sa


revision = "r6a7b8c9d0e1"
down_revision = "o3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cases", sa.Column("rejected_at", sa.DateTime(), nullable=True))
    op.add_column("cases", sa.Column("reject_note", sa.Text(), nullable=True))
    op.add_column(
        "cases",
        sa.Column(
            "attribution",
            sa.String(length=20),
            nullable=False,
            server_default="customer",
        ),
    )
    op.create_index("ix_cases_rejected_at", "cases", ["rejected_at"])
    op.create_index("ix_cases_attribution", "cases", ["attribution"])


def downgrade():
    op.drop_index("ix_cases_attribution", table_name="cases")
    op.drop_index("ix_cases_rejected_at", table_name="cases")
    op.drop_column("cases", "attribution")
    op.drop_column("cases", "reject_note")
    op.drop_column("cases", "rejected_at")
