"""跨端收件箱：其他端提交后，管理/员工/客户端 live-status 的 cursor 必须立刻变化。"""

from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import Case, Customer, CustomerKind, Project, User


def _seed(suffix: str):
    admin = User(username=f"_live_admin_{suffix}", role="admin")
    admin.set_password("secret")
    writer = User(
        username=f"_live_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    writer.set_password("secret")
    process = User(
        username=f"_live_proc_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    process.set_password("secret")
    business = User(
        username=f"_live_biz_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_BUSINESS,
    )
    business.set_password("secret")
    customer = Customer(kind=CustomerKind.COMPANY, name=f"_live_cust_{suffix}")
    db.session.add_all([admin, writer, process, business, customer])
    db.session.flush()
    client = User(
        username=f"_live_client_{suffix}",
        role="client",
        customer_id=customer.id,
    )
    client.set_password("secret")
    db.session.add(client)
    project = Project(customer_id=customer.id, name=f"_live_proj_{suffix}")
    db.session.add(project)
    db.session.commit()
    return {
        "admin_name": admin.username,
        "writer_name": writer.username,
        "writer_id": writer.id,
        "process_name": process.username,
        "process_id": process.id,
        "business_name": business.username,
        "client_name": client.username,
        "customer_id": customer.id,
        "project_id": project.id,
    }


def test_other_port_submit_updates_admin_live_status_without_reload():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix)

    admin = app.test_client()
    admin.post("/auth/login", data={"username": seeded["admin_name"], "password": "secret"})
    dashboard = admin.get("/admin/dashboard")
    assert dashboard.status_code == 200
    assert b'data-qy-live-status' in dashboard.data
    assert b"/admin/live-status" in dashboard.data

    before = admin.get("/admin/live-status").get_json()
    assert before["ok"] is True
    assert "cursor" in before
    assert "pending_order_intake" in before
    intake_before = before["pending_order_intake"]
    cursor_before = before["cursor"]

    business = app.test_client()
    business.post("/auth/login", data={"username": seeded["business_name"], "password": "secret"})
    submitted = business.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": str(seeded["customer_id"]),
            "project_id": str(seeded["project_id"]),
            "title": f"_live_order_{suffix}",
            "case_type_code": "utility_utility_model",
        },
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)

    after = admin.get("/admin/live-status").get_json()
    assert after["cursor"] != cursor_before
    assert after["pending_order_intake"] == intake_before + 1

    writer = app.test_client()
    writer.post("/auth/login", data={"username": seeded["writer_name"], "password": "secret"})
    assert writer.get("/admin/live-status").status_code == 403
    assert admin.get("/staff/live-status").status_code == 403


def test_admin_action_updates_staff_and_client_live_status():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix)

    business = app.test_client()
    business.post("/auth/login", data={"username": seeded["business_name"], "password": "secret"})
    submitted = business.post(
        "/staff/business-orders/cases",
        data={
            "customer_id": str(seeded["customer_id"]),
            "project_id": str(seeded["project_id"]),
            "title": f"_live_assign_{suffix}",
            "case_type_code": "utility_utility_model",
        },
        follow_redirects=False,
    )
    assert submitted.status_code in (302, 303)

    with app.app_context():
        case_id = Case.query.filter_by(title=f"_live_assign_{suffix}").one().id

    writer = app.test_client()
    writer.post("/auth/login", data={"username": seeded["writer_name"], "password": "secret"})
    writer_home = writer.get("/staff/dashboard")
    assert writer_home.status_code == 200
    assert b"/staff/live-status" in writer_home.data
    writer_before = writer.get("/staff/live-status").get_json()
    assert writer_before["ok"] is True
    unread_before = writer_before["unread"]
    cursor_before = writer_before["cursor"]

    process = app.test_client()
    process.post(
        "/auth/login",
        data={"username": seeded["process_name"], "password": "secret"},
    )
    process_before = process.get("/staff/live-status").get_json()

    business_before = business.get("/staff/live-status").get_json()

    client = app.test_client()
    client.post("/auth/login", data={"username": seeded["client_name"], "password": "secret"})
    client_home = client.get("/client/dashboard")
    assert client_home.status_code == 200
    assert b"/client/live-status" in client_home.data
    client_before = client.get("/client/live-status").get_json()
    assert client_before["ok"] is True
    client_cursor_before = client_before["cursor"]

    admin = app.test_client()
    admin.post("/auth/login", data={"username": seeded["admin_name"], "password": "secret"})
    approved = admin.post(
        f"/admin/order-intake/{case_id}/action",
        data={
            "review_action": "approve",
            "writer_id": str(seeded["writer_id"]),
            "process_owner_id": str(seeded["process_id"]),
        },
        follow_redirects=False,
    )
    assert approved.status_code in (302, 303)

    writer_after = writer.get("/staff/live-status").get_json()
    assert writer_after["unread"] == unread_before + 1
    assert writer_after["cursor"] != cursor_before

    process_after = process.get("/staff/live-status").get_json()
    assert process_after["unread"] == process_before["unread"] + 1
    assert process_after["cursor"] != process_before["cursor"]

    business_after = business.get("/staff/live-status").get_json()
    assert business_after["unread"] == business_before["unread"] + 1
    assert business_after["cursor"] != business_before["cursor"]

    client_after = client.get("/client/live-status").get_json()
    assert client_after["cursor"] != client_cursor_before
    assert admin.get("/client/live-status").status_code == 403
