from uuid import uuid4

from app import create_app
from app.assignment_advisor import pending_assignment_cases
from app.case_trace import stamp_intake_owner
from app.extensions import db
from app.models import Case, CaseReviewLog, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase, staff_may_transition_phase


def test_staff_cannot_enter_or_leave_intake_phases():
    assert staff_may_transition_phase(TaskPhase.IN_PROGRESS, TaskPhase.PENDING_ORDER_REVIEW) is False
    assert staff_may_transition_phase(TaskPhase.PENDING_ORDER_REVIEW, TaskPhase.IN_PROGRESS) is False
    assert staff_may_transition_phase(TaskPhase.ORDER_REVISION, TaskPhase.PENDING_REVIEW) is False


def _seed_intake_case(*, suffix: str, title: str, application_no: str):
    admin = User(username=f"_intake_admin_{suffix}", role="admin")
    admin.set_password("secret")
    writer = User(
        username=f"_intake_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    writer.set_password("secret")
    business = User(
        username=f"_intake_biz_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_BUSINESS,
    )
    business.set_password("secret")
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_intake_customer_{suffix}")
    db.session.add_all([admin, writer, business, customer])
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_intake_project_{suffix}")
    db.session.add(project)
    db.session.flush()
    case = Case(
        project_id=project.id,
        title=title,
        application_no=application_no,
        case_type_code="invention_nonrisk_software",
        case_note=f"_intake_note_{suffix}",
    )
    stamp_intake_owner(case, business)
    db.session.add(case)
    db.session.flush()
    task = Task(case_id=case.id, phase_status=TaskPhase.PENDING_ORDER_REVIEW)
    db.session.add(task)
    db.session.commit()
    return {
        "admin_name": admin.username,
        "writer_name": writer.username,
        "business_name": business.username,
        "case_id": case.id,
        "task_id": task.id,
        "title": title,
    }


def test_order_intake_page_lists_cases_and_excludes_writer_review():
    app = create_app()
    suffix = uuid4().hex[:8]
    intake_title = f"_intake_pending_{suffix}"
    review_title = f"_intake_writer_review_{suffix}"
    with app.app_context():
        seeded = _seed_intake_case(
            suffix=suffix,
            title=intake_title,
            application_no=f"OI1{suffix}",
        )
        review_case = Case(
            project_id=db.session.get(Case, seeded["case_id"]).project_id,
            title=review_title,
            application_no=f"OI2{suffix}",
        )
        db.session.add(review_case)
        db.session.flush()
        db.session.add(
            Task(
                case_id=review_case.id,
                phase_status=TaskPhase.PENDING_REVIEW,
            )
        )
        db.session.commit()
        case_id = seeded["case_id"]
        admin_name = seeded["admin_name"]
        writer_name = seeded["writer_name"]
        assert case_id not in [c.id for c in pending_assignment_cases()]

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})

    page = client.get("/admin/order-intake")
    assert page.status_code == 200
    assert "下单待确认".encode() in page.data
    assert intake_title.encode() in page.data
    assert "确认下单".encode() in page.data
    assert review_title.encode() not in page.data

    review_page = client.get("/admin/review-quality")
    assert review_page.status_code == 200
    assert review_title.encode() in review_page.data
    assert intake_title.encode() not in review_page.data

    dashboard = client.get("/admin/dashboard")
    assert dashboard.status_code == 200
    assert "下单待确认".encode() in dashboard.data
    assert b'data-spa-endpoint="admin.order_intake"' in dashboard.data
    assert dashboard.data.count(b"data-order-intake-badge") == 2
    assert b"/admin/order-intake/status" in dashboard.data
    assert 'data-tooltip="任务分派与监控"'.encode() in dashboard.data
    status = client.get("/admin/order-intake/status").get_json()
    assert status["ok"] is True
    assert status["unread"] >= 1
    assert status["total"] == status["unread"]

    client.get("/auth/logout")
    client.post("/auth/login", data={"username": writer_name, "password": "secret"})
    assert client.get("/admin/order-intake").status_code == 403
    assert client.get("/admin/order-intake/status").status_code == 403
    denied = client.post(
        f"/admin/order-intake/{case_id}/action",
        data={"review_action": "approve"},
        follow_redirects=False,
    )
    assert denied.status_code == 403


def test_order_intake_approve_enters_assignment_pool():
    app = create_app()
    suffix = uuid4().hex[:8]
    title = f"_intake_approve_{suffix}"
    with app.app_context():
        seeded = _seed_intake_case(
            suffix=suffix,
            title=title,
            application_no=f"OA1{suffix}",
        )
        case_id = seeded["case_id"]
        task_id = seeded["task_id"]
        admin_name = seeded["admin_name"]
        assert case_id not in [c.id for c in pending_assignment_cases()]

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})
    before = client.get("/admin/order-intake/status").get_json()
    assert before["ok"] is True
    unread_before = before["unread"]
    approved = client.post(
        f"/admin/order-intake/{case_id}/action",
        data={"review_action": "approve"},
        follow_redirects=False,
    )
    assert approved.status_code in (302, 303)

    with app.app_context():
        task = db.session.get(Task, task_id)
        assert task.phase_status == TaskPhase.PENDING_ASSIGNMENT
        assert task.assignee_id is None
        assert case_id in [c.id for c in pending_assignment_cases()]
        assert CaseReviewLog.query.filter_by(case_id=case_id, action="intake_approve").count() == 1

    after_status = client.get("/admin/order-intake/status").get_json()
    assert after_status["unread"] == unread_before - 1
    after = client.get("/admin/order-intake")
    assert title.encode() not in after.data
    assert after.data.count(b"data-order-intake-badge") == 2


def test_order_intake_reject_requires_note_and_leaves_pool():
    app = create_app()
    suffix = uuid4().hex[:8]
    title = f"_intake_reject_{suffix}"
    with app.app_context():
        seeded = _seed_intake_case(
            suffix=suffix,
            title=title,
            application_no=f"OR1{suffix}",
        )
        case_id = seeded["case_id"]
        task_id = seeded["task_id"]
        admin_name = seeded["admin_name"]

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})
    missing_note = client.post(
        f"/admin/order-intake/{case_id}/action",
        data={"review_action": "reject"},
        follow_redirects=False,
    )
    assert missing_note.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.PENDING_ORDER_REVIEW

    rejected = client.post(
        f"/admin/order-intake/{case_id}/action",
        data={"review_action": "reject", "reject_note": "客户名称与合同不一致"},
        follow_redirects=False,
    )
    assert rejected.status_code in (302, 303)
    with app.app_context():
        task = db.session.get(Task, task_id)
        assert task.phase_status == TaskPhase.ORDER_REVISION
        assert case_id not in [c.id for c in pending_assignment_cases()]
        log = CaseReviewLog.query.filter_by(case_id=case_id, action="intake_reject").one()
        assert log.note == "客户名称与合同不一致"

    after = client.get("/admin/order-intake")
    assert title.encode() not in after.data


def test_order_revision_hidden_from_admin_project_case_lists():
    app = create_app()
    suffix = uuid4().hex[:8]
    visible_title = f"_intake_visible_{suffix}"
    revision_title = f"_intake_revision_{suffix}"
    with app.app_context():
        seeded = _seed_intake_case(
            suffix=suffix,
            title=revision_title,
            application_no=f"OH1{suffix}",
        )
        project_id = db.session.get(Case, seeded["case_id"]).project_id
        visible = Case(
            project_id=project_id,
            title=visible_title,
            application_no=f"OH2{suffix}",
            case_type_code="invention_nonrisk_software",
        )
        db.session.add(visible)
        db.session.flush()
        db.session.add(Task(case_id=visible.id, phase_status=TaskPhase.IN_PROGRESS))
        task = db.session.get(Task, seeded["task_id"])
        task.phase_status = TaskPhase.ORDER_REVISION
        db.session.commit()
        admin_name = seeded["admin_name"]

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})

    detail = client.get(f"/admin/project-detail/{project_id}")
    assert detail.status_code == 200
    assert visible_title.encode() in detail.data
    assert revision_title.encode() not in detail.data

    cases_page = client.get(f"/admin/cases?project_id={project_id}")
    assert cases_page.status_code == 200
    assert visible_title.encode() in cases_page.data
    assert revision_title.encode() not in cases_page.data

    all_cases = client.get("/admin/cases")
    assert visible_title.encode() in all_cases.data
    assert revision_title.encode() not in all_cases.data
