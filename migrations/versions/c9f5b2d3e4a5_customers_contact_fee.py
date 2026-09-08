"""customers contact_name contact_phone fee_standard

Revision ID: c9f5b2d3e4a5
Revises: b8e4a1c2d3f4
Create Date: 2026-05-03

"""

from alembic import op
import sqlalchemy as sa


revision = "c9f5b2d3e4a5"
down_revision = "b8e4a1c2d3f4"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("customers")}
    adds = []
    if "contact_name" not in cols:
        adds.append(("contact_name", sa.String(120), True))
    if "contact_phone" not in cols:
        adds.append(("contact_phone", sa.String(40), True))
    if "fee_standard" not in cols:
        adds.append(("fee_standard", sa.Text(), True))
    if not adds:
        return
    with op.batch_alter_table("customers", schema=None) as batch_op:
        for name, typ, nullable in adds:
            batch_op.add_column(sa.Column(name, typ, nullable=nullable))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("customers")}
    drops = [c for c in ("fee_standard", "contact_phone", "contact_name") if c in cols]
    if not drops:
        return
    with op.batch_alter_table("customers", schema=None) as batch_op:
        for name in drops:
            batch_op.drop_column(name)
