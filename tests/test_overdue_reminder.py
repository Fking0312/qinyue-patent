"""超期提醒：列表范围、展示与状态同步。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.overdue_reminder import refresh_all_open_tasks_overdue, reminder_lists, tasks_for_reminder_scope
from app.workflow import TaskPhase


def test_reminder_syncs_overdue_and_lists_for_admin():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC1-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"RP1-{suffix}", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        app_no = f"CN209900000001.{suffix}"
        case = Case(project_id=proj.id, title="Case A", application_no=app_no)
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
        db.session.add(task)
        admin = User(username=f"_rem_admin_{suffix}", role="admin")
        admin.set_password("x")
        db.session.add(admin)
        db.session.commit()
        task_id = task.id

        overdue, soon = reminder_lists(admin)
        mine = [t for t in overdue if t.id == task_id]
        assert len(mine) == 1
        assert mine[0].phase_status == TaskPhase.OVERDUE_IN_PROGRESS
        # 管理员可见全局任务，数据库中可能有其他临期数据
        assert not any(t.id == task_id for t in soon)


def test_staff_sees_only_assigned_tasks():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC2-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"RP2-{suffix}", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case B", application_no=f"CN209900000002.{suffix}")
        db.session.add(case)
        db.session.flush()
        staff_a = User(username=f"_rem_staff_a_{suffix}", role="staff")
        staff_a.set_password("x")
        staff_b = User(username=f"_rem_staff_b_{suffix}", role="staff")
        staff_b.set_password("x")
        db.session.add_all([staff_a, staff_b])
        db.session.flush()
        t_a = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, assignee_id=staff_a.id)
        db.session.add(t_a)
        db.session.commit()

        assert len(tasks_for_reminder_scope(staff_a)) == 1
        assert len(tasks_for_reminder_scope(staff_b)) == 0


def test_client_sees_customer_project_tasks():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC3-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"RP3-{suffix}", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case C", application_no=f"CN209900000003.{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.PENDING_REVIEW)
        db.session.add(task)
        client_u = User(username=f"_rem_client_{suffix}", role="client", customer_id=cust.id)
        client_u.set_password("x")
        db.session.add(client_u)
        db.session.commit()
        task_id = task.id

        overdue, _ = reminder_lists(client_u)
        mine = [t for t in overdue if t.id == task_id]
        assert len(mine) == 0
        assert db.session.get(Task, task_id).phase_status == TaskPhase.PENDING_REVIEW


def test_due_soon_not_overdue_yet():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC4-{suffix}")
        db.session.add(cust)
        db.session.flush()
        due = datetime.now(timezone.utc) + timedelta(days=3)
        proj = Project(customer_id=cust.id, name=f"RP4-{suffix}", due_at=due)
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case D", application_no=f"CN209900000004.{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
        db.session.add(task)
        admin = User(username=f"_rem_admin2_{suffix}", role="admin")
        admin.set_password("x")
        db.session.add(admin)
        db.session.commit()
        task_id = task.id

        _, soon = reminder_lists(admin, due_soon_days=7)
        mine = [t for t in soon if t.id == task_id]
        assert len(mine) == 1


def test_case_expected_return_overrides_project_due_for_overdue():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC4B-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"RP4B-{suffix}", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case = Case(
            project_id=proj.id,
            title="Case D2",
            application_no=f"CN209900000004B.{suffix}",
            expected_return_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.OVERDUE_IN_PROGRESS)
        db.session.add(task)
        admin = User(username=f"_rem_admin4b_{suffix}", role="admin")
        admin.set_password("x")
        db.session.add(admin)
        db.session.commit()
        task_id = task.id

        overdue, soon = reminder_lists(admin, due_soon_days=7)
        task = db.session.get(Task, task_id)
        assert task is not None
        assert task.phase_status == TaskPhase.IN_PROGRESS
        assert not any(t.id == task_id for t in overdue)
        assert not any(t.id == task_id for t in soon)


def test_authorized_pending_payment_and_completed_do_not_become_overdue():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC4C-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"RP4C-{suffix}", due_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case_a = Case(project_id=proj.id, title="Case D3", application_no=f"CN209900000004C.{suffix}")
        case_b = Case(project_id=proj.id, title="Case D4", application_no=f"CN209900000004D.{suffix}")
        db.session.add_all([case_a, case_b])
        db.session.flush()
        pending_payment = Task(case_id=case_a.id, phase_status=TaskPhase.AUTHORIZED_PENDING_PAYMENT)
        completed = Task(case_id=case_b.id, phase_status=TaskPhase.COMPLETED)
        admin = User(username=f"_rem_admin4c_{suffix}", role="admin")
        admin.set_password("x")
        db.session.add_all([pending_payment, completed, admin])
        db.session.commit()
        pending_payment_id = pending_payment.id
        completed_id = completed.id

        overdue, soon = reminder_lists(admin, due_soon_days=7)
        pending_payment = db.session.get(Task, pending_payment_id)
        completed = db.session.get(Task, completed_id)

        assert pending_payment is not None
        assert completed is not None
        assert pending_payment.phase_status == TaskPhase.AUTHORIZED_PENDING_PAYMENT
        assert completed.phase_status == TaskPhase.COMPLETED
        assert not any(t.id in {pending_payment_id, completed_id} for t in overdue)
        assert not any(t.id == completed_id for t in soon)


def test_refresh_all_open_tasks_cli_path():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC5-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"RP5-{suffix}", due_at=datetime(2019, 1, 1, tzinfo=timezone.utc))
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case E", application_no=f"CN209900000005.{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.DRAFT)
        db.session.add(task)
        db.session.commit()
        task_id = task.id

        n = refresh_all_open_tasks_overdue()
        assert n >= 1
        task = db.session.get(Task, task_id)
        assert task is not None
        assert task.phase_status == TaskPhase.OVERDUE_DRAFT


def test_dashboard_renders_reminder_block():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        staff = User(username=f"_rem_dash_staff_{suffix}", role="staff")
        staff.set_password("sec")
        db.session.add(staff)
        db.session.commit()
        uname = staff.username

    client = app.test_client()
    client.post("/auth/login", data={"username": uname, "password": "sec"}, follow_redirects=True)
    r = client.get("/staff/dashboard")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "暂无超期或临期任务" in text or "qy-reminder-table" in text
