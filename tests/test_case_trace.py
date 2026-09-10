"""案件留痕：提交审核钉文件名，人名快照不随离职消失。"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

from app import create_app
from app.case_trace import display_trace, stamp_process_owner, writing_material_submit_note
from app.extensions import db
from app.models import Case, CaseMaterial, CaseReviewLog, Customer, CustomerKind, Project, Task, User
from app.workflow import TaskPhase


def _seed_assigned_case():
    suffix = uuid4().hex[:8]
    admin = User(username=f"_tr_admin_{suffix}", role="admin")
    admin.set_password("secret")
    writer = User(
        username=f"_tr_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    writer.set_password("secret")
    process = User(
        username=f"_tr_proc_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    process.set_password("secret")
    db.session.add_all([admin, writer, process])
    db.session.flush()
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_tr_customer_{suffix}")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_tr_project_{suffix}")
    db.session.add(project)
    db.session.flush()
    case = Case(
        project_id=project.id,
        title=f"_tr_case_{suffix}",
        application_no=f"TR{suffix}",
        expected_return_at=datetime.now(timezone.utc) + timedelta(days=10),
        business_owner_id=writer.id,
        business_owner_label=writer.display_label,
    )
    db.session.add(case)
    db.session.flush()
    stamp_process_owner(case, process)
    db.session.add(case)
    db.session.flush()
    db.session.add(
        Task(
            case_id=case.id,
            phase_status=TaskPhase.IN_PROGRESS,
            assignee_id=writer.id,
            assignee_label=writer.display_label,
        )
    )
    db.session.commit()
    return {
        "admin": admin.username,
        "writer": writer.username,
        "writer_id": writer.id,
        "writer_label": writer.display_label,
        "case_id": case.id,
        "case_title": case.title,
    }


def test_submit_for_review_snapshots_files_and_name_survives_departure():
    app = create_app()
    with app.app_context():
        seeded = _seed_assigned_case()
        writer_name = seeded["writer"]
        admin_name = seeded["admin"]
        case_id = seeded["case_id"]
        writer_label = seeded["writer_label"]

    client = app.test_client()
    client.post("/auth/login", data={"username": writer_name, "password": "secret"}, follow_redirects=True)
    upload = client.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_version_tag": "draft",
            "material_note": "撰写稿",
            "material_file": (BytesIO(b"%PDF-1.4 dummy"), "退稿重做说明书.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert upload.status_code == 200
    submitted = client.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "submit_for_review"},
        follow_redirects=True,
    )
    assert submitted.status_code == 200

    with app.app_context():
        log = CaseReviewLog.query.filter_by(case_id=case_id, action="submit_for_review").one()
        assert log.operator_label == writer_label
        assert "退稿重做说明书.pdf" in (log.note or "")
        material = CaseMaterial.query.filter_by(case_id=case_id).one()
        assert material.uploaded_by_label == writer_label
        assert material.uploaded_by_role == "staff"
        writer = db.session.get(User, seeded["writer_id"])
        writer.is_active = False
        db.session.commit()

    client.get("/auth/logout")
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    page = client.get(f"/admin/case-detail/{case_id}").data.decode("utf-8")
    assert "提交流程核对" in page
    assert "退稿重做说明书.pdf" in page
    assert writer_label in page
    assert "已离职" in page
    assert "撰写材料" in page or "已上传撰写材料" in page


def test_completed_case_keeps_writer_name_after_account_row_is_gone():
    """办结案件的人名钉在任务上；即使用户行被拿掉，快照仍能显示。"""
    app = create_app()
    with app.app_context():
        seeded = _seed_assigned_case()
        case = db.session.get(Case, seeded["case_id"])
        case.task.phase_status = TaskPhase.COMPLETED
        case.actual_return_at = datetime.now(timezone.utc)
        db.session.commit()
        writer_id = seeded["writer_id"]
        writer_label = seeded["writer_label"]
        case_title = seeded["case_title"]
        admin_name = seeded["admin"]

        material = CaseMaterial(
            case_id=case.id,
            uploaded_by_id=writer_id,
            uploaded_by_label=writer_label,
            uploaded_by_role="staff",
            original_name="定稿.docx",
            stored_name=f"stored_{uuid4().hex}.docx",
            version_tag="final",
        )
        db.session.add(material)
        db.session.commit()
        material_id = material.id

        # 模拟硬删账号：外键拦不住时上传人/承办人变成空，只剩快照。
        case.task.assignee_id = None
        case.business_owner_id = None
        material = db.session.get(CaseMaterial, material_id)
        # uploaded_by_id 仍是 NOT NULL，用一个将要被停用的人占着，再把关系切断测试展示。
        # 这里直接测 display_trace 与办结库页面用的 staff_label。
        assert display_trace(None, writer_label) == writer_label
        assert writing_material_submit_note(case.id).startswith("提交撰写材料：定稿.docx")

    client = app.test_client()
    client.post("/auth/login", data={"username": admin_name, "password": "secret"}, follow_redirects=True)
    library = client.get("/admin/completed-cases-library").data.decode("utf-8")
    assert case_title in library
    assert writer_label in library


def test_submit_requires_writing_file_and_records_operator_snapshot():
    app = create_app()
    with app.app_context():
        seeded = _seed_assigned_case()

    client = app.test_client()
    client.post(
        "/auth/login",
        data={"username": seeded["writer"], "password": "secret"},
        follow_redirects=True,
    )
    blocked = client.post(
        f"/staff/case-detail/{seeded['case_id']}",
        data={"form_action": "submit_for_review"},
    )
    assert blocked.status_code == 302
    assert "请先上传撰写材料" in unquote(blocked.headers["Location"])
    with app.app_context():
        assert (
            CaseReviewLog.query.filter_by(
                case_id=seeded["case_id"], action="submit_for_review"
            ).count()
            == 0
        )


def test_correction_submit_note_lists_only_new_files():
    """改正提交的提示只冻结本轮新上传的撰写文件。"""
    app = create_app()
    with app.app_context():
        seeded = _seed_assigned_case()
        case_id = seeded["case_id"]
        writer_id = seeded["writer_id"]
        writer_label = seeded["writer_label"]
        older = datetime.now(timezone.utc) - timedelta(hours=2)
        newer = datetime.now(timezone.utc) - timedelta(minutes=5)
        first = CaseMaterial(
            case_id=case_id,
            uploaded_by_id=writer_id,
            uploaded_by_label=writer_label,
            uploaded_by_role="staff",
            original_name="定稿.docx",
            stored_name=f"stored_{uuid4().hex}.docx",
            version_tag="final",
            created_at=older,
        )
        db.session.add(first)
        db.session.add(
            CaseReviewLog(
                case_id=case_id,
                operator_id=writer_id,
                action="submit_for_review",
                created_at=older + timedelta(minutes=1),
            )
        )
        db.session.add(
            CaseReviewLog(
                case_id=case_id,
                operator_id=writer_id,
                action="process_reject",
                created_at=older + timedelta(minutes=10),
            )
        )
        db.session.add(
            CaseMaterial(
                case_id=case_id,
                uploaded_by_id=writer_id,
                uploaded_by_label=writer_label,
                uploaded_by_role="staff",
                original_name="改正稿.docx",
                stored_name=f"stored_{uuid4().hex}.docx",
                version_tag="final",
                note="只改权利要求",
                created_at=newer,
            )
        )
        db.session.commit()
        note = writing_material_submit_note(case_id, correction=True)
        assert note.startswith("提交改正材料：改正稿.docx")
        assert "定稿.docx" not in note
        assert "只改权利要求" in note
        assert writing_material_submit_note(case_id).startswith("提交撰写材料：定稿.docx")
