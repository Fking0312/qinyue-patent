"""expand invention case type taxonomy

Revision ID: o3d4e5f6a7b8
Revises: n2c3d4e5f6a7
Create Date: 2026-07-18

"""

from alembic import op


revision = "o3d4e5f6a7b8"
down_revision = "n2c3d4e5f6a7"
branch_labels = None
depends_on = None


_CODE_CHANGES = {
    "invention_nonrisk_mechanical": "invention_nonrisk_unknown_mechanical",
    "invention_nonrisk_software": "invention_nonrisk_unknown_software",
    "invention_nonrisk_chemical": "invention_nonrisk_unknown_chemical",
}


def upgrade():
    for old_code, new_code in _CODE_CHANGES.items():
        op.execute(
            "UPDATE cases "
            f"SET case_type_code = '{new_code}' "
            f"WHERE case_type_code = '{old_code}'"
        )


def downgrade():
    for old_code, new_code in _CODE_CHANGES.items():
        op.execute(
            "UPDATE cases "
            f"SET case_type_code = '{old_code}' "
            f"WHERE case_type_code = '{new_code}'"
        )
