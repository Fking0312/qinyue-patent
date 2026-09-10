"""流程人员官文跟进：上传、转交、回执、催办与权限边界。"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

from app import create_app
from app.case_trace import stamp_assignee, stamp_process_owner
from app.extensions import db
from app.models import Case, CaseMaterial, CaseReviewLog, Customer, CustomerKind, OfficialNotice, Project, Task, User
from app.workflow import TaskPhase


def _make_user(*, username: str, role: str, password: str = "secret", **kwargs) -> User:
    user = User(username=username, role=role, **kwargs)
    user.set_password(password)
    db.session.add(user)
    return user


def _seed(*, suffix: str, phase: str = TaskPhase.IN_PROGRESS):
    admin = _make_user(username=f"_on_admin_{suffix}", role="admin")
    writer = _make_user(
        username=f"_on_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    process = _make_user(
        username=f"_on_proc_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    other_process = _make_user(
        username=f"_on_proc2_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    business = _make_user(
        username=f"_on_biz_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_BUSINESS,
    )
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_on_cust_{suffix}")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_on_proj_{suffix}")
    db.session.add(project)
    db.session.flush()
    case = Case(
        project_id=project.id,
        title=f"_on_case_{suffix}",
        application_no=f"ON1{suffix}",
        case_type_code="invention_nonrisk_software",
    )
    stamp_process_owner(case, process)
    db.session.add(case)
    db.session.flush()
    task = Task(case_id=case.id, phase_status=phase)
    stamp_assignee(task, writer)
    db.session.add(task)
    db.session.commit()
    return {
        "admin_name": admin.username,
        "writer_name": writer.username,
        "writer_id": writer.id,
        "process_name": process.username,
        "process_id": process.id,
        "other_process_name": other_process.username,
        "business_name": business.username,
        "case_id": case.id,
        "task_id": task.id,
        "title": case.title,
        "project_id": project.id,
    }


def _login(app, username: str):
    client = app.test_client()
    client.post("/auth/login", data={"username": username, "password": "secret"})
    return client


def _toast(response) -> str:
    return unquote(response.headers.get("Location", ""))


def _upload(
    client,
    case_id: int,
    *,
    notice_type: str = "oa1",
    filename: str = "oa1.pdf",
    official_due_at: str = "2099-12-31",
):
    return client.post(
        f"/staff/process-cases/{case_id}",
        data={
            "form_action": "upload_notice",
            "notice_type": notice_type,
            "official_due_at": official_due_at,
            "notice_file": (BytesIO(b"%PDF-1.4 official-notice"), filename),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def _forward(client, case_id: int, notice_id: int):
    return client.post(
        f"/staff/process-cases/{case_id}",
        data={
            "form_action": "forward_notice",
            "notice_id": str(notice_id),
            "internal_due_at": "2099-12-31",
        },
        follow_redirects=False,
    )


def test_only_process_owner_can_upload_forward_and_writer_receipt():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, phase=TaskPhase.PENDING_REVIEW)
        case_id = seeded["case_id"]
        task_id = seeded["task_id"]
        title = seeded["title"]

    process = _login(app, seeded["process_name"])
    writer = _login(app, seeded["writer_name"])
    admin = _login(app, seeded["admin_name"])
    other_process = _login(app, seeded["other_process_name"])
    business = _login(app, seeded["business_name"])

    home = process.get("/staff/process-dashboard")
    assert home.status_code == 200
    home_text = home.data.decode("utf-8")
    assert "案件跟进" in home_text
    assert "功能建设中" not in home_text
    assert "马上处理" in home_text

    case_page = process.get(f"/staff/process-cases/{case_id}")
    assert case_page.status_code == 200
    assert title.encode("utf-8") in case_page.data
    assert "上传官方来文".encode("utf-8") in case_page.data
    assert b'name="official_due_at" required' in case_page.data

    missing_due = _upload(process, case_id, official_due_at="")
    assert missing_due.status_code in (302, 303)
    assert "请填写官方期限" in _toast(missing_due)
    with app.app_context():
        assert OfficialNotice.query.filter_by(case_id=case_id).count() == 0

    assert writer.get(f"/staff/process-cases/{case_id}").status_code == 403
    assert admin.get(f"/staff/process-cases/{case_id}").status_code == 403
    assert business.get(f"/staff/process-cases/{case_id}").status_code == 403
    assert other_process.get(f"/staff/process-cases/{case_id}").status_code == 404
    assert _upload(writer, case_id).status_code == 403
    assert _upload(admin, case_id).status_code == 403
    assert _upload(other_process, case_id).status_code == 404
    biz_inbox = business.get("/staff/notifications")
    assert biz_inbox.status_code == 200
    assert "官方来文" not in biz_inbox.data.decode("utf-8")

    uploaded = _upload(process, case_id, filename="一通.pdf")
    assert uploaded.status_code in (302, 303)

    with app.app_context():
        notice = OfficialNotice.query.filter_by(case_id=case_id).one()
        notice_id = notice.id
        assert notice.notice_type == OfficialNotice.TYPE_OA1
        assert notice.forwarded_at is None
        assert notice.needs_writer_reply is True

    dash = process.get("/staff/process-dashboard")
    assert "马上处理".encode("utf-8") in dash.data
    assert title.encode("utf-8") in dash.data
    pending = process.get("/staff/process-followup?queue=pending_forward")
    assert title.encode("utf-8") in pending.data

    writer_before = writer.get(f"/staff/case-detail/{case_id}")
    assert writer_before.status_code == 200
    assert "一通.pdf" not in writer_before.data.decode("utf-8")
    assert "暂无官方来文" in writer_before.data.decode("utf-8")
    assert writer.get(f"/staff/official-notices/{notice_id}").status_code == 404

    admin_detail = admin.get(f"/admin/case-detail/{case_id}")
    assert admin_detail.status_code == 200
    assert "一通.pdf" in admin_detail.data.decode("utf-8")
    admin_dl = admin.get(f"/admin/official-notices/{notice_id}")
    assert admin_dl.status_code == 200
    with app.app_context():
        assert db.session.get(OfficialNotice, notice_id).received_at is None

    forwarded = _forward(process, case_id, notice_id)
    assert forwarded.status_code in (302, 303)
    with app.app_context():
        notice = db.session.get(OfficialNotice, notice_id)
        assert notice.forwarded_at is not None
        assert notice.forwarded_to_id == seeded["writer_id"]
        assert notice.received_at is None
        task = db.session.get(Task, task_id)
        assert task.phase_status == TaskPhase.IN_PROGRESS
        log = CaseReviewLog.query.filter_by(
            case_id=case_id, action="official_forward", recipient_id=seeded["writer_id"]
        ).one()
        assert "一通" in (log.note or "")

    writer_after = writer.get(f"/staff/case-detail/{case_id}")
    writer_after_text = writer_after.data.decode("utf-8")
    assert "一通.pdf" in writer_after_text
    assert "尚未下载" in writer_after_text
    assert 'data-qy-notice-download' in writer_after_text
    inbox = writer.get("/staff/notifications")
    inbox_text = inbox.data.decode("utf-8")
    assert "官方来文已转交" in inbox_text
    assert "查看官文" in inbox_text
    assigned_only = writer.get("/staff/notifications?type=assigned")
    assert "官方来文已转交" not in assigned_only.data.decode("utf-8")
    official_only = writer.get("/staff/notifications?type=official")
    assert "官方来文已转交" in official_only.data.decode("utf-8")

    first_dl = writer.get(f"/staff/official-notices/{notice_id}")
    assert first_dl.status_code == 200
    with app.app_context():
        first_received = db.session.get(OfficialNotice, notice_id).received_at
        assert first_received is not None
    writer_received = writer.get(f"/staff/case-detail/{case_id}")
    writer_received_text = writer_received.data.decode("utf-8")
    assert "已接收" in writer_received_text
    assert "尚未下载" not in writer_received_text
    second_dl = writer.get(f"/staff/official-notices/{notice_id}")
    assert second_dl.status_code == 200
    with app.app_context():
        assert db.session.get(OfficialNotice, notice_id).received_at == first_received

    blocked = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "delete_notice", "notice_id": str(notice_id)},
        follow_redirects=False,
    )
    assert blocked.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(OfficialNotice, notice_id) is not None


def test_urge_delete_queues_and_non_reply_types():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        submit_case = Case(
            project_id=seeded["project_id"],
            title=f"_on_submit_{suffix}",
            application_no=f"ON2{suffix}",
        )
        stamp_process_owner(submit_case, db.session.get(User, seeded["process_id"]))
        db.session.add(submit_case)
        db.session.flush()
        db.session.add(Task(case_id=submit_case.id, phase_status=TaskPhase.PENDING_SUBMIT))
        db.session.commit()
        case_id = seeded["case_id"]
        submit_title = submit_case.title
        writer_id = seeded["writer_id"]

    process = _login(app, seeded["process_name"])
    writer = _login(app, seeded["writer_name"])

    acceptance = _upload(process, case_id, notice_type="acceptance", filename="受理.pdf")
    assert acceptance.status_code in (302, 303)
    oa = _upload(process, case_id, notice_type="oa1", filename="一通.pdf")
    assert oa.status_code in (302, 303)

    with app.app_context():
        notices = {item.notice_type: item for item in OfficialNotice.query.filter_by(case_id=case_id)}
        acceptance_id = notices[OfficialNotice.TYPE_ACCEPTANCE].id
        oa_id = notices[OfficialNotice.TYPE_OA1].id

    pending = process.get("/staff/process-followup?queue=pending_forward")
    pending_text = pending.data.decode("utf-8")
    assert seeded["title"] in pending_text
    dash = process.get("/staff/process-dashboard").data.decode("utf-8")
    assert "待转交" in dash

    with app.app_context():
        from app.official_notices import QUEUE_PENDING_FORWARD, process_queue_counts

        counts = process_queue_counts(seeded["process_id"])
        assert counts[QUEUE_PENDING_FORWARD] == 1

    too_soon_urge = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "urge_notice", "notice_id": str(oa_id)},
        follow_redirects=False,
    )
    assert too_soon_urge.status_code in (302, 303)
    assert "请先转交" in _toast(too_soon_urge)

    assert _forward(process, case_id, oa_id).status_code in (302, 303)
    early_urge = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "urge_notice", "notice_id": str(oa_id)},
        follow_redirects=False,
    )
    assert early_urge.status_code in (302, 303)
    assert "转交未满一天" in _toast(early_urge)

    with app.app_context():
        notice = db.session.get(OfficialNotice, oa_id)
        notice.forwarded_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.session.commit()

    urged = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "urge_notice", "notice_id": str(oa_id)},
        follow_redirects=False,
    )
    assert urged.status_code in (302, 303)
    assert "已催办" in _toast(urged)
    with app.app_context():
        assert (
            CaseReviewLog.query.filter_by(
                case_id=case_id, action="official_urge", recipient_id=writer_id
            ).count()
            == 1
        )

    deleted = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "delete_notice", "notice_id": str(acceptance_id)},
        follow_redirects=False,
    )
    assert deleted.status_code in (302, 303)
    assert "已删除" in _toast(deleted)
    with app.app_context():
        assert db.session.get(OfficialNotice, acceptance_id) is None

    submit_queue = process.get("/staff/process-followup?queue=pending_submit")
    submit_text = submit_queue.data.decode("utf-8")
    assert submit_title in submit_text
    assert seeded["title"] not in submit_text

    writer_case = writer.get(f"/staff/case-detail/{case_id}").data.decode("utf-8")
    assert "一通.pdf" in writer_case
    assert "受理.pdf" not in writer_case


def test_writer_can_upload_reply_after_receiving_oa_or_amendment():
    """交局后的审查意见/补正：撰写师接收后可再上传改正材料，供流程再次递交官方。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, phase=TaskPhase.IN_PROGRESS)
        case_id = seeded["case_id"]
        task_id = seeded["task_id"]

    writer = _login(app, seeded["writer_name"])
    process = _login(app, seeded["process_name"])

    first = writer.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"first spec"), f"first_{suffix}.txt"),
            "material_version_tag": "final",
            "material_note": "初稿",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert first.status_code in (302, 303)
    assert writer.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "submit_for_review"},
        follow_redirects=False,
    ).status_code in (302, 303)
    assert process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "process_accept"},
        follow_redirects=False,
    ).status_code in (302, 303)
    assert process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "process_filed"},
        follow_redirects=False,
    ).status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.OFFICE_ACTION

    locked = writer.get(f"/staff/case-detail/{case_id}")
    assert b'data-upload-locked="true"' in locked.data

    assert _upload(process, case_id, notice_type="oa1", filename="一通.pdf").status_code in (302, 303)
    with app.app_context():
        oa_id = OfficialNotice.query.filter_by(case_id=case_id, notice_type=OfficialNotice.TYPE_OA1).one().id

    assert _forward(process, case_id, oa_id).status_code in (302, 303)
    before_receipt = writer.get(f"/staff/case-detail/{case_id}")
    before_text = before_receipt.data.decode("utf-8")
    assert "一通.pdf" in before_text
    assert b'data-upload-locked="true"' in before_receipt.data
    assert "请先下载并接收" in before_text
    blocked = writer.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"too early"), f"early_{suffix}.txt"),
            "material_version_tag": "final",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert "请先下载并接收" in _toast(blocked)

    assert writer.get(f"/staff/official-notices/{oa_id}").status_code == 200
    with app.app_context():
        notice = db.session.get(OfficialNotice, oa_id)
        assert notice.received_at is not None
        assert db.session.get(Task, task_id).phase_status == TaskPhase.IN_PROGRESS

    received_page = writer.get(f"/staff/case-detail/{case_id}")
    received_text = received_page.data.decode("utf-8")
    assert b'data-upload-locked="true"' not in received_page.data
    assert "改正材料上传" in received_text
    assert "提交改正材料" in received_text
    assert b'qy-submit-review-btn' in received_page.data
    assert b'disabled' in received_page.data

    premature = writer.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "submit_for_review"},
        follow_redirects=False,
    )
    assert "请先上传针对官方来文的改正材料" in _toast(premature)

    reply = writer.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"oa reply spec"), f"reply_oa_{suffix}.txt"),
            "material_version_tag": "final",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert reply.status_code in (302, 303)
    assert "已上传" in _toast(reply)
    reply_inbox = process.get("/staff/notifications").data.decode("utf-8")
    assert "撰写师已上传改正材料" in reply_inbox
    assert f"reply_oa_{suffix}.txt" in reply_inbox
    pending_process = process.get(f"/staff/process-cases/{case_id}").data.decode("utf-8")
    assert "尚未提交核对" in pending_process
    submitted = writer.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "submit_for_review"},
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.PENDING_REVIEW
        notes = [m.note for m in CaseMaterial.query.filter_by(case_id=case_id).all()]
        assert any(note and "审查意见" in note for note in notes)
        revised_log = CaseReviewLog.query.filter_by(
            case_id=case_id, action="writing_revised"
        ).order_by(CaseReviewLog.id.desc()).first()
        assert revised_log is not None
        assert revised_log.recipient_id == seeded["process_id"]
        submit_logs = (
            CaseReviewLog.query.filter_by(case_id=case_id, action="submit_for_review")
            .order_by(CaseReviewLog.id.asc())
            .all()
        )
        assert len(submit_logs) >= 2
        latest_note = submit_logs[-1].note or ""
        assert f"reply_oa_{suffix}.txt" in latest_note
        assert f"first_{suffix}.txt" not in latest_note

    process_inbox = process.get("/staff/notifications").data.decode("utf-8")
    assert "改正材料已提交" in process_inbox
    assert "提交改正材料" in process_inbox
    process_page = process.get(f"/staff/process-cases/{case_id}").data.decode("utf-8")
    assert f"reply_oa_{suffix}.txt" in process_page
    assert "本次新上传" in process_page
    assert "改正材料" in process_page or "待递交官方" in process_page
    assert process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "process_accept"},
        follow_redirects=False,
    ).status_code in (302, 303)
    assert process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "process_filed"},
        follow_redirects=False,
    ).status_code in (302, 303)

    assert _upload(process, case_id, notice_type="amendment", filename="补正.pdf").status_code in (302, 303)
    with app.app_context():
        amd_id = OfficialNotice.query.filter_by(
            case_id=case_id, notice_type=OfficialNotice.TYPE_AMENDMENT
        ).one().id
    assert _forward(process, case_id, amd_id).status_code in (302, 303)
    assert writer.get(f"/staff/official-notices/{amd_id}").status_code == 200
    with app.app_context():
        assert db.session.get(OfficialNotice, amd_id).received_at is not None
    amd_page = writer.get(f"/staff/case-detail/{case_id}")
    assert b'data-upload-locked="true"' not in amd_page.data
    assert "补正通知书" in amd_page.data.decode("utf-8")
    assert writer.post(
        f"/staff/case-detail/{case_id}",
        data={
            "form_action": "upload_material",
            "material_file": (BytesIO(b"amendment reply"), f"reply_amd_{suffix}.txt"),
            "material_version_tag": "final",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    ).status_code in (302, 303)
    assert writer.post(
        f"/staff/case-detail/{case_id}",
        data={"form_action": "submit_for_review"},
        follow_redirects=False,
    ).status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, task_id).phase_status == TaskPhase.PENDING_REVIEW
        names = [m.original_name for m in CaseMaterial.query.filter_by(case_id=case_id).all()]
        assert f"reply_amd_{suffix}.txt" in names
