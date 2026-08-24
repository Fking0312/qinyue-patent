from datetime import datetime, timezone
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def test_admin_dashboard_renders_stat_cards():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_dash_admin_{suffix}", role="admin")
        admin.set_password("secret")
        db.session.add(admin)
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_dash_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_dash_project_{suffix}")
        db.session.add(project)
        db.session.flush()

        pending_case = Case(
            project_id=project.id,
            title=f"_dash_pending_{suffix}",
            application_no=f"DP1{suffix}",
        )
        unassigned_case = Case(
            project_id=project.id,
            title=f"_dash_unassigned_{suffix}",
            application_no=f"DP2{suffix}",
        )
        db.session.add_all([pending_case, unassigned_case])
        db.session.flush()
        db.session.add_all(
            [
                Task(case_id=pending_case.id, phase_status=TaskPhase.PENDING_REVIEW),
                Task(case_id=unassigned_case.id, phase_status=TaskPhase.PENDING_ASSIGNMENT),
            ]
        )
        db.session.commit()
        admin_name = admin.username

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})
    page = client.get("/admin/dashboard")
    assert page.status_code == 200
    assert "待审核".encode() in page.data
    assert "本月新建".encode() in page.data
    assert "已超期".encode() in page.data
    assert "待分配".encode() in page.data
    assert b"qy-dash-stat-card" in page.data
    assert b'data-spa-endpoint="admin.review_quality"' in page.data
    assert "期限提醒".encode("utf-8") in page.data


def test_staff_dashboard_renders_stat_cards():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        staff = User(username=f"_dash_staff_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add(staff)
        db.session.flush()

        customer = Customer(kind=CustomerKind.COMPANY, name=f"_dash_staff_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_dash_staff_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(
            project_id=project.id,
            title=f"_dash_staff_case_{suffix}",
            application_no=f"DS1{suffix}",
            expected_return_at=datetime(2099, 12, 31, tzinfo=timezone.utc),
        )
        db.session.add(case)
        db.session.flush()
        db.session.add(
            Task(
                case_id=case.id,
                assignee_id=staff.id,
                phase_status=TaskPhase.IN_PROGRESS,
            )
        )
        db.session.commit()
        staff_name = staff.username

    client = app.test_client()
    client.post("/auth/login", data={"username": staff_name, "password": "secret"})
    page = client.get("/staff/dashboard")
    assert page.status_code == 200
    assert "撰写中".encode() in page.data
    assert "待审核".encode() in page.data
    assert "未读消息".encode() in page.data
    assert "期限关注".encode() in page.data
    assert b"qy-dash-stat-card" in page.data
    assert b'data-spa-endpoint="staff.task_board"' in page.data
    assert b'data-spa-endpoint="staff.notifications"' in page.data
