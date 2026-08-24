"""phase_a_customer_project_case_task

Revision ID: 3bf7fcc83ff3
Revises:
Create Date: 2026-04-23 10:28:53.535464

"""
from alembic import op
import sqlalchemy as sa


revision = "3bf7fcc83ff3"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "patents" in existing:
        op.drop_table("patents")

    if "customers" not in existing:
        op.create_table(
            "customers",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "projects" not in existing:
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("code", sa.String(length=64), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("due_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["created_by_id"],
                ["users.id"],
            ),
            sa.ForeignKeyConstraint(
                ["customer_id"],
                ["customers.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_projects_customer_id"), "projects", ["customer_id"], unique=False)
        op.create_index(op.f("ix_projects_code"), "projects", ["code"], unique=False)

    if "cases" not in existing:
        op.create_table(
            "cases",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("application_no", sa.String(length=100), nullable=False),
            sa.Column("formal_status", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["project_id"],
                ["projects.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("application_no"),
        )
        op.create_index(op.f("ix_cases_project_id"), "cases", ["project_id"], unique=False)
        op.create_index(op.f("ix_cases_application_no"), "cases", ["application_no"], unique=False)

    if "tasks" not in existing:
        op.create_table(
            "tasks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("case_id", sa.Integer(), nullable=False),
            sa.Column("assignee_id", sa.Integer(), nullable=True),
            sa.Column("phase_status", sa.String(length=40), nullable=False),
            sa.Column("due_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["assignee_id"],
                ["users.id"],
            ),
            sa.ForeignKeyConstraint(
                ["case_id"],
                ["cases.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("case_id"),
        )
        op.create_index(op.f("ix_tasks_assignee_id"), "tasks", ["assignee_id"], unique=False)

    insp = sa.inspect(bind)
    table_names = set(insp.get_table_names())
    ucols = {c["name"] for c in insp.get_columns("users")} if "users" in table_names else set()
    with op.batch_alter_table("users", schema=None) as batch_op:
        if "customer_id" not in ucols:
            batch_op.add_column(sa.Column("customer_id", sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f("ix_users_customer_id"), ["customer_id"], unique=False)
            batch_op.create_foreign_key("fk_users_customer_id", "customers", ["customer_id"], ["id"])
        if "staff_function" not in ucols:
            batch_op.add_column(sa.Column("staff_function", sa.String(length=40), nullable=True))


def downgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        try:
            batch_op.drop_constraint("fk_users_customer_id", type_="foreignkey")
        except Exception:
            pass
        try:
            batch_op.drop_index(batch_op.f("ix_users_customer_id"))
        except Exception:
            pass
        batch_op.drop_column("staff_function")
        batch_op.drop_column("customer_id")

    op.drop_table("tasks")
    op.drop_table("cases")
    op.drop_table("projects")
    op.drop_table("customers")

    op.create_table(
        "patents",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("title", sa.VARCHAR(length=200), nullable=False),
        sa.Column("application_no", sa.VARCHAR(length=100), nullable=False),
        sa.Column("status", sa.VARCHAR(length=50), nullable=True),
        sa.Column("applicant_id", sa.INTEGER(), nullable=False),
        sa.Column("created_at", sa.DATETIME(), nullable=True),
        sa.ForeignKeyConstraint(
            ["applicant_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_no"),
    )
