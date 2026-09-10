"""撰写 → 流程核对 → 交局 → 终审办结。"""

from io import BytesIO
from uuid import uuid4

from app import create_app
from app.case_trace import stamp_assignee, stamp_process_owner
from app.extensions import db
from app.models import Case, CaseReviewLog, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def _seed(suffix: str):
    admin = User(username=f"_pw_admin_{suffix}", role="admin")
    admin.set_password("secret")
    writer = User(
        username=f"_pw_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    writer.set_password("secret")
    process = User(
        username=f"_pw_proc_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    process.set_password("secret")
    db.session.add_all([admin, writer, process])
    db.session.flush()
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_pw_cust_{suffix}")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_pw_proj_{suffix}")
    db.session.add(project)
    db.session.flush()
    case = Case(
        project_id=project.id,
        title=f"_pw_case_{suffix}",
        application_no=f"PW{suffix}",
    )
    stamp_process_owner(case, process)
    db.session.add(case)
    db.session.flush()
    task = Task(case_id=case.id, phase_status=TaskPhase.IN_PROGRESS)
    stamp_assignee(task, writer)
    db.session.add(task)
    db.session.commit()
    return {
        "admin": admin.username,
        "writer": writer.username,
        "process": process.username,
        "case_id": case.id,
        "task_id": task.id,
        "process_id": process.id,
        "writer_id": writer.id,
    }


def _login(app, username: str):
    client = app.test_client()
    client.post("/auth/login", data={"username": username, "password": "secret"})
    return client


def test_writer_submit_process_file_and_admin_complete():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix)
        case_id = seeded["case_id"]
        task_id = seeded["task_id"]

    writer = _login(app, seeded["writer"])
    process = _login(app, seeded["process"])
    admin = _login(app, seeded["admin"])

    upload = writer.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"draft body"), f"spec_{suffix}.txt"),
            "material_version_tag": "final",
            "material_note": "说明书",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert upload.status_code in (302, 303)

    submitted = writer.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "submit_for_review"},
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)
    with app.app_context():
        assert CaseReviewLog.query.filter_by(case_id=case_id, action="writing_revised").count() == 0
        assert db.session.get(Task, task_id).phase_status == TaskPhase.PENDING_REVIEW
        log = CaseReviewLog.query.filter_by(case_id=case_id, action="submit_for_review").one()
        assert log.recipient_id == seeded["process_id"]

    inbox = process.get("/staff/notifications")
    inbox_text = inbox.data.decode("utf-8")
    assert "撰写材料已提交" in inbox_text
    assert "说明书" in inbox_text
    case_page = process.get(f"/staff/process-cases/{case_id}")
    assert case_page.status_code == 200
    case_text = case_page.data.decode("utf-8")
    assert f"spec_{suffix}.txt" in case_text
    assert "说明书" in case_text
    assert "请查看文件备注后核对" in case_text
    assert b'form_action" value="process_accept"' in case_page.data

    dash = process.get("/staff/process-dashboard")
    dash_text = dash.data.decode("utf-8")
    assert f"_pw_case_{suffix}" in dash_text
    assert "说明书" in dash_text

    rejected = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "process_reject", "reject_note": "请补权利要求"},
        follow_redirects=False,
    )
    assert rejected.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.IN_PROGRESS

    revised = writer.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"fixed claims"), f"fixed_{suffix}.txt"),
            "material_version_tag": "final",
            "material_note": "已补权利要求",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert revised.status_code in (302, 303)
    revised_inbox = process.get("/staff/notifications").data.decode("utf-8")
    assert "撰写师已上传改正材料" in revised_inbox
    assert "已补权利要求" in revised_inbox
    pending_page = process.get(f"/staff/process-cases/{case_id}").data.decode("utf-8")
    assert "尚未提交核对" in pending_page
    assert "已补权利要求" in pending_page

    writer.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "submit_for_review"},
        follow_redirects=False,
    )
    with app.app_context():
        submit_logs = (
            CaseReviewLog.query.filter_by(case_id=case_id, action="submit_for_review")
            .order_by(CaseReviewLog.id.asc())
            .all()
        )
        assert len(submit_logs) == 2
        first_note = submit_logs[0].note or ""
        latest_note = submit_logs[1].note or ""
        assert f"spec_{suffix}.txt" in first_note
        assert "说明书" in first_note
        assert f"fixed_{suffix}.txt" in latest_note
        assert "已补权利要求" in latest_note
        assert f"spec_{suffix}.txt" not in latest_note
        assert "说明书" not in latest_note
    submitted_inbox = process.get("/staff/notifications").data.decode("utf-8")
    assert "改正材料已提交" in submitted_inbox
    assert "提交改正材料" in submitted_inbox
    assert "本次新上传" in process.get(f"/staff/process-cases/{case_id}").data.decode("utf-8")
    dash_after = process.get("/staff/process-dashboard").data.decode("utf-8")
    assert "已补权利要求" in dash_after
    assert f"spec_{suffix}.txt" not in dash_after
    accepted = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "process_accept"},
        follow_redirects=False,
    )
    assert accepted.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.PENDING_SUBMIT

    filed = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "process_filed"},
        follow_redirects=False,
    )
    assert filed.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.OFFICE_ACTION

    too_early_admin = admin.post(
        f"/admin/case-detail/{case_id}",
        data={"review_action": "approve"},
        follow_redirects=False,
    )
    assert too_early_admin.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.OFFICE_ACTION

    final = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert final.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.PENDING_FINAL_REVIEW

    inbox_admin = admin.get("/admin/review-quality")
    assert f"_pw_case_{suffix}".encode("utf-8") in inbox_admin.data

    completed = admin.post(
        f"/admin/review-quality/{case_id}/action",
        data={"review_action": "approve"},
        follow_redirects=False,
    )
    assert completed.status_code in (302, 303)
    with app.app_context():
        task = db.session.get(Task, task_id)
        assert task.phase_status == TaskPhase.COMPLETED
        case = db.session.get(Case, case_id)
        assert case.actual_return_at is not None

    writer_note = writer.get("/staff/notifications")
    assert "终审已通过".encode("utf-8") in writer_note.data
    assert "已办结".encode("utf-8") in writer_note.data
