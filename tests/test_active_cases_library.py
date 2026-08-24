"""办结案件库 · 在办案件列表。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app import create_app
from app.active_cases_library import active_cases_library_data, completed_cases_library_data
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def _seed_active_library():
    suffix = uuid4().hex[:8]
    admin = User(username=f"_acl_admin_{suffix}", role="admin")
    admin.set_password("secret")
    staff_a = User(username=f"_acl_staff_a_{suffix}", role="staff")
    staff_a.set_password("secret")
    staff_b = User(username=f"_acl_staff_b_{suffix}", role="staff")
    staff_b.set_password("secret")
    db.session.add_all([admin, staff_a, staff_b])
    db.session.flush()

    customer = Customer(kind=CustomerKind.COMPANY, name=f"_acl_customer_{suffix}")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_acl_project_{suffix}")
    db.session.add(project)
    db.session.flush()

    soon = datetime.now(timezone.utc) + timedelta(days=2)
    later = datetime.now(timezone.utc) + timedelta(days=10)

    case_soon = Case(
        project_id=project.id,
        title=f"_acl_soon_{suffix}",
        application_no=f"CNSOON{suffix}",
        business_owner_id=staff_a.id,
        expected_return_at=soon,
    )
    case_later = Case(
        project_id=project.id,
        title=f"_acl_later_{suffix}",
        application_no=f"CNLATER{suffix}",
        business_owner_id=staff_b.id,
        expected_return_at=later,
    )
    case_done = Case(
        project_id=project.id,
        title=f"_acl_done_{suffix}",
        application_no=f"CNDONE{suffix}",
        business_owner_id=staff_a.id,
        expected_return_at=soon,
    )
    case_unassigned = Case(
        project_id=project.id,
        title=f"_acl_unassigned_{suffix}",
        application_no=f"CNUA{suffix}",
        expected_return_at=soon,
    )
    db.session.add_all([case_soon, case_later, case_done, case_unassigned])
    db.session.flush()
    db.session.add_all(
        [
            Task(case_id=case_soon.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff_a.id),
            Task(case_id=case_later.id, phase_status=TaskPhase.PENDING_REVIEW, assignee_id=staff_b.id),
            Task(case_id=case_done.id, phase_status=TaskPhase.COMPLETED, assignee_id=staff_a.id),
            Task(case_id=case_unassigned.id, phase_status=TaskPhase.PENDING_ASSIGNMENT),
        ]
    )
    db.session.commit()
    return {
        "suffix": suffix,
        "admin": admin.username,
        "staff_a_id": staff_a.id,
        "case_soon_title": case_soon.title,
        "case_later_title": case_later.title,
        "case_done_title": case_done.title,
        "case_unassigned_title": case_unassigned.title,
    }


def test_active_cases_library_lists_assigned_incomplete_sorted_by_deadline():
    app = create_app()
    with app.app_context():
        seeded = _seed_active_library()
        data = active_cases_library_data(sort_by="deadline", group_by="none")
        titles = [row["case"].title for row in data["rows"]]
        assert seeded["case_soon_title"] in titles
        assert seeded["case_later_title"] in titles
        assert seeded["case_done_title"] not in titles
        assert seeded["case_unassigned_title"] not in titles
        soon_idx = titles.index(seeded["case_soon_title"])
        later_idx = titles.index(seeded["case_later_title"])
        assert soon_idx < later_idx

        by_staff = active_cases_library_data(staff_id=seeded["staff_a_id"])
        staff_titles = {row["case"].title for row in by_staff["rows"]}
        assert seeded["case_soon_title"] in staff_titles
        assert seeded["case_later_title"] not in staff_titles


def test_active_cases_library_page_renders():
    app = create_app()
    with app.app_context():
        seeded = _seed_active_library()
        admin_name = seeded["admin"]

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r = client.get("/admin/active-cases-library")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "在办案件" in text
    assert seeded["case_soon_title"] in text
    assert seeded["case_done_title"] not in text
    assert "负责员工" in text
    assert "按客户分类" in text


def test_completed_cases_library_lists_all_completed():
    app = create_app()
    with app.app_context():
        seeded = _seed_active_library()
        data = completed_cases_library_data()
        titles = {row["case"].title for row in data["rows"]}
        assert seeded["case_done_title"] in titles
        assert seeded["case_soon_title"] not in titles
        assert seeded["case_unassigned_title"] not in titles

        admin_name = seeded["admin"]

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    r = client.get("/admin/completed-cases-library")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "办结案件" in text
    assert seeded["case_done_title"] in text
    assert seeded["case_soon_title"] not in text
    assert "实际返稿时间" in text


def test_completed_case_can_fill_missing_actual_return_and_complete_requires_it():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_fill_admin_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_fill_staff_{suffix}", role="staff")
        staff.set_password("secret")
        db.session.add_all([admin, staff])
        db.session.flush()
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_fill_customer_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_fill_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        done = Case(
            project_id=project.id,
            title=f"_fill_done_{suffix}",
            application_no=f"CNFILL{suffix}",
            business_owner_id=staff.id,
            actual_return_at=None,
        )
        open_case = Case(
            project_id=project.id,
            title=f"_fill_open_{suffix}",
            application_no=f"CNOPEN{suffix}",
            business_owner_id=staff.id,
        )
        db.session.add_all([done, open_case])
        db.session.flush()
        db.session.add_all(
            [
                Task(case_id=done.id, phase_status=TaskPhase.COMPLETED, assignee_id=staff.id),
                Task(case_id=open_case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff.id),
            ]
        )
        db.session.commit()
        admin_name = admin.username
        done_id = done.id
        open_id = open_case.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)

    from urllib.parse import unquote

    blocked = client.post(
        f"/admin/case-detail/{open_id}",
        data={"form_action": "update_phase", "phase_status": TaskPhase.COMPLETED},
        follow_redirects=False,
    )
    assert blocked.status_code in (302, 303)
    location = unquote(blocked.headers.get("Location") or "")
    assert "qy_toast" in location
    assert "实际返稿时间" in location
    with app.app_context():
        assert db.session.get(Case, open_id).task.phase_status == TaskPhase.IN_PROGRESS
        assert db.session.get(Case, open_id).actual_return_at is None

    filled = client.post(
        "/admin/completed-cases-library/set-actual-return",
        data={
            "case_id": str(done_id),
            "mode": "manual",
            "actual_return_at": "2026-08-01T15:30",
            "missing_return": "1",
        },
        follow_redirects=False,
    )
    assert filled.status_code in (302, 303)
    with app.app_context():
        saved_actual_return = db.session.get(Case, done_id).actual_return_at
        assert saved_actual_return is not None

    auto_again = client.post(
        "/admin/completed-cases-library/set-actual-return",
        data={"case_id": str(done_id), "mode": "auto"},
        follow_redirects=False,
    )
    assert auto_again.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Case, done_id).actual_return_at == saved_actual_return

    completed = client.post(
        f"/admin/case-detail/{open_id}",
        data={
            "form_action": "update_phase",
            "phase_status": TaskPhase.COMPLETED,
            "actual_return_at": "2026-08-02T10:00",
        },
        follow_redirects=False,
    )
    assert completed.status_code in (302, 303)
    with app.app_context():
        case = db.session.get(Case, open_id)
        assert case.task.phase_status == TaskPhase.COMPLETED
        assert case.actual_return_at is not None
