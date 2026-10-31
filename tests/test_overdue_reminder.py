"""超期提醒：列表范围、展示与状态同步。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.overdue_reminder import (
    case_detail_endpoint_for_role,
    refresh_all_open_tasks_overdue,
    reminder_lists,
    reminder_template_kwargs,
    tasks_for_reminder_scope,
)
from app.workflow import (
    TaskPhase,
    effective_task_due_at,
    effective_task_phase,
    is_task_past_due,
)


def test_reminder_lists_overdue_without_mutating_phase():
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
        assert mine[0].phase_status == TaskPhase.IN_PROGRESS
        assert db.session.get(Task, task_id).phase_status == TaskPhase.IN_PROGRESS
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
        assert task.phase_status == TaskPhase.OVERDUE_IN_PROGRESS
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
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
        db.session.add(task)
        db.session.commit()
        task_id = task.id

        n = refresh_all_open_tasks_overdue()
        assert n >= 1
        task = db.session.get(Task, task_id)
        assert task is not None
        assert task.phase_status == TaskPhase.OVERDUE_IN_PROGRESS


def test_case_detail_endpoint_for_role():
    assert case_detail_endpoint_for_role("admin") == "admin.case_detail"
    assert case_detail_endpoint_for_role("staff") == "staff.case_detail_by_id"
    assert case_detail_endpoint_for_role("client") == "client.case_detail"
    assert case_detail_endpoint_for_role("unknown") is None


def test_reminder_template_kwargs_includes_case_detail_endpoint():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        staff = User(username=f"_rem_kw_staff_{suffix}", role="staff")
        staff.set_password("x")
        db.session.add(staff)
        db.session.commit()

        kwargs = reminder_template_kwargs(staff)
        assert kwargs["case_detail_endpoint"] == "staff.case_detail_by_id"


def test_staff_deadline_reminder_page_renders_calendar():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        staff = User(username=f"_rem_cal_staff_{suffix}", role="staff")
        staff.set_password("sec")
        db.session.add(staff)
        db.session.flush()
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC-cal-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"RP-cal-{suffix}")
        db.session.add(proj)
        db.session.flush()
        case = Case(
            project_id=proj.id,
            title="Cal Case",
            application_no=f"CNCAL{suffix}",
            expected_return_at=datetime(2099, 8, 15, 15, 0, tzinfo=timezone.utc),
        )
        db.session.add(case)
        db.session.flush()
        task = Task(
            case_id=case.id,
            phase_status=TaskPhase.IN_PROGRESS,
            assignee_id=staff.id,
        )
        db.session.add(task)
        db.session.commit()
        uname = staff.username

    client = app.test_client()
    client.post("/auth/login", data={"username": uname, "password": "sec"}, follow_redirects=True)
    page = client.get("/staff/deadline-reminder?cal_year=2099&cal_month=8&day=2099-08-15")
    assert page.status_code == 200
    text = page.data.decode("utf-8")
    assert "2099 年 8 月截止安排" in text
    assert "qy-deadline-cal-day--future" in text or "qy-deadline-cal-day--marked" in text
    assert "DEADLINE SCHEDULE" in text
    assert "回到今天" in text
    assert "本月截止" in text
    assert "qy-deadline-calendar-agenda" in text
    assert "1 项截止" in text
    assert "Cal Case" in text


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
    assert "qy-deadline-block--overdue" in text
    assert "qy-deadline-block--due-soon" in text
    assert "暂无超期任务" in text and "暂无临期任务" in text
    assert "qy-deadline-calendar-card--mini" in text
    assert "qy-deadline-calendar-card--readonly" in text
    assert "DEADLINE SCHEDULE" in text
    assert "完整日历" in text
    assert "qy-deadline-calendar-nav__arrow" not in text
    assert "qy-deadline-cal-grid is-compact is-readonly" in text


def test_parse_calendar_month_falls_back_on_invalid_values():
    from app.deadline_calendar import parse_calendar_month

    now_cn = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
    assert parse_calendar_month("abc", "99") == (now_cn.year, now_cn.month)
    assert parse_calendar_month("1999", "0") == (now_cn.year, now_cn.month)
    assert parse_calendar_month("2101", "13") == (now_cn.year, now_cn.month)
    assert parse_calendar_month("2026", "9") == (2026, 9)


def test_reminder_calendar_skips_gated_review_phases():
    from app.deadline_calendar import reminder_calendar_context

    app = create_app()
    suffix = uuid4().hex[:8]
    due = datetime(2088, 4, 3, 4, 0, tzinfo=timezone.utc)
    with app.app_context():
        admin = User(username=f"_cal_gate_admin_{suffix}", role="admin")
        admin.set_password("x")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_cal_gate_c_{suffix}")
        db.session.add_all([admin, customer])
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_cal_gate_p_{suffix}", due_at=due)
        db.session.add(project)
        db.session.flush()
        open_case = Case(
            project_id=project.id,
            title=f"_cal_open_{suffix}",
            application_no=f"CG1{suffix}",
            expected_return_at=due,
        )
        gated_case = Case(
            project_id=project.id,
            title=f"_cal_gated_{suffix}",
            application_no=f"CG2{suffix}",
            expected_return_at=due,
        )
        db.session.add_all([open_case, gated_case])
        db.session.flush()
        db.session.add_all(
            [
                Task(case_id=open_case.id, phase_status=TaskPhase.IN_PROGRESS),
                Task(case_id=gated_case.id, phase_status=TaskPhase.PENDING_REVIEW),
            ]
        )
        db.session.commit()

        ctx = reminder_calendar_context(admin, 2088, 4)
        titles = [task.case.title for task in ctx["days_by_iso"].get("2088-04-03", {}).get("tasks", [])]
        assert f"_cal_open_{suffix}" in titles
        assert f"_cal_gated_{suffix}" not in titles


def test_task_due_at_overrides_case_and_project_due():
    """任务自身截止时间优先于案件应返稿与项目截止时间。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC6-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(
            customer_id=cust.id,
            name=f"RP6-{suffix}",
            due_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db.session.add(proj)
        db.session.flush()
        case = Case(
            project_id=proj.id,
            title="Case F",
            application_no=f"CN209900000006.{suffix}",
            expected_return_at=datetime.now(timezone.utc) + timedelta(days=20),
        )
        db.session.add(case)
        db.session.flush()
        # 任务自身截止已过期，优先于案件/项目更晚的截止
        task = Task(
            case_id=case.id,
            phase_status=TaskPhase.IN_PROGRESS,
            due_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.session.add(task)
        admin = User(username=f"_rem_admin6_{suffix}", role="admin")
        admin.set_password("x")
        db.session.add(admin)
        db.session.commit()
        task_id = task.id

        overdue, soon = reminder_lists(admin)
        assert any(t.id == task_id for t in overdue)
        assert not any(t.id == task_id for t in soon)


def test_due_soon_outside_window_not_listed():
    """截止时间超过临期窗口的任务既不进超期也不进临期。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC6B-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(
            customer_id=cust.id,
            name=f"RP6B-{suffix}",
            due_at=datetime.now(timezone.utc) + timedelta(days=8),
        )
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case F2", application_no=f"CN209900000006B.{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
        db.session.add(task)
        admin = User(username=f"_rem_admin6b_{suffix}", role="admin")
        admin.set_password("x")
        db.session.add(admin)
        db.session.commit()
        task_id = task.id

        overdue, soon = reminder_lists(admin, due_soon_days=7)
        assert not any(t.id == task_id for t in overdue)
        assert not any(t.id == task_id for t in soon)


def test_overdue_phase_mapping_and_gated_phases():
    """可超期阶段映射到对应超期态；待审核/待终审不因超期改写。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC7-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"RP7-{suffix}")
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case G", application_no=f"CN209900000007.{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(
            case_id=case.id,
            phase_status=TaskPhase.IN_PROGRESS,
            due_at=now - timedelta(days=1),
        )
        db.session.add(task)
        db.session.commit()

        base_to_overdue = {
            TaskPhase.IN_PROGRESS: TaskPhase.OVERDUE_IN_PROGRESS,
            TaskPhase.PENDING_SUBMIT: TaskPhase.OVERDUE_PENDING_SUBMIT,
            TaskPhase.OFFICE_ACTION: TaskPhase.OVERDUE_OFFICE_ACTION,
            TaskPhase.ON_HOLD: TaskPhase.OVERDUE_ON_HOLD,
        }
        for base, expected in base_to_overdue.items():
            task.phase_status = base
            assert effective_task_phase(task, now=now) == expected

        for gated in (TaskPhase.PENDING_REVIEW, TaskPhase.PENDING_FINAL_REVIEW):
            task.phase_status = gated
            assert effective_task_phase(task, now=now) == gated


def test_reminder_lists_sorted_by_effective_due_date():
    """超期列表按有效截止时间升序排列。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_rem_admin8_{suffix}", role="admin")
        admin.set_password("x")
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC8-{suffix}")
        db.session.add_all([admin, cust])
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"RP8-{suffix}")
        db.session.add(proj)
        db.session.flush()
        now = datetime.now(timezone.utc)
        dues = [now - timedelta(days=5), now - timedelta(days=1), now - timedelta(days=3)]
        ids = []
        for i, due in enumerate(dues):
            case = Case(
                project_id=proj.id,
                title=f"Case H{i}",
                application_no=f"CN209900000008{i}.{suffix}",
            )
            db.session.add(case)
            db.session.flush()
            task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS, due_at=due)
            db.session.add(task)
            db.session.flush()
            ids.append(task.id)
        db.session.commit()

        overdue, _ = reminder_lists(admin)
        mine = [t for t in overdue if t.id in ids]
        due_dates = [effective_task_due_at(t) for t in mine]
        assert due_dates == sorted(due_dates)


def test_is_task_past_due_is_strict():
    """临界点：截止时间恰好等于当前时间不算超期。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        cust = Customer(kind=CustomerKind.COMPANY, name=f"RC9-{suffix}")
        db.session.add(cust)
        db.session.flush()
        proj = Project(customer_id=cust.id, name=f"RP9-{suffix}")
        db.session.add(proj)
        db.session.flush()
        case = Case(project_id=proj.id, title="Case I", application_no=f"CN209900000009.{suffix}")
        db.session.add(case)
        db.session.flush()
        task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
        db.session.add(task)
        db.session.commit()

        due = datetime.now(timezone.utc)
        task.due_at = due
        assert is_task_past_due(task, now=due) is False
        assert is_task_past_due(task, now=due + timedelta(seconds=1)) is True
