"""cases: add fixed hierarchical case_type_code

Revision ID: k9f0a1b2c3d4
Revises: j8e9f0a1b2c3
Create Date: 2026-07-17

"""

from alembic import op
import sqlalchemy as sa


revision = "k9f0a1b2c3d4"
down_revision = "j8e9f0a1b2c3"
branch_labels = None
depends_on = None


def _case_columns() -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns("cases")}


def _case_indexes() -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("cases")}


def upgrade():
    if "case_type_code" not in _case_columns():
        with op.batch_alter_table("cases") as batch_op:
            batch_op.add_column(sa.Column("case_type_code", sa.String(length=80), nullable=True))
    if "ix_cases_case_type_code" not in _case_indexes():
        with op.batch_alter_table("cases") as batch_op:
            batch_op.create_index("ix_cases_case_type_code", ["case_type_code"], unique=False)

    mappings = {
        "实用新型": "utility_utility_model",
        "外观": "utility_design",
        "外观专利": "utility_design",
        "商标": "trademark_trademark",
        "软著": "trademark_software_copyright",
        "软件著作权": "trademark_software_copyright",
        "集成电路": "ic_layout",
        "集成电路布图设计": "ic_layout",
        "其他": "other",
    }
    for legacy, code in mappings.items():
        op.execute(
            sa.text(
                "UPDATE cases SET case_type_code = :code "
                "WHERE (case_type_code IS NULL OR case_type_code = '') "
                "AND REPLACE(TRIM(project_type), ' ', '') = :legacy"
            ).bindparams(code=code, legacy=legacy)
        )


def downgrade():
    if "ix_cases_case_type_code" in _case_indexes():
        with op.batch_alter_table("cases") as batch_op:
            batch_op.drop_index("ix_cases_case_type_code")
    if "case_type_code" in _case_columns():
        with op.batch_alter_table("cases") as batch_op:
            batch_op.drop_column("case_type_code")
