"""管理端官方期限看板：按已上传官文的官方期限监控，并可催办当前负责人。"""

from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from uuid import uuid4

from app import create_app
from app.case_trace import stamp_assignee, stamp_process_owner
from app.extensions import db
from app.models import Case, CaseReviewLog, Customer, CustomerKind, OfficialNotice, Project, Task, User
from app.official_notices import parse_beijing_date
from app.statutory_deadlines import display_admin_urge_note
from app.workflow import TaskPhase


def _make_user(*, username: str, role: str, password: str = "secret", **kwargs) -> User:
    user = User(username=username, role=role, **kwargs)
    user.set_password(password)
    db.session.add(user)
    return user


def _due_offset(days: int) -> datetime:
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).date()
    return parse_beijing_date((today + timedelta(days=days)).isoformat())


def _seed(*, suffix: str, phase: str = TaskPhase.IN_PROGRESS):
    admin = _make_user(username=f"_sd_admin_{suffix}", role="admin")
    writer = _make_user(
        username=f"_sd_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    process = _make_user(
        username=f"_sd_proc_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_sd_cust_{suffix}")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_sd_proj_{suffix}")
    db.session.add(project)
    db.session.flush()
    case = Case(
        project_id=project.id,
        title=f"_sd_case_{suffix}",
        application_no=f"SD1{suffix}",
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
        "case_id": case.id,
        "title": case.title,
    }


def _add_notice(
    *,
    case_id: int,
    uploader_id: int,
    suffix: str,
    notice_type: str,
    due_days: int,
    forwarded_to_id: int | None = None,
    forwarded: bool = False,
    received: bool = False,
    fee_paid: bool = False,
) -> OfficialNotice:
    notice = OfficialNotice(
        case_id=case_id,
        notice_type=notice_type,
        original_name=f"{notice_type}.pdf",
        stored_name=f"sd_{suffix}_{notice_type}_{uuid4().hex[:8]}.pdf",
        official_due_at=_due_offset(due_days),
        uploaded_by_id=uploader_id,
    )
    if forwarded or received:
        notice.forwarded_to_id = forwarded_to_id
        notice.forwarded_at = datetime.now(timezone.utc) - timedelta(days=2)
    if received:
        notice.received_at = datetime.now(timezone.utc) - timedelta(days=1)
    if fee_paid:
        notice.fee_paid_at = datetime.now(timezone.utc)
        notice.fee_paid_proof_original_name = "proof.pdf"
        notice.fee_paid_proof_stored_name = f"sd_proof_{suffix}_{uuid4().hex[:8]}.pdf"
    db.session.add(notice)
    db.session.commit()
    return notice


def _login(app, username: str):
    client = app.test_client()
    client.post("/auth/login", data={"username": username, "password": "secret"})
    return client


def _toast(response) -> str:
    return unquote(response.headers.get("Location", ""))


def test_display_admin_urge_note_strips_prefix():
    assert display_admin_urge_note("#N12 请尽快办理「缴费通知」：待官方缴费。") == (
        "请尽快办理「缴费通知」：待官方缴费。"
    )
    assert display_admin_urge_note("普通备注") == "普通备注"


def test_statutory_deadline_lists_open_notices_and_hides_paid_or_distant():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        _add_notice(
            case_id=seeded["case_id"],
            uploader_id=seeded["process_id"],
            suffix=suffix,
            notice_type=OfficialNotice.TYPE_FEE,
            due_days=-2,
        )
        _add_notice(
            case_id=seeded["case_id"],
            uploader_id=seeded["process_id"],
            suffix=f"{suffix}paid",
            notice_type=OfficialNotice.TYPE_FEE,
            due_days=-1,
            fee_paid=True,
        )
        _add_notice(
            case_id=seeded["case_id"],
            uploader_id=seeded["process_id"],
            suffix=f"{suffix}oa",
            notice_type=OfficialNotice.TYPE_OA1,
            due_days=2,
        )
        later = _add_notice(
            case_id=seeded["case_id"],
            uploader_id=seeded["process_id"],
            suffix=f"{suffix}later",
            notice_type=OfficialNotice.TYPE_OA2,
            due_days=30,
        )
        admin_name = seeded["admin_name"]
        title = seeded["title"]
        later_id = later.id

    client = _login(app, admin_name)
    page = client.get("/admin/statutory-deadline")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "官方期限管理" in html
    assert title in html
    assert "待官方缴费" in html
    assert "待转交撰写师" in html
    assert "催办" in html
    assert f'data-notice-id="{later_id}"' not in html

    all_page = client.get("/admin/statutory-deadline?window=all")
    assert f'data-notice-id="{later_id}"' in all_page.get_data(as_text=True)


def test_statutory_deadline_pending_review_has_no_urge():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, phase=TaskPhase.PENDING_REVIEW)
        notice = _add_notice(
            case_id=seeded["case_id"],
            uploader_id=seeded["process_id"],
            suffix=suffix,
            notice_type=OfficialNotice.TYPE_OA1,
            due_days=1,
            forwarded_to_id=seeded["writer_id"],
            forwarded=True,
            received=True,
        )
        admin_name = seeded["admin_name"]
        notice_id = notice.id

    client = _login(app, admin_name)
    html = client.get("/admin/statutory-deadline").get_data(as_text=True)
    assert "待管理员审核" in html
    assert "管理员审核中" in html
    assert f"/admin/statutory-deadline/{notice_id}/urge" not in html


def test_admin_can_urge_process_and_writer_with_cooldown():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        fee = _add_notice(
            case_id=seeded["case_id"],
            uploader_id=seeded["process_id"],
            suffix=f"{suffix}fee",
            notice_type=OfficialNotice.TYPE_FEE,
            due_days=1,
        )
        oa = _add_notice(
            case_id=seeded["case_id"],
            uploader_id=seeded["process_id"],
            suffix=f"{suffix}oa",
            notice_type=OfficialNotice.TYPE_OA1,
            due_days=1,
            forwarded_to_id=seeded["writer_id"],
            forwarded=True,
        )
        admin_name = seeded["admin_name"]
        process_id = seeded["process_id"]
        writer_id = seeded["writer_id"]
        writer_name = seeded["writer_name"]
        process_name = seeded["process_name"]
        fee_id = fee.id
        oa_id = oa.id
        case_id = seeded["case_id"]

    client = _login(app, admin_name)
    fee_urge = client.post(f"/admin/statutory-deadline/{fee_id}/urge", follow_redirects=False)
    assert fee_urge.status_code == 302
    assert "已催办流程人员" in _toast(fee_urge)

    again = client.post(f"/admin/statutory-deadline/{fee_id}/urge", follow_redirects=False)
    assert "请稍后再试" in _toast(again)

    writer_urge = client.post(f"/admin/statutory-deadline/{oa_id}/urge", follow_redirects=False)
    assert "已催办撰写师" in _toast(writer_urge)

    with app.app_context():
        fee_logs = CaseReviewLog.query.filter_by(
            case_id=case_id, action="admin_urge", recipient_id=process_id
        ).all()
        writer_logs = CaseReviewLog.query.filter_by(
            case_id=case_id, action="admin_urge", recipient_id=writer_id
        ).all()
        assert len(fee_logs) == 1
        assert len(writer_logs) == 1
        assert "待官方缴费" in (fee_logs[0].note or "")
        assert "撰写师未接收" in (writer_logs[0].note or "")

    writer_client = _login(app, writer_name)
    inbox = writer_client.get("/staff/notifications").get_data(as_text=True)
    assert "管理员催办官方期限" in inbox
    assert "请尽快办理" in inbox

    process_client = _login(app, process_name)
    process_inbox = process_client.get("/staff/notifications").get_data(as_text=True)
    assert "管理员催办官方期限" in process_inbox
    assert "期限催办" in process_inbox


def test_staff_cannot_open_statutory_deadline():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        writer_name = seeded["writer_name"]

    client = _login(app, writer_name)
    assert client.get("/admin/statutory-deadline").status_code == 403
    assert client.post("/admin/statutory-deadline/1/urge").status_code == 403
