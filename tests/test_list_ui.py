from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.list_ui import task_phase_tone, task_urgency, task_urgency_row_class
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def test_task_phase_tone_mapping():
    assert task_phase_tone(TaskPhase.PENDING_REVIEW) == "warning"
    assert task_phase_tone(TaskPhase.OVERDUE_IN_PROGRESS) == "danger"
    assert task_phase_tone(TaskPhase.IN_PROGRESS) == "primary"
    assert task_phase_tone(TaskPhase.PENDING_ASSIGNMENT) == "muted"
    assert task_phase_tone(TaskPhase.PENDING_ORDER_REVIEW) == "warning"
    assert task_phase_tone(TaskPhase.ORDER_REVISION) == "muted"


def test_task_urgency_overdue_and_due_soon():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_urgency_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_urgency_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        overdue_case = Case(
            project_id=project.id,
            title=f"_urgency_overdue_{suffix}",
            application_no=f"UO{suffix}",
            expected_return_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        soon_case = Case(
            project_id=project.id,
            title=f"_urgency_soon_{suffix}",
            application_no=f"US{suffix}",
            expected_return_at=datetime.now(timezone.utc) + timedelta(days=2),
        )
        db.session.add_all([overdue_case, soon_case])
        db.session.flush()
        overdue_task = Task(case_id=overdue_case.id, phase_status=TaskPhase.IN_PROGRESS)
        soon_task = Task(case_id=soon_case.id, phase_status=TaskPhase.IN_PROGRESS)
        db.session.add_all([overdue_task, soon_task])
        db.session.commit()

        assert task_urgency(overdue_task) == "overdue"
        assert task_urgency_row_class(overdue_task) == "qy-row-overdue"
        assert task_urgency(soon_task) == "due_soon"
        assert task_urgency_row_class(soon_task) == "qy-row-due-soon"


def test_admin_cases_list_renders_phase_badges():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_list_ui_admin_{suffix}", role="admin")
        admin.set_password("secret")
        db.session.add(admin)
        db.session.flush()
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_list_ui_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_list_ui_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(
            project_id=project.id,
            title=f"_list_ui_case_{suffix}",
            application_no=f"LI{suffix}",
            case_type_code="utility_utility_model",
        )
        db.session.add(case)
        db.session.flush()
        db.session.add(Task(case_id=case.id, phase_status=TaskPhase.PENDING_REVIEW))
        db.session.commit()
        admin_name = admin.username

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})
    page = client.get("/admin/cases")
    assert page.status_code == 200
    assert b"qy-phase-badge" in page.data
    assert b"qy-phase-badge--warning" in page.data
    assert b"qy-case-type-badge" in page.data
