"""管理端员工绩效：按职能分组，再按完成案件类型展开。"""

from datetime import datetime, timezone
from uuid import uuid4

from app import create_app
from app.case_trace import add_review_log, stamp_assignee, stamp_process_owner
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def _make_user(*, username: str, role: str, password: str = "secret", **kwargs) -> User:
    user = User(username=username, role=role, **kwargs)
    user.set_password(password)
    db.session.add(user)
    return user


def _section(html: str, function: str) -> str:
    marker = f'data-perf-function="{function}"'
    start = html.find(marker)
    assert start != -1, function
    next_start = html.find('data-perf-function="', start + len(marker))
    return html[start:] if next_start == -1 else html[start:next_start]


def test_staff_performance_groups_by_function_then_case_type():
    app = create_app()
    suffix = uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    with app.app_context():
        admin = _make_user(username=f"_perf_admin_{suffix}", role="admin")
        writer = _make_user(
            username=f"_perf_writer_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_WRITER,
        )
        empty_writer = _make_user(
            username=f"_perf_empty_{suffix}",
            role="staff",
            staff_function=None,
        )
        process = _make_user(
            username=f"_perf_proc_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_PROCESS,
        )
        business = _make_user(
            username=f"_perf_biz_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_BUSINESS,
        )
        idle_process = _make_user(
            username=f"_perf_idle_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_PROCESS,
        )
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_perf_cust_{suffix}")
        db.session.add(customer)
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_perf_proj_{suffix}")
        db.session.add(project)
        db.session.flush()

        invention = Case(
            project_id=project.id,
            title=f"_perf_invention_{suffix}",
            application_no=f"PI{suffix[:8]}",
            case_type_code="invention_nonrisk_unknown_software",
            actual_return_at=now,
        )
        utility = Case(
            project_id=project.id,
            title=f"_perf_utility_{suffix}",
            application_no=f"PU{suffix[:8]}",
            case_type_code="utility_utility_model",
            actual_return_at=now,
        )
        process_case = Case(
            project_id=project.id,
            title=f"_perf_filed_{suffix}",
            application_no=f"PF{suffix[:8]}",
            case_type_code="invention_nonrisk_unknown_mechanical",
            process_owner_id=process.id,
        )
        business_case = Case(
            project_id=project.id,
            title=f"_perf_order_{suffix}",
            application_no=f"PB{suffix[:8]}",
            case_type_code="trademark_trademark",
            intake_owner_id=business.id,
        )
        db.session.add_all([invention, utility, process_case, business_case])
        db.session.flush()
        stamp_process_owner(process_case, process)
        task_i = Task(case_id=invention.id, phase_status=TaskPhase.COMPLETED)
        task_u = Task(case_id=utility.id, phase_status=TaskPhase.IN_PROGRESS)
        task_p = Task(case_id=process_case.id, phase_status=TaskPhase.COMPLETED)
        task_b = Task(case_id=business_case.id, phase_status=TaskPhase.IN_PROGRESS)
        stamp_assignee(task_i, writer)
        stamp_assignee(task_u, empty_writer)
        stamp_assignee(task_p, writer)
        db.session.add_all([task_i, task_u, task_p, task_b])
        db.session.flush()
        add_review_log(
            case_id=process_case.id,
            action="approve",
            operator=admin,
            recipient=writer,
        )
        add_review_log(
            case_id=process_case.id,
            action="process_filed",
            operator=process,
            recipient=writer,
        )
        add_review_log(
            case_id=business_case.id,
            action="intake_approve",
            operator=admin,
            recipient=business,
        )
        add_review_log(
            case_id=invention.id,
            action="writing_reject",
            operator=admin,
            recipient=writer,
            note="补实施例",
        )
        db.session.commit()
        admin_name = admin.username
        writer_name = writer.username
        empty_name = empty_writer.username
        process_name = process.username
        business_name = business.username
        idle_name = idle_process.username

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})
    page = client.get("/admin/staff-performance")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "功能建设中" not in html
    assert "员工绩效" in html
    assert "先按职能看团队" in html

    writer_html = _section(html, "writer")
    process_html = _section(html, "process")
    business_html = _section(html, "business")

    assert writer_name in writer_html
    assert empty_name in writer_html
    assert f"_perf_invention_{suffix}" in writer_html
    assert f"_perf_utility_{suffix}" in writer_html
    assert 'data-perf-type="invention"' in writer_html
    assert 'data-perf-type="utility"' in writer_html
    assert process_name not in writer_html
    assert business_name not in writer_html
    assert f"_perf_filed_{suffix}" not in writer_html
    assert f"_perf_order_{suffix}" not in writer_html

    assert process_name in process_html
    assert idle_name in process_html
    assert f"_perf_filed_{suffix}" in process_html
    assert "交局" in process_html
    assert f"_perf_invention_{suffix}" not in process_html
    assert writer_name not in process_html

    assert business_name in business_html
    assert f"_perf_order_{suffix}" in business_html
    assert 'data-perf-type="trademark"' in business_html
    assert f"_perf_invention_{suffix}" not in business_html

    process_only = client.get("/admin/staff-performance?function=process")
    process_only_html = process_only.get_data(as_text=True)
    assert process_only.status_code == 200
    assert process_name in process_only_html
    assert f"_perf_filed_{suffix}" in process_only_html
    assert f"_perf_invention_{suffix}" not in process_only_html
    assert f"_perf_order_{suffix}" not in process_only_html
    assert 'data-perf-function="writer"' not in process_only_html
    assert 'data-perf-function="business"' not in process_only_html
