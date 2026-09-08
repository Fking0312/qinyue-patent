from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Case, CaseReviewLog, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def test_admin_review_inbox_unread_filters_and_actions():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        admin = User(username=f"_review_inbox_admin_{suffix}", role="admin")
        admin.set_password("secret")
        staff = User(username=f"_review_inbox_staff_{suffix}", role="staff")
        staff.set_password("secret")
        customer = Customer(kind=CustomerKind.COMPANY, name=f"_review_inbox_customer_{suffix}")
        db.session.add_all([admin, staff, customer])
        db.session.flush()
        project = Project(customer_id=customer.id, name=f"_review_inbox_project_{suffix}")
        db.session.add(project)
        db.session.flush()
        case = Case(
            project_id=project.id,
            title=f"_review_inbox_case_{suffix}",
            application_no=f"RI1{suffix}",
            case_type_code="invention_nonrisk_software",
            business_owner_id=staff.id,
            case_note=f"_review_inbox_note_{suffix}",
        )
        db.session.add(case)
        db.session.flush()
        task = Task(
            case_id=case.id,
            assignee_id=staff.id,
            phase_status=TaskPhase.PENDING_REVIEW,
            updated_at=datetime.now(timezone.utc),
        )
        db.session.add(task)
        db.session.commit()
        admin_name = admin.username
        staff_name = staff.username
        staff_id = staff.id
        project_id = project.id
        case_id = case.id
        task_id = task.id

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"})

    dashboard = client.get("/admin/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.data.count(b"data-review-unread-badge") == 2

    before = client.get("/admin/review-quality/status").get_json()
    assert before["ok"] is True
    assert before["unread"] >= 1
    pending_before = before["unread"]

    page = client.get(
        f"/admin/review-quality?q=_review_inbox_case_{suffix}"
        f"&case_type_primary=invention&assignee_id={staff_id}"
    )
    assert page.status_code == 200
    assert f"_review_inbox_case_{suffix}".encode() in page.data
    assert f"_review_inbox_note_{suffix}".encode() in page.data
    assert "审核通过".encode() in page.data
    assert "打回".encode() in page.data

    after_open = client.get("/admin/review-quality/status").get_json()
    assert after_open["unread"] >= 1

    approved = client.post(
        f"/admin/review-quality/{case_id}/action",
        data={"review_action": "approve"},
        follow_redirects=False,
    )
    assert approved.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.PENDING_SUBMIT
        assert CaseReviewLog.query.filter_by(case_id=case_id, action="approve").count() == 1

    after_approve = client.get("/admin/review-quality/status").get_json()
    assert after_approve["unread"] == pending_before - 1

    with app.app_context():
        second_case = Case(
            project_id=project_id,
            title=f"_review_inbox_reject_{suffix}",
            application_no=f"RI2{suffix}",
            case_type_code="utility_utility_model",
            business_owner_id=staff_id,
        )
        db.session.add(second_case)
        db.session.flush()
        second_task = Task(
            case_id=second_case.id,
            assignee_id=staff_id,
            phase_status=TaskPhase.PENDING_REVIEW,
            updated_at=datetime.now(timezone.utc) + timedelta(seconds=2),
        )
        db.session.add(second_task)
        db.session.commit()
        second_case_id = second_case.id

    new_status = client.get("/admin/review-quality/status").get_json()
    assert new_status["unread"] == after_approve["unread"] + 1

    rejected = client.post(
        f"/admin/review-quality/{second_case_id}/action",
        data={"review_action": "reject", "reject_note": "请补充技术效果说明"},
        follow_redirects=False,
    )
    assert rejected.status_code in (302, 303)
    after_reject = client.get("/admin/review-quality/status").get_json()
    assert after_reject["unread"] == after_approve["unread"]
    with app.app_context():
        rejected_case = db.session.get(Case, second_case_id)
        assert rejected_case.task.phase_status == TaskPhase.IN_PROGRESS
        log = CaseReviewLog.query.filter_by(case_id=second_case_id, action="reject").one()
        assert log.note == "请补充技术效果说明"

    client.get("/auth/logout")
    client.post("/auth/login", data={"username": staff_name, "password": "secret"})
    assert client.get("/admin/review-quality").status_code == 403
    assert client.get("/admin/review-quality/status").status_code == 403

    notification_status = client.get("/staff/notifications/status").get_json()
    assert notification_status["ok"] is True
    assert notification_status["unread"] == 2

    approved_case_page = client.get(f"/staff/case-detail/{case_id}")
    assert approved_case_page.status_code == 200
    assert b'data-upload-locked="true"' in approved_case_page.data
    assert "材料上传已锁定".encode() in approved_case_page.data

    staff_dashboard = client.get("/staff/dashboard")
    assert b'id="qyStaffNotificationBadge"' in staff_dashboard.data
    assert b">2</span>" in staff_dashboard.data

    notification_page = client.get("/staff/notifications")
    assert notification_page.status_code == 200
    assert "_review_inbox_reject_".encode() in notification_page.data
    assert "请补充技术效果说明".encode() in notification_page.data
    assert "案件审核被打回".encode() in notification_page.data
    assert "案件审核已通过".encode() in notification_page.data
    assert "待递交".encode() in notification_page.data

    after_read = client.get("/staff/notifications/status").get_json()
    assert after_read["unread"] == 0
    with app.app_context():
        notice = CaseReviewLog.query.filter_by(
            case_id=second_case_id,
            action="reject",
            recipient_id=staff_id,
        ).one()
        assert notice.read_at is not None
        approved_notice = CaseReviewLog.query.filter_by(
            case_id=case_id,
            action="approve",
            recipient_id=staff_id,
        ).one()
        assert approved_notice.read_at is not None
