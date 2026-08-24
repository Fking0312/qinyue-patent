"""cases business_owner_id replaces business_owner text

Revision ID: d1a2b3c4d5e6
Revises: c9f5b2d3e4a5
Create Date: 2026-05-04

"""

from alembic import op
import sqlalchemy as sa


revision = "d1a2b3c4d5e6"
down_revision = "c9f5b2d3e4a5"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("cases")}

    if "business_owner_id" not in cols:
        with op.batch_alter_table("cases", schema=None) as batch_op:
            batch_op.add_column(sa.Column("business_owner_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_cases_business_owner_id_users",
                "users",
                ["business_owner_id"],
                ["id"],
            )
            batch_op.create_index(batch_op.f("ix_cases_business_owner_id"), ["business_owner_id"], unique=False)

    if "business_owner" in cols:
        bind.execute(
            sa.text(
                """
                UPDATE cases SET business_owner_id = (
                    SELECT id FROM users
                    WHERE users.username = cases.business_owner AND users.role = 'staff'
                )
                WHERE business_owner IS NOT NULL AND TRIM(business_owner) != ''
                """
            )
        )
        with op.batch_alter_table("cases", schema=None) as batch_op:
            batch_op.drop_column("business_owner")


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("cases")}

    if "business_owner" not in cols:
        with op.batch_alter_table("cases", schema=None) as batch_op:
            batch_op.add_column(sa.Column("business_owner", sa.String(length=120), nullable=True))

        bind.execute(
            sa.text(
                """
                UPDATE cases SET business_owner = (
                    SELECT username FROM users WHERE users.id = cases.business_owner_id
                )
                WHERE business_owner_id IS NOT NULL
                """
            )
        )

    if "business_owner_id" in cols:
        with op.batch_alter_table("cases", schema=None) as batch_op:
            try:
                batch_op.drop_constraint("fk_cases_business_owner_id_users", type_="foreignkey")
            except Exception:
                pass
            try:
                batch_op.drop_index(batch_op.f("ix_cases_business_owner_id"))
            except Exception:
                pass
            batch_op.drop_column("business_owner_id")
