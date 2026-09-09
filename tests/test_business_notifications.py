"""业务人员消息中心：侧栏入口、下单确认/打回、筛选与越权。"""

from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Case, CaseReviewLog, Customer, CustomerKind, Project, User


def _seed(*, suffix: str):
    admin = User(username=f"_bn_admin_{suffix}", role="admin")
    admin.set_password("secret")
    writer = User(
        username=f"_bn_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    writer.set_password("secret")
    business = User(
        username=f"_bn_biz_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_BUSINESS,
    )
    business.set_password("secret")
    other_business = User(
        username=f"_bn_biz2_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_BUSINESS,
    )
    other_business.set_password("secret")
    process = User(
        username=f"_bn_proc_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    process.set_password("secret")
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_bn_customer_{suffix}")
    db.session.add_all([admin, writer, business, other_business, process, customer])
    db.session.flush()
    project = Project(customer_id=customer.id, name=f"_bn_project_{suffix}")
    db.session.add(project)
    db.session.commit()
    return {
        "admin_name": admin.username,
        "writer_name": writer.username,
        "business_name": business.username,
        "other_business_name": other_business.username,
        "process_name": process.username,
        "customer_id": customer.id,
        "project_id": project.id,
        "business_id": business.id,
    }


def _login(app, username: str):
    client = app.test_client()
    client.post("/auth/login", data={"username": username, "password": "secret"})
    return client


def _submit_order(client, *, customer_id: int, project_id: int, title: str) -> int:
    submitted = client.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": str(customer_id),
            "project_id": str(project_id),
            "title": title,
            "case_type_code": "utility_utility_model",
        },
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)
    case = Case.query.filter_by(title=title).one()
    return case.id


def _admin_review(client, case_id: int, *, action: str, note: str = ""):
    data = {"review_action": action}
    if note:
        data["reject_note"] = note
    reviewed = client.post(
        f"/admin/order-intake/{case_id}/action",
        data=data,
        follow_redirects=False,
    )
    assert reviewed.status_code in (302, 303)


def test_business_sidebar_opens_empty_message_center():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)

    business = _login(app, seeded["business_name"])
    home = business.get("/staff/business-dashboard")
    assert home.status_code == 200
    home_text = home.data.decode("utf-8")
    assert "消息中心" in home_text
    assert 'data-spa-endpoint="staff.notifications"' in home_text
    assert 'id="qyStaffNotificationBadge"' in home_text
    assert "去案件列表" not in home_text
    assert "任务看板" not in home_text

    page = business.get("/staff/notifications")
    assert page.status_code == 200
    text = page.data.decode("utf-8")
    assert "消息中心" in text
    assert "下单确认结果会显示在这里" in text
    assert "去下单" in text
    assert "去案件列表" not in text
    assert "去任务看板" not in text
    assert "下单通过" in text
    assert "下单打回" in text
    assert "案件分配" not in text
    assert "审核通过" not in text
    assert business.get("/staff/notifications/status").get_json() == {"ok": True, "unread": 0}

    spa = business.get("/staff/notifications", headers={"X-Qy-Spa": "1"})
    assert spa.status_code == 200
    assert b'id="qy-spa-main"' in spa.data
    assert b'data-spa-endpoint="staff.notifications"' in spa.data
    assert "消息中心 — 琴岳专利管理系统".encode() in spa.data


def test_process_cannot_open_message_center():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)

    process = _login(app, seeded["process_name"])
    home = process.get("/staff/process-dashboard")
    assert home.status_code == 200
    assert "消息中心".encode() not in home.data
    assert b'data-spa-endpoint="staff.notifications"' not in home.data
    for path in (
        "/staff/notifications",
        "/staff/notifications/status",
        "/staff/notifications/1/read",
    ):
        assert process.get(path).status_code == 403
    assert process.post("/staff/notifications/read-all").status_code == 403


def test_intake_approve_and_reject_filters_and_read_paths():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        customer_id = seeded["customer_id"]
        project_id = seeded["project_id"]
        admin_name = seeded["admin_name"]
        business_name = seeded["business_name"]
        other_business_name = seeded["other_business_name"]
        writer_name = seeded["writer_name"]
        process_name = seeded["process_name"]
        business_id = seeded["business_id"]

    business = _login(app, business_name)
    approve_title = f"_bn_ok_{suffix}"
    reject_title = f"_bn_back_{suffix}"
    with app.app_context():
        approve_id = _submit_order(
            business, customer_id=customer_id, project_id=project_id, title=approve_title
        )
        reject_id = _submit_order(
            business, customer_id=customer_id, project_id=project_id, title=reject_title
        )

    admin = _login(app, admin_name)
    _admin_review(admin, approve_id, action="approve")
    _admin_review(admin, reject_id, action="reject", note="材料清单不完整")

    with app.app_context():
        approve_log = CaseReviewLog.query.filter_by(
            case_id=approve_id, action="intake_approve", recipient_id=business_id
        ).one()
        reject_log = CaseReviewLog.query.filter_by(
            case_id=reject_id, action="intake_reject", recipient_id=business_id
        ).one()
        approve_log_id = approve_log.id
        reject_log_id = reject_log.id
        assert approve_log.read_at is None
        assert reject_log.read_at is None
        assert reject_log.note == "材料清单不完整"

    status = business.get("/staff/notifications/status").get_json()
    assert status == {"ok": True, "unread": 2}

    home = business.get("/staff/business-dashboard")
    assert home.status_code == 200
    assert b'id="qyStaffNotificationBadge"' in home.data
    assert b">2</span>" in home.data

    inbox = business.get("/staff/notifications")
    assert inbox.status_code == 200
    inbox_text = inbox.data.decode("utf-8")
    assert approve_title in inbox_text
    assert reject_title in inbox_text
    assert "下单已确认" in inbox_text
    assert "下单被打回" in inbox_text
    assert "管理员已确认您提交的下单，案件进入待分配。" in inbox_text
    assert "材料清单不完整" in inbox_text
    assert "去下单" in inbox_text
    assert "去修改" in inbox_text
    assert "立即修改" not in inbox_text
    assert "查看案件" not in inbox_text
    assert "案件分配" not in inbox_text

    approved_only = business.get("/staff/notifications?type=intake_approve")
    approved_text = approved_only.data.decode("utf-8")
    assert approved_only.status_code == 200
    assert approve_title in approved_text
    assert "下单已确认" in approved_text
    assert reject_title not in approved_text

    rejected_only = business.get("/staff/notifications?type=intake_reject")
    rejected_text = rejected_only.data.decode("utf-8")
    assert rejected_only.status_code == 200
    assert reject_title in rejected_text
    assert "下单被打回" in rejected_text
    assert approve_title not in rejected_text
    assert "下单已确认" not in rejected_text

    actionable = business.get("/staff/notifications?status=actionable")
    actionable_text = actionable.data.decode("utf-8")
    assert actionable.status_code == 200
    assert reject_title in actionable_text
    assert approve_title not in actionable_text
    assert "下单已确认" not in actionable_text

    writer = _login(app, writer_name)
    writer_page = writer.get("/staff/notifications")
    assert writer_page.status_code == 200
    writer_text = writer_page.data.decode("utf-8")
    assert approve_title not in writer_text
    assert reject_title not in writer_text
    assert "下单通过" not in writer_text
    assert "下单打回" not in writer_text
    assert "案件分配" in writer_text
    assert writer.get("/staff/notifications/status").get_json()["unread"] == 0

    process = _login(app, process_name)
    assert process.get("/staff/notifications").status_code == 403
    assert process.get(f"/staff/notifications/{reject_log_id}/read").status_code == 403

    other = _login(app, other_business_name)
    assert other.get("/staff/notifications/status").get_json()["unread"] == 0
    assert other.get(f"/staff/notifications/{reject_log_id}/read").status_code == 404
    other_page = other.get("/staff/notifications")
    assert approve_title.encode() not in other_page.data
    assert reject_title.encode() not in other_page.data

    opened_reject = business.get(
        f"/staff/notifications/{reject_log_id}/read", follow_redirects=False
    )
    assert opened_reject.status_code in (302, 303)
    assert "/staff/business-orders" in opened_reject.headers["Location"]
    assert f"edit={reject_id}" in opened_reject.headers["Location"]
    assert "/staff/case-detail" not in opened_reject.headers["Location"]
    assert business.get("/staff/notifications/status").get_json()["unread"] == 1

    opened_approve = business.get(
        f"/staff/notifications/{approve_log_id}/read", follow_redirects=False
    )
    assert opened_approve.status_code in (302, 303)
    assert "/staff/business-orders" in opened_approve.headers["Location"]
    assert f"edit={approve_id}" not in opened_approve.headers["Location"]
    assert business.get("/staff/notifications/status").get_json()["unread"] == 0

    with app.app_context():
        assert db.session.get(CaseReviewLog, reject_log_id).read_at is not None
        assert db.session.get(CaseReviewLog, approve_log_id).read_at is not None


def test_business_read_all_clears_unread_badge():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)
        customer_id = seeded["customer_id"]
        project_id = seeded["project_id"]
        admin_name = seeded["admin_name"]
        business_name = seeded["business_name"]

    business = _login(app, business_name)
    title = f"_bn_readall_{suffix}"
    with app.app_context():
        case_id = _submit_order(
            business, customer_id=customer_id, project_id=project_id, title=title
        )

    admin = _login(app, admin_name)
    _admin_review(admin, case_id, action="reject", note="请核对申请人名称")

    assert business.get("/staff/notifications/status").get_json()["unread"] == 1
    marked = business.post(
        "/staff/notifications/read-all",
        data={"status": "all", "type": "all"},
        follow_redirects=False,
    )
    assert marked.status_code in (302, 303)
    assert "/staff/notifications" in marked.headers["Location"]
    assert business.get("/staff/notifications/status").get_json()["unread"] == 0

    unread = business.get("/staff/notifications?status=unread")
    unread_text = unread.data.decode("utf-8")
    assert unread.status_code == 200
    assert "所有消息都已查看" in unread_text
    assert title not in unread_text

    remaining = business.get("/staff/notifications")
    assert title.encode() in remaining.data
    assert "下单被打回".encode() in remaining.data
