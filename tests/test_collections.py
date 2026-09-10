"""收账：未指定禁止转交缴费通知；撰写师不可见证明；全部确认才能终审。"""

from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

from app import create_app
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


def test_fee_notice_cannot_forward_without_billing_owner():
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
    assert "转交业务收账".encode("utf-8") in page.data
    assert "尚未指定收账人员".encode("utf-8") in page.data

    with app.app_context():
        notice = OfficialNotice.query.filter_by(case_id=case_id).one()
        notice_id = notice.id
        assert notice.needs_billing is True

    blocked = _forward(process, case_id, notice_id)
    assert blocked.status_code in (302, 303)
    assert "请先让管理员指定收账负责人" in _toast(blocked)
    with app.app_context():
        notice = db.session.get(OfficialNotice, notice_id)
        assert notice.forwarded_at is None
        assert CaseCollection.query.filter_by(case_id=case_id).count() == 0

    final = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert final.status_code in (302, 303)
    assert "未转交的缴费通知" in _toast(final)
    with app.app_context():
        assert db.session.get(Task, seeded["task_id"]).phase_status == TaskPhase.OFFICE_ACTION


def test_fee_notice_goes_to_business_and_writer_cannot_see_proofs():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, with_billing=True)
        case_id = seeded["case_id"]
        business_id = seeded["business_id"]
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

    forwarded = _forward(process, case_id, notice_id)
    assert forwarded.status_code in (302, 303)
    assert "收账人员" in _toast(forwarded)
    with app.app_context():
        notice = db.session.get(OfficialNotice, notice_id)
        assert notice.forwarded_to_id == business_id
        collection = CaseCollection.query.filter_by(case_id=case_id).one()
        collection_id = collection.id
        assert collection.status == CaseCollection.STATUS_PENDING_PROOF

    writer_detail = writer.get(f"/staff/case-detail/{case_id}").data.decode("utf-8")
    assert "年费.pdf" not in writer_detail
    assert writer.get(f"/staff/official-notices/{notice_id}").status_code == 404

    biz_page = business.get(f"/staff/business-collections/{case_id}")
    assert biz_page.status_code == 200
    biz_text = biz_page.data.decode("utf-8")
    assert "年费.pdf" in biz_text
    assert "收款证明" in biz_text

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
            "note": "已缴年费",
        },
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(CaseCollection, collection_id).status == CaseCollection.STATUS_PENDING_CONFIRM

    blocked_final = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert "未确认的收账" in _toast(blocked_final)

    confirmed = process.post(
        f"/staff/process-cases/{case_id}",
        data={
            "form_action": "confirm_collection",
            "collection_id": str(collection_id),
        },
        follow_redirects=False,
    )
    assert confirmed.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(CaseCollection, collection_id).status == CaseCollection.STATUS_CONFIRMED

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
        assert writer_id == seeded["writer_id"]


def test_multiple_fee_notices_all_must_be_confirmed():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix, with_billing=True)
        case_id = seeded["case_id"]

    process = _login(app, seeded["process_name"])
    business = _login(app, seeded["business_name"])

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

    for notice_id in notice_ids:
        assert _forward(process, case_id, notice_id).status_code in (302, 303)

    with app.app_context():
        collections = (
            CaseCollection.query.filter_by(case_id=case_id)
            .order_by(CaseCollection.id.asc())
            .all()
        )
        collection_ids = [item.id for item in collections]
        assert len(collection_ids) == 2

    for collection_id in collection_ids:
        assert business.post(
            f"/staff/business-collections/{case_id}",
            data={
                "form_action": "upload_proof",
                "collection_id": str(collection_id),
                "proof_file": (BytesIO(b"%PDF-1.4 proof"), f"proof_{collection_id}.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        ).status_code in (302, 303)
        assert business.post(
            f"/staff/business-collections/{case_id}",
            data={
                "form_action": "submit_collection",
                "collection_id": str(collection_id),
            },
            follow_redirects=False,
        ).status_code in (302, 303)

    assert process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "confirm_collection", "collection_id": str(collection_ids[0])},
        follow_redirects=False,
    ).status_code in (302, 303)
    still_blocked = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert "未确认的收账" in _toast(still_blocked)

    assert process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "confirm_collection", "collection_id": str(collection_ids[1])},
        follow_redirects=False,
    ).status_code in (302, 303)
    final = process.post(
        f"/staff/process-cases/{case_id}",
        data={"form_action": "submit_final_review"},
        follow_redirects=False,
    )
    assert final.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Task, seeded["task_id"]).phase_status == TaskPhase.PENDING_FINAL_REVIEW


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
