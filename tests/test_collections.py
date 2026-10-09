"""收账：官方缴费归流程，不通知业务；向客户收款归业务。撰写师不可见证明。"""

from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

from app import create_app
from app.assignment_advisor import pending_billing_assignment_cases
from app.case_trace import stamp_assignee, stamp_billing_owner, stamp_process_owner
from app.extensions import db
from app.models import (
    Case,
    CaseCollection,
    CaseCollectionProof,
    Customer,
    CustomerKind,
    OfficialNotice,
    Project,
    Task,
    User,
)
from app.workflow import TaskPhase


def _make_user(*, username: str, role: str, password: str = "secret", **kwargs) -> User:
    user = User(username=username, role=role, **kwargs)
    user.set_password(password)
    db.session.add(user)
    return user


def _seed(*, suffix: str, phase: str = TaskPhase.OFFICE_ACTION, with_billing: bool = False):
    admin = _make_user(username=f"_col_admin_{suffix}", role="admin")
    writer = _make_user(
        username=f"_col_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    process = _make_user(
        username=f"_col_proc_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    business = _make_user(
        username=f"_col_biz_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_BUSINESS,
    )
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_col_cust_{suffix}")
    db.session.add(customer)
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_col_proj_{suffix}")
    db.session.add(project)
    db.session.flush()
    case = Case(
        project_id=project.id,
        title=f"_col_case_{suffix}",
        application_no=f"CL1{suffix}",
        case_type_code="invention_nonrisk_software",
    )
    stamp_process_owner(case, process)
    if with_billing:
        stamp_billing_owner(case, business)
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
        "business_name": business.username,
        "business_id": business.id,
        "case_id": case.id,
        "task_id": task.id,
        "title": case.title,
    }


def _login(app, username: str):
    client = app.test_client()
    client.post("/auth/login", data={"username": username, "password": "secret"})
    return client


def _toast(response) -> str:
    return unquote(response.headers.get("Location", ""))


def _upload_fee(client, case_id: int, filename: str = "缴费.pdf"):
    return client.post(
        f"/staff/process-cases/{case_id}",
        data={
            "form_action": "upload_notice",
            "notice_type": "fee",
            "official_due_at": "2099-12-31",
            "notice_file": (BytesIO(b"%PDF-1.4 fee-notice"), filename),
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


def _notify(client, case_id: int, notice_id: int):
    return client.post(
        f"/staff/process-cases/{case_id}",
        data={
            "form_action": "notify_collection",
            "notice_id": str(notice_id),
        },
        follow_redirects=False,
    )


def _mark_paid(client, case_id: int, notice_id: int, *, with_proof: bool = True):
    data = {
        "form_action": "mark_fee_paid",
        "notice_id": str(notice_id),
    }
    if with_proof:
        data["fee_proof_file"] = (BytesIO(b"%PDF-1.4 fee-proof"), "缴费回单.pdf")
        return client.post(
            f"/staff/process-cases/{case_id}",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=False,
        )
    return client.post(
        f"/staff/process-cases/{case_id}",
        data=data,
        follow_redirects=False,
    )


def test_fee_notice_does_not_notify_or_require_business():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, with_billing=False)
        case_id = seeded["case_id"]

    process = _login(app, seeded["process_name"])
    uploaded = _upload_fee(process, case_id)
    assert uploaded.status_code in (302, 303)
    page = process.get(f"/staff/process-cases/{case_id}")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "缴费确认" in html
    assert "标记已缴费" in html
    assert 'name="fee_proof_file"' in html
    assert "必须上传向专利局缴费的证明" in html
    assert "还没有缴费通知" not in html
    assert "收账确认" not in html
    assert "通知业务收账" not in html
    assert "未通知业务收账" not in html
    assert "转交业务收账" not in html

    with app.app_context():
        from app.official_notices import QUEUE_PENDING_FEE, QUEUE_PENDING_FORWARD, process_queue_counts

        notice = OfficialNotice.query.filter_by(case_id=case_id).one()
        notice_id = notice.id
        assert notice.needs_billing is True
        counts = process_queue_counts(seeded["process_id"])
        assert counts[QUEUE_PENDING_FEE] == 1
        assert counts[QUEUE_PENDING_FORWARD] == 0

    blocked_forward = _forward(process, case_id, notice_id)
    assert blocked_forward.status_code in (302, 303)
    assert "标记已缴费" in _toast(blocked_forward)
    blocked_notify = _notify(process, case_id, notice_id)
    assert blocked_notify.status_code in (302, 303)
    assert "不需要通知业务收账" in _toast(blocked_notify)
    with app.app_context():
        notice = db.session.get(OfficialNotice, notice_id)
        assert notice.forwarded_at is None
        assert notice.fee_paid_at is None
        assert CaseCollection.query.filter_by(case_id=case_id).count() == 0

    final = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert final.status_code in (302, 303)
    assert "未完成官方缴费" in _toast(final)

    missing = _mark_paid(process, case_id, notice_id, with_proof=False)
    assert missing.status_code in (302, 303)
    assert "请上传官方缴费证明" in _toast(missing)
    with app.app_context():
        notice = db.session.get(OfficialNotice, notice_id)
        assert notice.fee_paid_at is None
        assert notice.fee_paid_complete is False

    paid = _mark_paid(process, case_id, notice_id)
    assert paid.status_code in (302, 303)
    assert "官方缴费完成" in _toast(paid)
    again = _mark_paid(process, case_id, notice_id)
    assert again.status_code in (302, 303)
    assert "已经标记过已缴费" in _toast(again)
    done_page = process.get(f"/staff/process-cases/{case_id}")
    assert done_page.status_code == 200
    done_html = done_page.data.decode("utf-8")
    assert "缴费回单.pdf" in done_html
    assert 'name="fee_proof_file"' not in done_html
    assert 'value="mark_fee_paid"' not in done_html
    done = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert done.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, seeded["task_id"]).phase_status == TaskPhase.PENDING_FINAL_REVIEW
        notice = db.session.get(OfficialNotice, notice_id)
        assert notice.fee_paid_complete is True
        assert notice.fee_paid_proof_original_name == "缴费回单.pdf"
        assert CaseCollection.query.filter_by(case_id=case_id).count() == 0


def test_fee_notice_stays_with_process_not_business_or_writer():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, with_billing=True)
        case_id = seeded["case_id"]
        writer_id = seeded["writer_id"]

    admin = _login(app, seeded["admin_name"])
    process = _login(app, seeded["process_name"])
    writer = _login(app, seeded["writer_name"])
    business = _login(app, seeded["business_name"])

    assign_page = admin.get("/admin/billing-assignment")
    assert assign_page.status_code == 200
    assign_text = assign_page.data.decode("utf-8")
    assert seeded["business_name"] in assign_text or "指定收账" in assign_text
    assert seeded["writer_name"] not in assign_text or "收账人员" in assign_text

    assert _upload_fee(process, case_id, filename="年费.pdf").status_code in (302, 303)
    with app.app_context():
        notice_id = OfficialNotice.query.filter_by(case_id=case_id).one().id

    paid = _mark_paid(process, case_id, notice_id)
    assert paid.status_code in (302, 303)
    with app.app_context():
        notice = db.session.get(OfficialNotice, notice_id)
        assert notice.forwarded_to_id is None
        assert notice.fee_paid_complete is True
        assert CaseCollection.query.filter_by(case_id=case_id).count() == 0

    writer_detail = writer.get(f"/staff/case-detail/{case_id}").data.decode("utf-8")
    assert "年费.pdf" not in writer_detail
    assert writer.get(f"/staff/official-notices/{notice_id}").status_code == 404
    assert business.get(f"/staff/official-notices/{notice_id}").status_code == 404
    assert process.get(f"/staff/official-notices/{notice_id}").status_code == 200
    assert writer.get(f"/staff/official-notices/{notice_id}/fee-proof").status_code == 404
    assert business.get(f"/staff/official-notices/{notice_id}/fee-proof").status_code == 404
    assert process.get(f"/staff/official-notices/{notice_id}/fee-proof").status_code == 200
    assert admin.get(f"/admin/official-notices/{notice_id}/fee-proof").status_code == 200
    admin_detail = admin.get(f"/admin/case-detail/{case_id}").data.decode("utf-8")
    assert "缴费回单.pdf" in admin_detail

    biz_page = business.get(f"/staff/business-collections/{case_id}")
    assert biz_page.status_code == 200
    assert "年费.pdf" not in biz_page.data.decode("utf-8")

    final = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert final.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, seeded["task_id"]).phase_status == TaskPhase.PENDING_FINAL_REVIEW
        assert writer_id == seeded["writer_id"]


def test_legacy_paid_without_proof_stays_incomplete():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, with_billing=False)
        case_id = seeded["case_id"]

    process = _login(app, seeded["process_name"])
    assert _upload_fee(process, case_id).status_code in (302, 303)
    with app.app_context():
        notice = OfficialNotice.query.filter_by(case_id=case_id).one()
        notice_id = notice.id
        notice.fee_paid_at = datetime.now(timezone.utc)
        db.session.commit()

    page = process.get(f"/staff/process-cases/{case_id}")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert "请补交证明" in html
    assert "补交缴费证明" in html
    assert 'name="fee_proof_file"' in html

    blocked = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert "请先上传缴费证明" in _toast(blocked)
    with app.app_context():
        from app.official_notices import QUEUE_PENDING_FEE, process_queue_counts

        assert process_queue_counts(seeded["process_id"])[QUEUE_PENDING_FEE] == 1

    paid = _mark_paid(process, case_id, notice_id)
    assert paid.status_code in (302, 303)
    assert "官方缴费完成" in _toast(paid)
    with app.app_context():
        notice = db.session.get(OfficialNotice, notice_id)
        assert notice.fee_paid_complete is True
        assert process_queue_counts(seeded["process_id"])[QUEUE_PENDING_FEE] == 0

    final = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert final.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, seeded["task_id"]).phase_status == TaskPhase.PENDING_FINAL_REVIEW


def test_writer_cannot_see_collection_proofs():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, with_billing=True)
        case_id = seeded["case_id"]
        collection = CaseCollection(case_id=case_id, status=CaseCollection.STATUS_PENDING_PROOF)
        db.session.add(collection)
        db.session.commit()
        collection_id = collection.id

    admin = _login(app, seeded["admin_name"])
    process = _login(app, seeded["process_name"])
    writer = _login(app, seeded["writer_name"])
    business = _login(app, seeded["business_name"])

    uploaded_proof = business.post(
        f"/staff/business-collections/{case_id}",
        data={
            "form_action": "upload_proof",
            "collection_id": str(collection_id),
            "proof_file": (BytesIO(b"%PDF-1.4 payment-proof"), "回单.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert uploaded_proof.status_code in (302, 303)
    with app.app_context():
        proof = CaseCollectionProof.query.filter_by(collection_id=collection_id).one()
        proof_id = proof.id

    assert writer.get(f"/staff/collection-proofs/{proof_id}").status_code == 404
    assert business.get(f"/staff/collection-proofs/{proof_id}").status_code == 200
    assert process.get(f"/staff/collection-proofs/{proof_id}").status_code == 200
    assert admin.get(f"/admin/collection-proofs/{proof_id}").status_code == 200

    submitted = business.post(
        f"/staff/business-collections/{case_id}",
        data={
            "form_action": "submit_collection",
            "collection_id": str(collection_id),
            "amount": "1200.50",
            "note": "客户已付",
        },
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)
    blocked_final = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert "未确认的收账" in _toast(blocked_final)
    confirmed = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "confirm_collection", "collection_id": str(collection_id)},
        follow_redirects=False,
    )
    assert confirmed.status_code in (302, 303)
    admin_detail = admin.get(f"/admin/case-detail/{case_id}").data.decode("utf-8")
    assert "回单.pdf" in admin_detail
    assert "收账记录" in admin_detail
    final = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert final.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, seeded["task_id"]).phase_status == TaskPhase.PENDING_FINAL_REVIEW


def test_multiple_fee_notices_all_must_be_paid():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, with_billing=True)
        case_id = seeded["case_id"]

    process = _login(app, seeded["process_name"])

    assert _upload_fee(process, case_id, filename="年费1.pdf").status_code in (302, 303)
    assert _upload_fee(process, case_id, filename="年费2.pdf").status_code in (302, 303)
    with app.app_context():
        notices = (
            OfficialNotice.query.filter_by(case_id=case_id)
            .order_by(OfficialNotice.id.asc())
            .all()
        )
        notice_ids = [item.id for item in notices]
        assert len(notice_ids) == 2

    assert _mark_paid(process, case_id, notice_ids[0]).status_code in (302, 303)
    still_blocked = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert "未完成官方缴费" in _toast(still_blocked)

    assert _mark_paid(process, case_id, notice_ids[1]).status_code in (302, 303)
    final = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert final.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, seeded["task_id"]).phase_status == TaskPhase.PENDING_FINAL_REVIEW
        assert CaseCollection.query.filter_by(case_id=case_id).count() == 0


def test_pending_billing_pool_includes_unassigned_without_fee_notice():
    """在办案件没填收账人员时，侧栏与指定页就要提示，不必先有缴费通知。"""
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        open_seeded = _seed(suffix=f"{suffix}o", with_billing=False)
        billed = _seed(suffix=f"{suffix}b", with_billing=True)
        intake = _seed(
            suffix=f"{suffix}i",
            phase=TaskPhase.PENDING_ORDER_REVIEW,
            with_billing=False,
        )
        done = _seed(suffix=f"{suffix}d", phase=TaskPhase.COMPLETED, with_billing=False)
        departed_seeded = _seed(suffix=f"{suffix}l", with_billing=True)
        departed_user = db.session.get(User, departed_seeded["business_id"])
        departed_user.is_active = False
        db.session.commit()
        ids = [case.id for case in pending_billing_assignment_cases()]
        assert open_seeded["case_id"] in ids
        assert billed["case_id"] not in ids
        assert intake["case_id"] not in ids
        assert done["case_id"] not in ids
        assert departed_seeded["case_id"] in ids
        assert ids.index(departed_seeded["case_id"]) < ids.index(open_seeded["case_id"])
        admin_name = open_seeded["admin_name"]
        title = open_seeded["title"]

    admin = _login(app, admin_name)
    page = admin.get("/admin/billing-assignment")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert title in html
    assert "暂无待指定收账的案件" not in html
    status = admin.get("/admin/assignment-nav/status").get_json()
    assert status["ok"] is True
    assert status["pending_billing"] >= 1
    dashboard = admin.get("/admin/dashboard")
    assert dashboard.status_code == 200
    assert f'待指定收账 {status["pending_billing"]} 件'.encode() in dashboard.data


def test_billing_assignment_rejects_writer_and_lists_business_only():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, with_billing=False)
        case_id = seeded["case_id"]
        writer_id = seeded["writer_id"]
        business_id = seeded["business_id"]

    process = _login(app, seeded["process_name"])
    admin = _login(app, seeded["admin_name"])
    assert _upload_fee(process, case_id).status_code in (302, 303)

    page = admin.get(f"/admin/billing-assignment?case_id={case_id}")
    assert page.status_code == 200
    html = page.data.decode("utf-8")
    assert seeded["business_name"] in html
    assert f'value="{writer_id}"' not in html
    assert f'value="{business_id}"' in html
    assert html.count("data-billing-assignment-badge") == 1
    pending_before = admin.get("/admin/assignment-nav/status").get_json()
    assert pending_before["ok"] is True
    assert pending_before["pending_billing"] >= 1

    rejected = admin.post(
        "/admin/billing-assignment/assign",
        data={"case_id": str(case_id), "billing_owner_id": str(writer_id)},
        follow_redirects=False,
    )
    assert rejected.status_code in (302, 303)
    assert "仅在职业务人员" in _toast(rejected)

    assigned = admin.post(
        "/admin/billing-assignment/assign",
        data={"case_id": str(case_id), "billing_owner_id": str(business_id)},
        follow_redirects=False,
    )
    assert assigned.status_code in (302, 303)
    with app.app_context():
        case = db.session.get(Case, case_id)
        assert case.billing_owner_id == business_id
    after = admin.get("/admin/assignment-nav/status").get_json()
    assert after["pending_billing"] == pending_before["pending_billing"] - 1
