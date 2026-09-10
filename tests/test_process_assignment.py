"""指定流程人员：跟进负载排序、待指定池口径，以及页面落库。"""

from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from uuid import uuid4

from app import create_app
from app.assignment_advisor import (
    pending_process_assignment_cases,
    process_load_rows,
)
from app.case_trace import stamp_process_owner
from app.extensions import db
from app.models import Case, CaseReviewLog, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase

INVENTION = "invention_nonrisk_normal_mechanical"
UTILITY = "utility_utility_model"


def _seed():
    suffix = uuid4().hex[:8]
    admin = User(username=f"_pa_admin_{suffix}", role="admin")
    admin.set_password("secret")
    writer = User(username=f"_pa_writer_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_WRITER)
    business = User(username=f"_pa_biz_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_BUSINESS)
    free = User(username=f"_pa_p_free_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_PROCESS)
    mid = User(username=f"_pa_p_mid_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_PROCESS)
    busy = User(username=f"_pa_p_busy_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_PROCESS)
    departed = User(username=f"_pa_p_left_{suffix}", role="staff", staff_function=User.STAFF_FUNCTION_PROCESS)
    departed.is_active = False
    for user in (writer, business, free, mid, busy, departed):
        user.set_password("secret")
    db.session.add_all([admin, writer, business, free, mid, busy, departed])
    db.session.flush()

    customer = Customer(kind=CustomerKind.COMPANY, name=f"_pa_customer_{suffix}")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_pa_project_{suffix}")
    db.session.add(project)
    db.session.flush()

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=10)
    inside = now + timedelta(days=5)
    outside = now + timedelta(days=90)

    counter = {"n": 0}
    ids: dict[str, int] = {}

    def add_case(
        name,
        *,
        code,
        due,
        writer_user=None,
        process_user=None,
        phase=TaskPhase.IN_PROGRESS,
    ):
        counter["n"] += 1
        case = Case(
            project_id=project.id,
            title=f"_pa_{name}_{suffix}",
            application_no=f"PA{counter['n']:02d}{suffix}",
            case_type_code=code,
            expected_return_at=due,
            business_owner_id=writer_user.id if writer_user else None,
        )
        db.session.add(case)
        db.session.flush()
        if process_user is not None:
            stamp_process_owner(case, process_user)
        db.session.add(
            Task(
                case_id=case.id,
                phase_status=phase,
                assignee_id=writer_user.id if writer_user else None,
            )
        )
        ids[name] = case.id
        return case

    add_case("free_far_a", code=INVENTION, due=outside, writer_user=writer, process_user=free)
    add_case("free_far_b", code=INVENTION, due=outside, writer_user=writer, process_user=free)
    add_case("free_done", code=INVENTION, due=inside, writer_user=writer, process_user=free, phase=TaskPhase.COMPLETED)
    add_case("mid_one", code=UTILITY, due=inside, writer_user=writer, process_user=mid)
    add_case("busy_one", code=INVENTION, due=inside, writer_user=writer, process_user=busy)
    add_case("busy_two", code=INVENTION, due=inside, writer_user=writer, process_user=busy)

    target = add_case(
        "target", code=INVENTION, due=window_end, writer_user=writer, phase=TaskPhase.IN_PROGRESS
    )
    undated = add_case("undated", code=UTILITY, due=None, writer_user=writer)
    intake = add_case(
        "intake",
        code=UTILITY,
        due=inside,
        phase=TaskPhase.PENDING_ORDER_REVIEW,
    )
    completed = add_case(
        "completed",
        code=UTILITY,
        due=inside,
        writer_user=writer,
        phase=TaskPhase.COMPLETED,
    )
    departed_case = add_case(
        "departed",
        code=UTILITY,
        due=inside,
        writer_user=writer,
        process_user=departed,
    )
    db.session.commit()

    return {
        "admin": admin.username,
        "writer_id": writer.id,
        "writer_name": writer.username,
        "business_id": business.id,
        "business_name": business.username,
        "process_staff": [free, mid, busy],
        "free_id": free.id,
        "free_name": free.username,
        "mid_name": mid.username,
        "busy_name": busy.username,
        "departed_name": departed.username,
        "target_id": target.id,
        "target_title": target.title,
        "undated_id": undated.id,
        "intake_id": intake.id,
        "completed_id": completed.id,
        "departed_id": departed_case.id,
        "window_end": window_end,
        "case_ids": ids,
    }


def test_process_ranking_counts_only_followups_due_inside_the_window():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        rows = process_load_rows(
            seeded["process_staff"],
            window_end=seeded["window_end"],
            exclude_case_id=seeded["target_id"],
        )
        order = [row["user"].username for row in rows]
        assert order[0] == seeded["free_name"]
        assert order[-1] == seeded["busy_name"]

        by_name = {row["user"].username: row for row in rows}
        free_row = by_name[seeded["free_name"]]
        assert free_row["window_workload"] == 0.0
        assert free_row["open_count"] == 2
        assert by_name[seeded["busy_name"]]["window_workload"] == 6.0


def test_pending_process_pool_excludes_intake_and_completed():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        ids = [case.id for case in pending_process_assignment_cases()]
        assert seeded["target_id"] in ids
        assert seeded["undated_id"] in ids
        assert seeded["departed_id"] in ids
        assert seeded["intake_id"] not in ids
        assert seeded["completed_id"] not in ids
        assert seeded["case_ids"]["busy_one"] not in ids
        assert ids.index(seeded["departed_id"]) < ids.index(seeded["target_id"])


def test_process_assignment_page_ranks_process_staff_and_hides_other_functions():
    app = create_app()
    with app.app_context():
        seeded = _seed()

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    r = client.get(f"/admin/process-assignment?case_id={seeded['target_id']}")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "流程人员建议顺序" in text
    assert 'data-qy-scroll-key="smart-pending"' in text
    assert seeded["target_title"] in text
    assert seeded["free_name"] in text
    assert seeded["busy_name"] in text
    assert f'value="{seeded["writer_id"]}"' not in text
    assert f'value="{seeded["business_id"]}"' not in text
    assert seeded["business_name"] not in text
    assert text.index(seeded["free_name"]) < text.index(seeded["busy_name"])


def test_process_assignment_page_still_ranks_without_due_date():
    app = create_app()
    with app.app_context():
        seeded = _seed()

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    r = client.get(f"/admin/process-assignment?case_id={seeded['undated_id']}")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "按在办总负载" in text
    assert "窗口内负载" in text
    assert seeded["free_name"] in text


def test_assigning_process_owner_does_not_change_writer_or_phase():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        target_id = seeded["target_id"]
        writer_id = seeded["writer_id"]
        free_id = seeded["free_id"]

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    r = client.post(
        "/admin/process-assignment/assign",
        data={"case_id": target_id, "process_owner_id": free_id},
        follow_redirects=True,
    )
    assert r.status_code == 200

    with app.app_context():
        case = db.session.get(Case, target_id)
        assert case.process_owner_id == free_id
        assert case.process_owner_label
        assert case.business_owner_id == writer_id
        assert case.task.assignee_id == writer_id
        assert case.task.phase_status == TaskPhase.IN_PROGRESS
        assert target_id not in [c.id for c in pending_process_assignment_cases()]
        log = CaseReviewLog.query.filter_by(
            case_id=target_id, action="process_assigned", recipient_id=free_id
        ).one()
        assert log.recipient_id == free_id


def test_process_page_refuses_writer_and_intake_cases():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        target_id = seeded["target_id"]
        writer_id = seeded["writer_id"]
        intake_id = seeded["intake_id"]
        free_id = seeded["free_id"]

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    refused = client.post(
        "/admin/process-assignment/assign",
        data={"case_id": target_id, "process_owner_id": writer_id},
        follow_redirects=False,
    )
    assert refused.status_code in (302, 303)
    assert "仅在职流程人员可指定为流程负责人" in unquote(refused.headers.get("Location", ""))

    intake = client.post(
        "/admin/process-assignment/assign",
        data={"case_id": intake_id, "process_owner_id": free_id},
        follow_redirects=False,
    )
    assert intake.status_code in (302, 303)
    assert "下单待确认" in unquote(intake.headers.get("Location", ""))


def test_departed_process_owner_can_be_replaced():
    app = create_app()
    with app.app_context():
        seeded = _seed()
        departed_id = seeded["departed_id"]
        free_id = seeded["free_id"]

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["admin"], "password": "secret"},
        follow_redirects=True,
    )
    page = client.get(f"/admin/process-assignment?case_id={departed_id}")
    assert page.status_code == 200
    assert "原流程已离岗" in page.data.decode("utf-8")

    r = client.post(
        "/admin/process-assignment/assign",
        data={"case_id": departed_id, "process_owner_id": free_id},
        follow_redirects=True,
    )
    assert r.status_code == 200
    with app.app_context():
        case = db.session.get(Case, departed_id)
        assert case.process_owner_id == free_id
        assert case.task.phase_status == TaskPhase.IN_PROGRESS


def test_process_assignment_page_denies_non_admin():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        staff = User(
            username=f"_pa_staff_{suffix}",
            role="staff",
            staff_function=User.STAFF_FUNCTION_WRITER,
        )
        staff.set_password("secret")
        db.session.add(staff)
        db.session.commit()
        name = staff.username

    client = app.test_client()
    client.post("/auth/login", data={"username": name, "password": "secret"}, follow_redirects=True)
    r = client.get("/admin/process-assignment")
    assert r.status_code == 403
